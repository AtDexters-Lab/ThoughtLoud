import io
import json
import subprocess
from queue import Queue
from types import SimpleNamespace

import numpy as np
import pytest


def install_probe(monkeypatch, muted=False, source="input.headset"):
    from voxd.platforms.linux import audio
    calls = []
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["pactl", "get-default-source"]:
            return SimpleNamespace(stdout=source + "\n")
        return SimpleNamespace(stdout=json.dumps([{"name": source, "mute": muted}]))
    monkeypatch.setattr(audio.subprocess, "run", run)
    return audio, calls


@pytest.mark.parametrize("muted", [True, False])
def test_probe_mute_applies_to_exact_named_capture_source(monkeypatch, muted):
    audio, calls = install_probe(monkeypatch, muted)
    result = audio.probe_input()
    assert result.source == "input.headset" and result.muted is muted
    assert all(call[1]["timeout"] <= 0.7 for call in calls)
    assert all("set-" not in " ".join(call[0]) for call in calls)


def test_default_source_is_resolved_again_for_next_recording(monkeypatch):
    audio, _ = install_probe(monkeypatch, source="input.builtin")
    assert audio.probe_input().source == "input.builtin"
    audio, _ = install_probe(monkeypatch, source="input.headset")
    assert audio.probe_input().source == "input.headset"


@pytest.mark.parametrize("device,prefer", [("USB mic", True), ("", False)])
def test_explicit_override_never_uses_unrelated_default_mute(monkeypatch, device, prefer):
    audio, calls = install_probe(monkeypatch, muted=True)
    assert audio.probe_input(device, prefer).muted is None
    assert calls == []


def test_probe_absent_capture_helper_cannot_report_positive_mute(monkeypatch):
    audio, calls = install_probe(monkeypatch, muted=True)
    monkeypatch.setattr(audio.shutil, "which", lambda name: None if name == "parec" else name)
    assert audio.probe_input().muted is None
    assert calls == []


def test_probe_timeout_is_unknown(monkeypatch):
    audio, _ = install_probe(monkeypatch)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(audio.subprocess, "run", timeout)
    assert audio.probe_input().muted is None


class FakeProcess:
    def __init__(self, data):
        self.stdout = io.BytesIO(data)
        self.returncode = None
        self.terminated = False
    def terminate(self):
        self.terminated = True
        self.returncode = 0
    def wait(self, **_kwargs):
        return self.returncode
    def poll(self):
        return self.returncode


def test_pinned_capture_keeps_final_short_pcm_block(monkeypatch):
    from voxd.platforms.linux import audio
    pcm = np.arange(1103, dtype=np.float32) / 1103

    class TailAfterTerminateProcess(FakeProcess):
        def __init__(self):
            super().__init__(b"")
            self.output = Queue()
            self.output.put(pcm[:1024].tobytes())
            self.stdout = SimpleNamespace(
                read=lambda _size: self.output.get(timeout=2), close=lambda: None,
            )

        def terminate(self):
            super().terminate()
            self.output.put(pcm[1024:].tobytes())
            self.output.put(b"")

    process = TailAfterTerminateProcess()
    commands = []
    def popen(command, **kwargs):
        commands.append(command)
        return process
    monkeypatch.setattr(audio.subprocess, "Popen", popen)
    received = Queue()
    stream = audio.PulseInputStream(source="input.headset", samplerate=16000,
        channels=1, callback=lambda data, *_: received.put((process.terminated, data.copy())))
    stream.start()
    first_after_stop, first = received.get(timeout=2)
    assert not first_after_stop
    stream.stop()
    # The short tail appears only after terminate(), and must arrive before
    # stop() returns so the recorder can safely close its WAV afterward.
    tail_after_stop, tail = received.get_nowait()
    assert tail_after_stop and len(tail) == 79
    assert not stream.worker.is_alive()
    stream.close()
    assert "--device=input.headset" in commands[0]
    assert "--latency-msec=50" in commands[0]
    assert np.array_equal(np.concatenate([first, tail])[:, 0], pcm)
    assert received.empty()
    assert process.terminated


def test_capture_failure_is_reported_on_stop(monkeypatch):
    from voxd.platforms.linux import audio
    monkeypatch.setattr(audio.subprocess, "Popen", lambda *_, **__: FakeProcess(b""))
    stream = audio.PulseInputStream(source="gone", samplerate=16000, channels=1, callback=lambda *_: None)
    stream.start()
    stream.worker.join(timeout=1)
    with pytest.raises(RuntimeError, match="ended unexpectedly"):
        stream.stop()
    stream.close()
