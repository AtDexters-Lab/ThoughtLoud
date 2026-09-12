"""A loopback llama-server whose lifetime belongs to one desktop app instance."""

from __future__ import annotations

import base64
import io
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import requests

from .models import DownloadCancelled, ModelStore, MTP_ASSISTANT


class RuntimeStartupError(RuntimeError):
    """Managed inference is unavailable; includes a next step for the user."""


def validate_managed_host() -> None:
    """CPU instruction features are selected by the bundled runtime's dispatcher."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeStartupError(
            "The bundled runtime supports Linux x86_64. Use a separately configured "
            "inference endpoint on this platform."
        )


def find_runtime_dir(runtime_dir=None) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir)
    if os.environ.get("VOXD_LLAMA_RUNTIME"):
        return Path(os.environ["VOXD_LLAMA_RUNTIME"])
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "llama"
    raise RuntimeStartupError(
        "The bundled inference runtime is missing. Install the complete desktop "
        "package, or set VOXD_LLAMA_RUNTIME to your built llama-server directory."
    )


def _audio_probe_payload() -> dict:
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return {
        "model": "gemma-e4b",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Transcribe the audio. Output nothing for silence."},
            {"type": "input_audio", "input_audio": {
                "data": base64.b64encode(audio.getvalue()).decode("ascii"), "format": "wav",
            }},
        ]}],
        "stream": False, "temperature": 0.0, "max_tokens": 1,
        "chat_template_kwargs": {"enable_thinking": False},
    }


class RuntimeLease:
    """Hold from capture start until live transcription/final/replay has ended."""

    def __init__(self, runtime, token):
        self._runtime = runtime
        self._token = token

    @property
    def server_url(self) -> str:
        return self._runtime.server_url

    def wait_ready(self, *, cancel=None, timeout=300.0) -> None:
        self._runtime.wait_ready(cancel=cancel, timeout=timeout)

    def release(self) -> None:
        self._runtime._release(self._token)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


class ManagedRuntime:
    """Start immediately; probe readiness in the caller's transcription worker.

    This never contacts, controls or terminates a user-configured endpoint. A
    dedicated random loopback port and direct Popen handle identify our child.
    Downloads are separate setup work, never started implicitly on a shortcut.
    """

    def __init__(
        self, model_dir, *, runtime_dir=None, accelerator="cpu", idle_seconds=300.0,
        model_store=None, session=None, popen_factory=subprocess.Popen,
        timer_factory=threading.Timer, host_validator=validate_managed_host,
        port=None,
    ):
        if accelerator not in {"cpu", "vulkan"}:
            raise ValueError("accelerator must be 'cpu' or 'vulkan'")
        if idle_seconds < 0:
            raise ValueError("idle_seconds cannot be negative")
        if port is None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port must be an unprivileged or user-selected valid port")
        self.server_url = f"http://127.0.0.1:{port}"
        self.port = port
        self.model_store = model_store if model_store is not None else ModelStore(
            model_dir, assistant=MTP_ASSISTANT if accelerator == "vulkan" else None,
        )
        self.runtime_dir = runtime_dir
        self.accelerator = accelerator
        self.idle_seconds = idle_seconds
        self.log_path = Path(model_dir) / "managed-runtime.log"
        self._session = session if session is not None else requests.Session()
        if session is None:
            self._session.trust_env = False
        self._popen = popen_factory
        self._timer_factory = timer_factory
        self._host_validator = host_validator
        self._lock = threading.RLock()
        self._probe_lock = threading.Lock()
        self._leases = set()
        self._process = None
        self._log = None
        self._idle_timer = None
        self._idle_token = None
        self._generation = 0
        self._ready_generation = None
        self._closed = False
        self._closing = threading.Event()

    def acquire(self) -> RuntimeLease:
        """Start the process without waiting for model loading or an audio probe."""
        with self._lock:
            if self._closed:
                raise RuntimeStartupError("The local runtime has been closed. Restart the app.")
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
                self._idle_token = None
            if self._process is None or self._process.poll() is not None:
                self._start_locked()
            token = object()
            self._leases.add(token)
            return RuntimeLease(self, token)

    def _start_locked(self):
        self._host_validator()
        if not self.model_store.installed():
            raise RuntimeStartupError(
                "Local model files are missing or changed. Open setup and download/verify the model."
            )
        directory = find_runtime_dir(self.runtime_dir).resolve()
        binary = directory / "llama-server"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeStartupError(
                f"llama-server is missing or not executable in {directory}. "
                "Reinstall the complete desktop package."
            )
        paths = self.model_store.model_paths
        if self.accelerator == "vulkan" and paths.assistant is None:
            raise RuntimeStartupError(
                "The Vulkan acceleration model is missing. Open setup, select Vulkan "
                "and download/verify the local models."
            )
        args = [
            str(binary), "-m", str(paths.model), "--mmproj", str(paths.projector),
            "--alias", "gemma-e4b", "--host", "127.0.0.1", "--port", str(self.port),
            "-c", "8192", "-b", "2048", "-ub", "2048", "-np", "1",
            "--cache-ram", "0", "--fit", "off", "-ctk", "f16", "-ctv", "f16",
        ]
        if self.accelerator == "vulkan":
            args += [
                "--device", "Vulkan0", "-ngl", "99", "-fa", "on",
                "--mtp-head", str(paths.assistant), "--spec-type", "mtp",
                "--draft-block-size", "3", "--device-draft", "Vulkan0", "-ngld", "99",
            ]
        else:
            args += ["--device", "none", "-ngl", "0", "--no-mmproj-offload"]
        env = os.environ.copy()
        if self.accelerator == "vulkan":
            env["LLAMA_MTP_SKIP_STREAK_THRESHOLD"] = "0"
            env["MTMD_BACKEND_DEVICE"] = "Vulkan0"
        original_libs = env.get("LD_LIBRARY_PATH_ORIG", "")
        env["LD_LIBRARY_PATH"] = str(directory) + (":" + original_libs if original_libs else "")
        self._close_log_locked()
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("wb")
            self._process = self._popen(
                args, stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
                cwd=str(directory), env=env,
            )
        except OSError as exc:
            self._close_log_locked()
            self._process = None
            raise RuntimeStartupError(f"Could not start local inference: {exc}. Reinstall the runtime.") from exc
        self._generation += 1
        self._ready_generation = None

    def _failure(self, message):
        return RuntimeStartupError(f"{message} See {self.log_path} for details.")

    def _check_process(self, generation):
        with self._lock:
            if self._closed:
                raise self._failure("Local inference was closed.")
            if self._generation != generation or self._process is None:
                raise self._failure("The local inference session ended. Press the shortcut to retry.")
            status = self._process.poll()
            if status is not None:
                raise self._failure(
                    f"Local inference exited with status {status}. Check memory availability "
                    "and the selected CPU/Vulkan mode, then retry."
                )

    def wait_ready(self, *, cancel=None, timeout=300.0) -> None:
        """Verify that this child can decode audio, once per process lifetime.

        The built-in silence request proves the input_audio path is available,
        not transcription quality. It is never sent to the keyboard injector.
        A caller cancellation affects that wait, not other active session leases.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        cancel = cancel if cancel is not None else threading.Event()
        deadline = time.monotonic() + timeout
        with self._lock:
            generation = self._generation
        acquired = False
        last_error = None
        try:
            while not acquired:
                self._check_wait(cancel, deadline, generation)
                acquired = self._probe_lock.acquire(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
            while True:
                self._check_wait(cancel, deadline, generation)
                with self._lock:
                    if self._ready_generation == generation:
                        return
                remaining = max(0.001, deadline - time.monotonic())
                try:
                    with self._session.get(
                        f"{self.server_url}/health", timeout=(min(2, remaining), min(2, remaining)),
                    ) as health:
                        health.raise_for_status()
                    self._check_wait(cancel, deadline, generation)
                    remaining = max(0.001, deadline - time.monotonic())
                    with self._session.post(
                        f"{self.server_url}/v1/chat/completions", json=_audio_probe_payload(),
                        timeout=(min(2, remaining), min(60, remaining)),
                    ) as response:
                        response.raise_for_status()
                        result = response.json()
                    choices = result.get("choices") if isinstance(result, dict) else None
                    if not choices or not isinstance(choices[0].get("message", {}).get("content"), str):
                        raise self._failure("Local inference returned an invalid audio-probe response.")
                    self._check_wait(cancel, deadline, generation)
                    with self._lock:
                        self._ready_generation = generation
                    return
                except requests.HTTPError as exc:
                    status = getattr(exc.response, "status_code", None)
                    if status is not None and 400 <= status < 500:
                        raise self._failure(
                            f"The runtime rejected input_audio (HTTP {status}). "
                            "Reinstall the matching runtime and projector."
                        ) from exc
                    last_error = exc
                except requests.RequestException as exc:
                    last_error = exc
                except (ValueError, AttributeError, TypeError) as exc:
                    raise self._failure("Local inference returned an invalid audio-probe response.") from exc
                self._check_wait(cancel, deadline, generation, last_error)
                self._closing.wait(min(0.25, max(0, deadline - time.monotonic())))
        finally:
            if acquired:
                self._probe_lock.release()

    def _check_wait(self, cancel, deadline, generation, last_error=None):
        if cancel.is_set():
            raise DownloadCancelled("Local inference readiness wait cancelled.")
        self._check_process(generation)
        if time.monotonic() >= deadline:
            raise self._failure(
                "Local inference did not become audio-ready in time. Check available RAM "
                "and the runtime log, increase the startup timeout if needed, then retry."
            ) from last_error

    def _release(self, token):
        with self._lock:
            if token not in self._leases:
                return
            self._leases.remove(token)
            if not self._leases and not self._closed:
                generation = self._generation
                token = object()
                timer = self._timer_factory(self.idle_seconds, lambda: self._expire(generation, token))
                timer.daemon = True
                self._idle_timer = timer
                self._idle_token = token
                timer.start()

    def _expire(self, generation, token):
        with self._lock:
            if (not self._leases and self._generation == generation
                    and self._idle_token is token):
                self._stop_locked()
                self._idle_timer = None
                self._idle_token = None

    def _stop_locked(self):
        process = self._process
        self._process = None
        self._ready_generation = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        self._close_log_locked()

    def _close_log_locked(self):
        if self._log is not None:
            self._log.close()
            self._log = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._closing.set()
            self._leases.clear()
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
                self._idle_token = None
            self._stop_locked()
