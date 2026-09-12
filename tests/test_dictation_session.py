import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_headless_adapter_runs_without_desktop_audio_or_model_dependencies(tmp_path):
    """A new platform can use the session without importing the Linux/Qt shell."""
    script = textwrap.dedent(
        """
        import builtins
        from pathlib import Path
        from threading import Event
        from types import SimpleNamespace

        real_import = builtins.__import__
        banned = (
            'PyQt6', 'sounddevice', 'numpy', 'onnxruntime', 'requests',
            'voxd.core.voxd_core', 'voxd.core.recorder', 'voxd.core.typer',
            'voxd.core.clipboard', 'voxd.core.gemma_transcriber',
        )
        def guard(name, *args, **kwargs):
            if any(name == root or name.startswith(root + '.') for root in banned):
                raise AssertionError('headless session imported ' + name)
            return real_import(name, *args, **kwargs)
        builtins.__import__ = guard

        from voxd.core.session import DictationSession

        events = []
        class Recorder:
            is_recording = False
            last_temp_file = None
            stopped = Event()
            def start_recording(self):
                self.is_recording = True
                events.append('record')
            def stop_recording(self, preserve=False):
                self.is_recording = False
                self.stopped.set()
                return Path('adapter.wav')
            def iter_segments(self):
                assert self.stopped.wait(1)
                yield (0, b'adapter audio', True)

        class Transcriber:
            def warmup(self):
                events.append('warmup')
            def transcribe_segments(self, segments):
                assert list(segments) == [(0, b'adapter audio', True)]
                return SimpleNamespace(text='spoken words', raw_transcript='spoken words')
            def cleanup_input(self, path):
                assert path == Path('adapter.wav')
                events.append('cleanup')

        session = DictationSession(
            transcriber_factory=Transcriber,
            recorder_factory=lambda transcriber: Recorder(),
            clipboard_factory=lambda: SimpleNamespace(copy=lambda text: events.append(('copy', text))),
            typer_factory=lambda: SimpleNamespace(type=lambda text: events.append(('type', text))),
            status_callback=lambda status: events.append(('status', status)),
        )
        session.stop_recording()
        assert session.run() == 'spoken words'
        assert events[0] == 'record'
        assert events.index('cleanup') < events.index(('copy', 'spoken words'))
        assert events.index(('copy', 'spoken words')) < events.index(('type', 'spoken words'))
        assert ('status', 'Transcribing') in events
        assert ('status', 'Typing') in events
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_transcriber_factory_failure_returns_without_starting_recorder():
    from voxd.core.session import DictationSession

    def unavailable():
        raise RuntimeError("model unavailable")

    def unexpected(*args):
        raise AssertionError("no platform operation should start")

    session = DictationSession(
        transcriber_factory=unavailable,
        recorder_factory=unexpected,
        clipboard_factory=unexpected,
        typer_factory=unexpected,
        archive_factory=unexpected,
        preserve_audio=True,
    )

    assert session.run() == ""
