"""Read-only Linux input discovery and capture pinned to a PulseAudio source."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from threading import Event, Thread
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class InputStatus:
    source: str | None = None
    muted: bool | None = None
    detail: str = "Microphone mute status unavailable; system input will be used."


def probe_input(input_device: str = "", prefer_pulse: bool = True) -> InputStatus:
    # PortAudio device names cannot safely be equated with Pulse source names.
    # Only report a definitive mute state when capture can bind the exact source.
    if input_device not in ("", "pulse") or not prefer_pulse:
        return InputStatus(detail="Using configured input; mute status unavailable.")
    if not shutil.which("pactl") or not shutil.which("parec"):
        return InputStatus()
    source = None
    try:
        result = subprocess.run(
            ["pactl", "get-default-source"], capture_output=True, text=True,
            timeout=0.7, check=True,
        )
        source = result.stdout.strip()
        if not source or "\n" in source:
            return InputStatus()
        result = subprocess.run(
            ["pactl", "--format=json", "list", "sources"],
            capture_output=True, text=True, timeout=0.7, check=True,
        )
        sources = json.loads(result.stdout)
        for item in sources:
            if item.get("name") == source and isinstance(item.get("mute"), bool):
                muted = item["mute"]
                name = item.get("description") or source
                return InputStatus(source, muted, f"{name}: {'muted' if muted else 'unmuted'}")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        pass
    return InputStatus(source, None, "Microphone mute status unavailable.")


class PulseInputStream:
    """Small sounddevice-compatible stream using an explicitly named source.

    parec works with both PulseAudio and PipeWire's PulseAudio server. A failed
    pinned source is an error, never a reason to capture a different microphone.
    """

    def __init__(self, *, source: str, samplerate: int, channels: int,
                 callback: Callable, **_kwargs):
        self.source, self.samplerate, self.channels = source, samplerate, channels
        self.callback = callback
        self.process = None
        self.worker = None
        self.stopping = Event()
        self.error = None

    def start(self):
        # Request small fragments instead of the server's roughly 2 s default.
        # The server may adjust this latency; it is not a guaranteed stop bound.
        self.process = subprocess.Popen(
            ["parec", f"--device={self.source}", "--raw", "--format=float32le",
             f"--rate={self.samplerate}", f"--channels={self.channels}",
             "--latency-msec=50"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.worker = Thread(target=self._read, name="voxd-pulse-capture", daemon=True)
        self.worker.start()

    def _read(self):
        frame_bytes = self.channels * 4
        pending = b""
        try:
            while True:
                data = self.process.stdout.read(1024 * frame_bytes)
                if not data:
                    if not self.stopping.is_set():
                        self.error = RuntimeError("microphone capture process ended unexpectedly")
                    return
                pending += data
                count = len(pending) // frame_bytes * frame_bytes
                if count:
                    pcm = np.frombuffer(pending[:count], dtype="<f4").reshape(-1, self.channels)
                    self.callback(pcm, len(pcm), None, None)
                    pending = pending[count:]
        except Exception as exc:
            if not self.stopping.is_set():
                self.error = exc

    def stop(self):
        self.stopping.set()
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        if self.worker is not None:
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                raise RuntimeError("microphone capture did not stop")
        if self.error is not None:
            raise self.error

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.stop()
            if self.process.stdout is not None:
                self.process.stdout.close()
