import hashlib
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from voxd.runtime import (
    DownloadCancelled, ManagedRuntime, ModelDownloadError, ModelStore, RuntimeStartupError,
)
from voxd.runtime.models import ModelArtifact, MODEL_ARTIFACTS, MTP_ASSISTANT


class Response:
    def __init__(self, chunks=(), *, status=200, data=None):
        self.chunks = chunks
        self.status_code = status
        self.data = data if data is not None else {"choices": [{"message": {"content": ""}}]}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def json(self):
        return self.data


class Session:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.calls = []
        self.probes = []
        self.health = Response()
        self.probe = Response()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/health"):
            return self.health
        return next(self.responses)

    def post(self, url, **kwargs):
        self.probes.append((url, kwargs))
        return self.probe


@pytest.fixture
def artifacts():
    return tuple(
        ModelArtifact(name, len(content), hashlib.sha256(content).hexdigest())
        for name, content in [("target.gguf", b"target"), ("projector.gguf", b"projector")]
    )


@pytest.fixture
def assistant():
    return ModelArtifact(
        "assistant.gguf", 9, hashlib.sha256(b"assistant").hexdigest(),
        "https://assistant.invalid/resolve/pinned-revision/assistant.gguf",
    )


def store_for(tmp_path, artifacts, responses=(), **kwargs):
    return ModelStore(tmp_path, artifacts=artifacts, session=Session(responses), **kwargs)


def test_download_validates_and_promotes_both_files_atomically(tmp_path, artifacts):
    store = store_for(tmp_path, artifacts, [Response([b"tar", b"get"]), Response([b"projector"])])
    events = []

    def progress(event):
        events.append(event)
        if event.filename == "target.gguf" and event.stage == "downloading":
            assert not (tmp_path / "target.gguf").exists()

    paths = store.ensure_models(progress=progress)
    assert paths.model.read_bytes() == b"target"
    assert paths.projector.read_bytes() == b"projector"
    assert store.installed()
    assert events[-1].stage == "complete"
    assert events[-1].downloaded_bytes == events[-1].total_bytes == 15
    assert not list(tmp_path.glob("*.part"))
    assert all(call[1] == {"stream": True, "timeout": (10, 30)} for call in store.session.calls)
    store.ensure_models()
    assert len(store.session.calls) == 2


def test_existing_files_are_verified_without_network(tmp_path, artifacts):
    (tmp_path / "target.gguf").write_bytes(b"target")
    (tmp_path / "projector.gguf").write_bytes(b"projector")
    store = store_for(tmp_path, artifacts)
    assert not store.installed()
    store.ensure_models()
    assert store.installed()
    assert not store.session.calls


def test_cpu_to_vulkan_only_downloads_assistant_from_its_pinned_url(tmp_path, artifacts, assistant):
    cpu = store_for(tmp_path, artifacts, [Response([b"target"]), Response([b"projector"])])
    cpu.ensure_models()
    receipts = {path.name: path.read_bytes() for path in tmp_path.glob("*.verified.json")}
    vulkan = store_for(tmp_path, artifacts, [Response([b"assistant"])], assistant=assistant)
    assert cpu.installed() and not vulkan.installed()
    paths = vulkan.ensure_models()
    assert paths.assistant.read_bytes() == b"assistant"
    assert vulkan.installed() and cpu.installed()
    assert vulkan.total_bytes == cpu.total_bytes + assistant.size
    assert [url for url, _ in vulkan.session.calls] == [assistant.url]
    assert receipts == {name: (tmp_path / name).read_bytes() for name in receipts}
    paths.assistant.unlink()
    assert cpu.installed() and not vulkan.installed()


def test_assistant_cancellation_keeps_cpu_ready_and_retries_only_assistant(tmp_path, artifacts, assistant):
    cpu = store_for(tmp_path, artifacts, [Response([b"target"]), Response([b"projector"])])
    cpu.ensure_models()
    vulkan = store_for(tmp_path, artifacts, [
        Response([b"assi", b"stant"]), Response([b"assistant"]),
    ], assistant=assistant)
    cancel = threading.Event()

    def progress(event):
        if event.filename == assistant.filename and event.downloaded_bytes == cpu.total_bytes + 4:
            cancel.set()

    with pytest.raises(DownloadCancelled):
        vulkan.ensure_models(cancel=cancel, progress=progress)
    assert cpu.installed() and not vulkan.installed()
    assert not list(tmp_path.glob("*.part"))
    assert not vulkan.model_paths.assistant.exists()
    vulkan.ensure_models()
    assert vulkan.installed()
    assert [url for url, _ in vulkan.session.calls] == [assistant.url, assistant.url]


def test_wrong_assistant_digest_does_not_invalidate_cpu_models(tmp_path, artifacts, assistant):
    cpu = store_for(tmp_path, artifacts, [Response([b"target"]), Response([b"projector"])])
    cpu.ensure_models()
    vulkan = store_for(tmp_path, artifacts, [Response([b"incorrect"])], assistant=assistant, attempts=1)
    with pytest.raises(ModelDownloadError, match="SHA256"):
        vulkan.ensure_models()
    assert cpu.installed() and not vulkan.installed()
    assert not vulkan.model_paths.assistant.exists()
    assert not list(tmp_path.glob("*.part"))


def test_changed_cached_file_invalidates_receipt_and_is_replaced(tmp_path, artifacts):
    store = store_for(tmp_path, artifacts, [Response([b"target"]), Response([b"projector"]), Response([b"target"])])
    store.ensure_models()
    (tmp_path / "target.gguf").write_bytes(b"broken")
    assert not store.installed()
    store.ensure_models()
    assert store.installed()
    assert len(store.session.calls) == 3


@pytest.mark.parametrize("response", [Response([b"broken"]), Response([b"short"]), Response([b"too long"]), Response([b"target"], status=206)])
def test_bad_download_never_replaces_existing_file(tmp_path, artifacts, response):
    (tmp_path / "target.gguf").write_bytes(b"old")
    store = store_for(tmp_path, artifacts, [response], attempts=1)
    with pytest.raises(ModelDownloadError):
        store.ensure_models()
    assert (tmp_path / "target.gguf").read_bytes() == b"old"
    assert not store.installed()
    assert not list(tmp_path.glob("*.part"))
    assert response.closed


def test_network_interruption_retries_from_zero(tmp_path, artifacts):
    responses = [
        Response([b"tar", requests.ConnectionError("interrupted")]),
        Response([b"target"]), Response([b"projector"]),
    ]
    store = store_for(tmp_path, artifacts, responses)
    store.ensure_models()
    assert store.installed()
    assert len(store.session.calls) == 3
    assert all("headers" not in call[1] for call in store.session.calls)
    assert not list(tmp_path.glob("*.part"))


def test_cancel_discards_partial_and_allows_retry(tmp_path, artifacts):
    cancel = threading.Event()
    store = store_for(tmp_path, artifacts, [Response([b"tar", b"get"]), Response([b"target"]), Response([b"projector"])])

    def progress(event):
        if event.downloaded_bytes == 3:
            cancel.set()

    with pytest.raises(DownloadCancelled):
        store.ensure_models(cancel=cancel, progress=progress)
    assert not (tmp_path / "target.gguf").exists()
    assert not list(tmp_path.glob("*.part"))
    store.ensure_models()
    assert store.installed()


def test_disk_space_checked_before_network(tmp_path, artifacts, monkeypatch):
    monkeypatch.setattr("voxd.runtime.models.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    store = store_for(tmp_path, artifacts)
    with pytest.raises(ModelDownloadError, match="disk space"):
        store.ensure_models()
    assert not store.session.calls


class Process:
    def __init__(self):
        self.code = None
        self.terminated = 0
        self.killed = 0
        self.waits = []
        self.stubborn = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated += 1
        if not self.stubborn:
            self.code = -15

    def kill(self):
        self.killed += 1
        self.code = -9

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.code is None:
            raise subprocess.TimeoutExpired("fake-server", timeout)
        return self.code


class Timer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.cancelled = False
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def runtime_factory(tmp_path, artifacts, assistant):
    store = store_for(tmp_path / "models", artifacts, [Response([b"target"]), Response([b"projector"])])
    store.ensure_models()
    native = tmp_path / "llama"
    native.mkdir()
    (native / "llama-server").touch(mode=0o755)
    runtimes = []

    def factory(**kwargs):
        processes, calls, timers = [], [], []
        selected_store = kwargs.pop("model_store", None)
        if selected_store is None:
            selected_store = store
            if kwargs.get("accelerator") == "vulkan":
                selected_store = store_for(
                    store.model_dir, artifacts, [Response([b"assistant"])], assistant=assistant,
                )
                selected_store.ensure_models()

        def popen(*args, **kwargs):
            process = Process()
            processes.append(process)
            calls.append((args, kwargs))
            return process

        def timer(*args):
            result = Timer(*args)
            timers.append(result)
            return result

        session = Session()
        runtime = ManagedRuntime(
            store.model_dir, runtime_dir=native, model_store=selected_store, port=34917,
            session=session, popen_factory=popen, timer_factory=timer,
            host_validator=lambda: None, **kwargs,
        )
        runtimes.append(runtime)
        return runtime, processes, calls, timers, session

    yield factory
    for runtime in runtimes:
        runtime.close()


def test_acquire_does_not_wait_for_readiness_and_uses_controlled_cpu_args(runtime_factory, monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unrelated/pyinstaller")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/original")
    runtime, processes, calls, timers, session = runtime_factory()
    lease = runtime.acquire()
    assert not session.calls and not session.probes
    args, kwargs = calls[0]
    argv = args[0]
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--device") + 1] == "none"
    assert argv[argv.index("-ngl") + 1] == "0"
    assert "--no-mmproj-offload" in argv
    assert argv[argv.index("-c") + 1] == "8192"
    assert argv[argv.index("--cache-ram") + 1] == "0"
    assert "--mtp-head" not in argv and "--spec-type" not in argv
    assert kwargs["env"]["LD_LIBRARY_PATH"].endswith("/llama:/original")
    assert "pyinstaller" not in kwargs["env"]["LD_LIBRARY_PATH"]
    assert not timers
    assert lease.server_url == "http://127.0.0.1:34917"


def test_audio_readiness_probes_once_and_does_not_type(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    lease = runtime.acquire()
    lease.wait_ready()
    runtime.wait_ready()
    assert len(session.probes) == 1
    payload = session.probes[0][1]["json"]
    assert any(part["type"] == "input_audio" for part in payload["messages"][0]["content"])
    assert payload["max_tokens"] == 1
    assert session.calls[0][0].endswith("/health")


def test_entire_session_lease_prevents_idle_shutdown_and_reacquire_cancels_timer(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    first = runtime.acquire()
    second = runtime.acquire()
    first.release()
    first.release()
    assert not timers
    second.release()
    assert timers[-1].delay == 300
    assert timers[-1].started
    third = runtime.acquire()
    assert timers[-1].cancelled
    # A timer already firing must also check current leases.
    timers[-1].callback()
    assert processes[0].terminated == 0
    third.release()
    # Even after the next session finishes, an old cancelled callback must not
    # consume the new session's full idle grace period.
    timers[-2].callback()
    assert processes[0].terminated == 0
    timers[-1].callback()
    assert processes[0].terminated == 1
    assert processes[0].waits == [5]
    next_session = runtime.acquire()
    assert len(processes) == 2
    next_session.release()


def test_process_exit_is_actionable_and_next_session_restarts(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    lease = runtime.acquire()
    processes[0].code = 23
    with pytest.raises(RuntimeStartupError, match="status 23"):
        lease.wait_ready()
    assert not session.calls
    lease.release()
    next_lease = runtime.acquire()
    next_lease.wait_ready()
    assert len(processes) == 2
    assert len(session.probes) == 1


def test_probe_rejection_is_not_claimed_as_audio_ready(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    lease = runtime.acquire()
    session.probe = Response(status=400)
    with pytest.raises(RuntimeStartupError, match="rejected input_audio"):
        lease.wait_ready()


def test_wait_can_be_cancelled_without_releasing_other_leases(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    lease = runtime.acquire()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        lease.wait_ready(cancel=cancel)
    assert processes[0].terminated == 0
    assert not timers
    lease.wait_ready()


def test_health_loading_timeout_does_not_mark_ready(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    lease = runtime.acquire()
    session.health = Response(status=503)
    with pytest.raises(RuntimeStartupError, match="did not become audio-ready"):
        lease.wait_ready(timeout=0.01)
    assert not session.probes


def test_vulkan_explicit_and_close_only_kills_owned_child(runtime_factory, monkeypatch):
    monkeypatch.setenv("MTMD_BACKEND_DEVICE", "unrelated-gpu")
    monkeypatch.setenv("LLAMA_MTP_SKIP_STREAK_THRESHOLD", "7")
    runtime, processes, calls, timers, session = runtime_factory(accelerator="vulkan")
    lease = runtime.acquire()
    argv = calls[0][0][0]
    assert argv[argv.index("--device") + 1] == "Vulkan0"
    assert "--no-mmproj-offload" not in argv
    assert argv[argv.index("--mtp-head") + 1] == str(runtime.model_store.model_paths.assistant)
    assert argv[argv.index("--spec-type") + 1] == "mtp"
    assert argv[argv.index("--draft-block-size") + 1] == "3"
    assert argv[argv.index("--device-draft") + 1] == "Vulkan0"
    assert argv[argv.index("-ngld") + 1] == "99"
    assert calls[0][1]["env"]["LLAMA_MTP_SKIP_STREAK_THRESHOLD"] == "0"
    assert calls[0][1]["env"]["MTMD_BACKEND_DEVICE"] == "Vulkan0"
    processes[0].stubborn = True
    runtime.close()
    assert processes[0].terminated == 1
    assert processes[0].killed == 1
    lease.release()
    runtime.close()
    assert processes[0].terminated == 1
    with pytest.raises(RuntimeStartupError, match="closed"):
        runtime.acquire()


def test_missing_models_fail_before_process_start(runtime_factory):
    runtime, processes, calls, timers, session = runtime_factory()
    runtime.model_store.model_paths.model.unlink()
    with pytest.raises(RuntimeStartupError, match="download/verify"):
        runtime.acquire()
    assert not processes


@pytest.mark.parametrize("change", ["missing", "corrupt"])
def test_vulkan_requires_unchanged_verified_assistant_before_start(runtime_factory, change):
    runtime, processes, calls, timers, session = runtime_factory(accelerator="vulkan")
    assistant_path = runtime.model_store.model_paths.assistant
    if change == "missing":
        assistant_path.unlink()
    else:
        assistant_path.write_bytes(b"incorrect")
    with pytest.raises(RuntimeStartupError, match="download/verify"):
        runtime.acquire()
    assert not processes


def test_vulkan_cannot_start_with_a_cpu_only_model_store(runtime_factory):
    cpu, *_ = runtime_factory()
    vulkan, processes, *_ = runtime_factory(accelerator="vulkan", model_store=cpu.model_store)
    with pytest.raises(RuntimeStartupError, match="acceleration model is missing"):
        vulkan.acquire()
    assert not processes


@pytest.mark.parametrize("accelerator", ["cpu", "vulkan"])
def test_default_model_store_matches_runtime_profile(tmp_path, accelerator):
    runtime = ManagedRuntime(tmp_path, accelerator=accelerator, port=34917)
    expected = MODEL_ARTIFACTS + ((MTP_ASSISTANT,) if accelerator == "vulkan" else ())
    assert runtime.model_store.artifacts == expected
    runtime.close()


@pytest.mark.parametrize("architecture", ["x86_64", "amd64", "AMD64"])
@pytest.mark.parametrize("cpuinfo", ["flags : sse sse2\n", "flags : sse2 avx\n", ""])
def test_managed_host_accepts_x64_without_avx2(monkeypatch, architecture, cpuinfo):
    from voxd.runtime.managed import validate_managed_host
    monkeypatch.setattr("voxd.runtime.managed.sys.platform", "linux")
    monkeypatch.setattr("voxd.runtime.managed.platform.machine", lambda: architecture)
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: cpuinfo)
    validate_managed_host()


@pytest.mark.parametrize("os_name,architecture", [
    ("darwin", "x86_64"), ("win32", "amd64"), ("linux", "aarch64"),
    ("linux", "i686"),
])
def test_managed_host_rejects_unsupported_platform(monkeypatch, os_name, architecture):
    from voxd.runtime.managed import validate_managed_host
    monkeypatch.setattr("voxd.runtime.managed.sys.platform", os_name)
    monkeypatch.setattr("voxd.runtime.managed.platform.machine", lambda: architecture)
    with pytest.raises(RuntimeStartupError, match="Linux x86_64"):
        validate_managed_host()
