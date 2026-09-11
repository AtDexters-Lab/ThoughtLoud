from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from voxd.core.keyboard_state import KeyboardState, KeyStateError
from voxd.utils.libw import verbo


_TEXT_CHUNK_CHARS = 400
_WORD_KEY_HOLD_MS = 1
_LEGACY_KEY_HOLD_MS = 5
_DRAIN_DELAY = 0.05
_RELEASE_ARGS = [
    f"{keycode}:0"
    for keycode in (
        list(range(2, 14))
        + [15, 28]  # Tab and Enter can also be interrupted while held.
        + list(range(16, 28))
        + list(range(30, 42))
        + [29, 42, 43]
        + list(range(44, 54))
        + [54, 56, 57, 97, 100, 125, 126]
    )
]


class YdotoolTyper:
    """Emit real Linux input events through ydotool; never paste text."""

    def __init__(self, *, delay=0, word_delay=10, start_delay=0.15, cfg=None):
        try:
            self.delay_ms = max(0.0, float(delay))
        except (TypeError, ValueError):
            self.delay_ms = 0.0
        try:
            self.word_delay_ms = max(0.0, float(word_delay))
        except (TypeError, ValueError):
            self.word_delay_ms = 10.0
        try:
            self.start_delay = max(0.0, float(start_delay))
        except (TypeError, ValueError):
            self.start_delay = 0.15
        self.delay_str = str(int(self.delay_ms))
        self.legacy_delay_ms = max(1.0, self.delay_ms)
        self.legacy_delay_str = str(int(self.legacy_delay_ms))
        self.word_delay_str = str(int(self.word_delay_ms))
        self.cfg = cfg
        default_socket = str(Path.home() / ".ydotool_socket")
        self.socket_path = Path(os.environ.setdefault("YDOTOOL_SOCKET", default_socket))
        self.tool = self._find_tool()
        self.supports_key_hold = self._supports_key_hold(self.tool)
        self.supports_word_pacing = self._supports_word_pacing(self.tool)

    @staticmethod
    def _find_tool() -> str | None:
        candidates = [
            shutil.which("ydotool"),
            "/usr/local/bin/ydotool",
            "/usr/bin/ydotool",
            str(Path.home() / ".local/bin/ydotool"),
            str(Path.home() / ".local/share/voxd/bin/ydotool"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @staticmethod
    def _supports_key_hold(tool: str | None) -> bool:
        if not tool:
            return False
        try:
            with open(tool, "rb") as executable:
                return b"key-hold" in executable.read()
        except OSError:
            return False

    @staticmethod
    def _supports_word_pacing(tool: str | None) -> bool:
        """Detect the complete modern `type` CLI used by the fast path."""
        if not tool:
            return False
        required_markers = (
            b"Usage: type [OPTION]... [STRINGS]...",
            b"key-hold=N",
            b"Delay N milliseconds between command line strings",
            b"escape=BOOL",
            b"hd:D:H:f:e:",
        )
        try:
            with open(tool, "rb") as executable:
                contents = executable.read()
        except OSError:
            return False
        return all(marker in contents for marker in required_markers)

    def _ensure_daemon(self) -> bool:
        if self._daemon_socket_ready():
            return True
        try:
            subprocess.run(
                ["systemctl", "--user", "start", "ydotoold.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

        for _ in range(10):
            if self._daemon_socket_ready():
                return True
            time.sleep(0.1)
        return False

    def _daemon_socket_ready(self) -> bool:
        if not self.socket_path.exists():
            return False
        # ydotoold uses a Unix datagram socket. A stream probe always fails
        # with a protocol mismatch even while the daemon is healthy.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            probe.settimeout(0.25)
            probe.connect(str(self.socket_path))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def type(self, text: str) -> None:
        if not self.tool:
            raise RuntimeError("ydotool is not installed")
        if not self._ensure_daemon():
            raise RuntimeError("ydotoold is not running")

        if self.start_delay:
            time.sleep(self.start_delay)

        rendered = text.rstrip()
        if self.cfg is None or self.cfg.data.get("append_trailing_space", True):
            rendered += " "

        verbo(f"[typer] Typing {len(rendered)} characters with ydotool")
        self._key_monitor = self._open_key_monitor()
        self._typing_process = None
        attempted = False
        try:
            if self._key_monitor and self._key_monitor.pressed_keys():
                raise RuntimeError("ydotool keyboard is already held; typing was not started")
            for chunk in self._split_chunks(rendered):
                attempted = True
                self._type_chunk(chunk)
            if self._key_monitor:
                self._key_monitor.settle(released=True)
        except Exception as exc:
            if self._key_monitor and attempted:
                try:
                    self._recover_keys()
                except Exception as recovery_error:
                    raise RuntimeError(f"{exc}; keyboard recovery failed: {recovery_error}") from exc
            raise
        finally:
            if self._key_monitor:
                self._key_monitor.close()
                self._key_monitor = None

    def _open_key_monitor(self) -> KeyboardState | None:
        try:
            if not self.supports_key_hold:
                raise OSError("legacy ydotool has no explicit bounded key hold")
            return KeyboardState.open(self.socket_path)
        except (OSError, KeyStateError) as exc:
            print(f"[typer] Key-state monitoring unavailable; using compatibility cleanup: {exc}",
                  flush=True)
            return None

    def _type_chunk(self, text: str) -> None:
        word_runs = self._word_runs(text) if self.supports_word_pacing else []
        hold_ms = (
            _WORD_KEY_HOLD_MS
            if self.supports_word_pacing
            else _LEGACY_KEY_HOLD_MS if self.supports_key_hold else 0
        )
        character_delay_ms = (
            self.delay_ms if self.supports_word_pacing else self.legacy_delay_ms
        )
        word_pause_ms = max(0, len(word_runs) - 1) * self.word_delay_ms
        expected_seconds = (
            len(text) * (character_delay_ms + hold_ms) + word_pause_ms
        ) / 1000.0
        timeout = max(5.0, expected_seconds * 2.0 + 3.0)
        if self.supports_word_pacing:
            command = [
                self.tool,
                "type",
                "-d",
                self.delay_str,
                "-H",
                str(_WORD_KEY_HOLD_MS),
                "-D",
                self.word_delay_str,
                "-e",
                "0",
                "--",
                *word_runs,
            ]
            input_text = None
        elif self.supports_key_hold:
            command = [
                self.tool,
                "type",
                "-d",
                self.legacy_delay_str,
                "-H",
                str(_LEGACY_KEY_HOLD_MS),
                "-f",
                "-",
            ]
            input_text = text
        else:
            command = [
                self.tool,
                "type",
                "--key-delay",
                self.legacy_delay_str,
                "--file",
                "-",
            ]
            input_text = text
        succeeded = self._run_tool(command, timeout=timeout, input_text=input_text)
        if self._key_monitor is None:
            time.sleep(_DRAIN_DELAY)
            self._release_keys()
        if not succeeded:
            raise RuntimeError("ydotool failed before the complete transcript was typed")

    @staticmethod
    def _word_runs(text: str) -> list[str]:
        """Split at word boundaries while preserving every input character."""
        return re.findall(r"\s+|\S+\s*", text)

    @staticmethod
    def _split_chunks(text: str, max_chars: int = _TEXT_CHUNK_CHARS):
        if max_chars < 1:
            raise ValueError("max_chars must be positive")

        start = 0
        while len(text) - start > max_chars:
            limit = start + max_chars
            split_at = max(
                text.rfind(" ", start, limit),
                text.rfind("\n", start, limit),
                text.rfind("\t", start, limit),
            )
            if split_at < start + max_chars // 2:
                split_at = limit
            else:
                split_at += 1
            yield text[start:split_at]
            start = split_at
        if start < len(text):
            yield text[start:]

    def _run_tool(
        self, command: list[str], *, timeout: float, input_text: str | None = None
    ) -> bool:
        try:
            if self._key_monitor is not None:
                return self._run_monitored_tool(command, timeout=timeout, input_text=input_text)
            result = subprocess.run(
                command,
                input=input_text,
                text=input_text is not None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def _run_monitored_tool(
        self, command: list[str], *, timeout: float, input_text: str | None
    ) -> bool:
        # Wake promptly on input events while the child emits independently.
        # This adds no per-key acknowledgement and owns only this child process.
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=input_text is not None,
        )
        self._typing_process = process
        deadline = time.monotonic() + timeout
        try:
            if input_text is not None:
                # A bounded <=400-character chunk fits in an empty pipe. Close
                # it immediately so the stdin-based client can observe EOF.
                process.stdin.write(input_text)
                process.stdin.close()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                self._key_monitor.wait(min(0.02, remaining))
                stuck = self._key_monitor.stuck_keys()
                if stuck:
                    raise KeyStateError(f"ydotool key remained held during typing: {sorted(stuck)}")
                if process.poll() is not None:
                    return process.returncode == 0
        finally:
            # Recovery must never race an owned process that is still emitting.
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            if process.stdin is not None:
                process.stdin.close()
            self._typing_process = None

    def _recover_keys(self) -> None:
        if self._typing_process is not None and self._typing_process.poll() is None:
            raise RuntimeError("typing process could not be stopped; refusing concurrent cleanup")
        try:
            keys = self._key_monitor.settle(released=False)
        except KeyStateError:
            # Observation was lost. Retain the old recovery as a last resort,
            # but do not claim that the keyboard or insertion was verified.
            time.sleep(_DRAIN_DELAY)
            self._release_keys()
            raise
        if keys and not self._release_keys(keys):
            raise RuntimeError("ydotool key-release command failed")
        self._key_monitor.settle(released=True)

    def _release_keys(self, keys: set[int] | None = None) -> bool:
        if not self.tool:
            return False
        release_args = _RELEASE_ARGS if keys is None else [f"{key}:0" for key in sorted(keys)]
        if not release_args:
            return True
        # Modern key accepts an explicit delay. Recovery should not spend
        # ~12 ms on each release; legacy CLI retains its compatible arguments.
        delay_args = ["-d", "0"] if self.supports_key_hold else []
        try:
            result = subprocess.run(
                [self.tool, "key", *delay_args, *release_args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
