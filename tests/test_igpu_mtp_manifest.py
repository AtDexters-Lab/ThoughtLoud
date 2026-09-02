import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_HELPER = Path(__file__).parents[1] / "runtime/igpu/mtp_build_manifest.py"
_SPEC = importlib.util.spec_from_file_location("mtp_build_manifest", _HELPER)
assert _SPEC is not None and _SPEC.loader is not None
mtp_build_manifest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mtp_build_manifest)


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    server = runtime / "llama-server"
    server.write_bytes(b"patched server")
    server.chmod(0o755)
    versioned = runtime / "libllama.so.1"
    versioned.write_bytes(b"linked library")
    (runtime / "libllama.so").symlink_to(versioned.name)
    return runtime


def _inputs(tmp_path: Path, monkeypatch):
    runtime = _runtime(tmp_path)
    patch = tmp_path / "atomic.patch"
    patch.write_bytes(b"tested patch")
    head = tmp_path / mtp_build_manifest.MTP_HEAD_NAME
    head.write_bytes(b"validated test assistant")
    monkeypatch.setattr(
        mtp_build_manifest,
        "MTP_HEAD_SHA256",
        hashlib.sha256(head.read_bytes()).hexdigest(),
    )
    manifest = tmp_path / "manifest.json"
    mtp_build_manifest.create_manifest(runtime, patch, manifest)
    return runtime, patch, head, manifest


def test_manifest_create_verify_and_stage(tmp_path, monkeypatch):
    runtime, patch, head, manifest = _inputs(tmp_path, monkeypatch)

    payload = mtp_build_manifest.verify_manifest(runtime, patch, manifest, head)
    destination = tmp_path / "staged"
    destination.mkdir()
    mtp_build_manifest.stage_runtime(runtime, destination, patch, manifest, head)

    assert payload["schema"] == 2
    assert payload["source_commit"] == "0a635dcd92ba66c75fccfef91c3e106f4668f367"
    assert payload["cmake_options"][0] == "GGML_VULKAN=ON"
    assert payload["mtp_head"]["sha256"] == hashlib.sha256(head.read_bytes()).hexdigest()
    assert payload["runtime_files"]["libllama.so"] == {
        "type": "symlink",
        "target": "libllama.so.1",
    }
    assert (destination / "libllama.so").readlink() == Path("libllama.so.1")
    assert (destination / mtp_build_manifest.MTP_HEAD_NAME).stat().st_mode & 0o777 == 0o600
    assert json.loads((destination / manifest.name).read_text()) == payload
    mtp_build_manifest.verify_manifest(
        destination,
        patch,
        destination / manifest.name,
        destination / mtp_build_manifest.MTP_HEAD_NAME,
    )


def test_manifest_rejects_server_changed_after_build(tmp_path, monkeypatch):
    runtime, patch, head, manifest = _inputs(tmp_path, monkeypatch)
    (runtime / "llama-server").write_bytes(b"different server")

    with pytest.raises(SystemExit, match="manifest does not match"):
        mtp_build_manifest.verify_manifest(runtime, patch, manifest, head)


def test_manifest_rejects_library_changed_or_added_after_build(tmp_path, monkeypatch):
    runtime, patch, head, manifest = _inputs(tmp_path, monkeypatch)
    (runtime / "libllama.so.1").write_bytes(b"different library")

    with pytest.raises(SystemExit, match="manifest does not match"):
        mtp_build_manifest.verify_manifest(runtime, patch, manifest, head)

    (runtime / "libllama.so.1").write_bytes(b"linked library")
    (runtime / "libextra.so").write_bytes(b"unexpected library")
    with pytest.raises(SystemExit, match="manifest does not match"):
        mtp_build_manifest.verify_manifest(runtime, patch, manifest, head)


def test_manifest_rejects_different_mtp_head(tmp_path, monkeypatch):
    runtime, patch, head, manifest = _inputs(tmp_path, monkeypatch)
    head.write_bytes(b"different assistant")

    with pytest.raises(SystemExit, match="validated artifact"):
        mtp_build_manifest.verify_manifest(runtime, patch, manifest, head)
