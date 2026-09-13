import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ENTRYPOINT = Path(__file__).parents[1] / "runtime/igpu/container-entrypoint.sh"


def _fixture(tmp_path, minor=129):
    device_root = tmp_path / "dev/dri"
    device_root.mkdir(parents=True)
    runtime_device = device_root / "thoughtloud-igpu"
    runtime_device.touch()
    metadata = {str(runtime_device): f"character special file:e2:{minor:x}"}
    metadata_path = tmp_path / "device-metadata.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stat = fake_bin / "stat"
    stat.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "assert sys.argv[1:4] == ['-Lc', '%F:%t:%T', '--']\n"
        "try:\n"
        "    path = str(pathlib.Path(sys.argv[4]).resolve(strict=True))\n"
        "    metadata = json.loads(pathlib.Path(os.environ['FAKE_DEVICE_METADATA']).read_text())\n"
        "    print(metadata[path])\n"
        "except (OSError, KeyError):\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    stat.chmod(0o755)
    env = os.environ.copy()
    env.update(
        DEVICE_ROOT=str(device_root),
        FAKE_DEVICE_METADATA=str(metadata_path),
        PATH=f"{fake_bin}:{env['PATH']}",
    )
    return {
        "device_root": device_root,
        "runtime_device": runtime_device,
        "metadata": metadata,
        "metadata_path": metadata_path,
        "env": env,
        "minor": minor,
    }


def _run(fixture, args=None):
    fixture["metadata_path"].write_text(json.dumps(fixture["metadata"]), encoding="utf-8")
    if args is None:
        args = [sys.executable, "-c", "print('started')"]
    return subprocess.run(
        ["sh", str(ENTRYPOINT), *args],
        env=fixture["env"],
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize("minor", [128, 129, 255])
def test_canonical_render_link_uses_actual_minor_and_forwards_argv(tmp_path, minor):
    fixture = _fixture(tmp_path, minor)
    arguments = ["with spaces", "", "--flag=value", "$(must stay literal)", "line\nbreak"]

    result = _run(
        fixture,
        [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", *arguments],
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == arguments
    canonical = fixture["device_root"] / f"renderD{minor}"
    assert canonical.is_symlink()
    assert canonical.readlink() == Path("thoughtloud-igpu")
    assert canonical.resolve() == fixture["runtime_device"]
    assert len(list(fixture["device_root"].glob("renderD*"))) == 1


@pytest.mark.parametrize("existing_kind", ["alias_link", "same_character_device"])
def test_matching_existing_canonical_device_is_preserved(tmp_path, existing_kind):
    fixture = _fixture(tmp_path)
    canonical = fixture["device_root"] / "renderD129"
    if existing_kind == "alias_link":
        canonical.symlink_to("thoughtloud-igpu")
    else:
        canonical.touch()
        fixture["metadata"][str(canonical)] = "character special file:e2:81"
    inode = canonical.lstat().st_ino

    result = _run(fixture)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "started\n"
    assert canonical.lstat().st_ino == inode


@pytest.mark.parametrize("conflict", ["regular_file", "other_device", "wrong_major", "dangling_link"])
def test_conflicting_canonical_path_is_not_replaced_or_executed(tmp_path, conflict):
    fixture = _fixture(tmp_path)
    canonical = fixture["device_root"] / "renderD129"
    if conflict == "dangling_link":
        canonical.symlink_to("missing")
    else:
        canonical.touch()
        fixture["metadata"][str(canonical)] = {
            "regular_file": "regular empty file:0:0",
            "other_device": "character special file:e2:80",
            "wrong_major": "character special file:e3:81",
        }[conflict]
    inode = canonical.lstat().st_ino

    result = _run(fixture)

    assert result.returncode == 1
    assert "conflicting iGPU render path" in result.stderr
    assert not result.stdout
    assert canonical.lstat().st_ino == inode


@pytest.mark.parametrize(
    "device_metadata",
    [
        "regular empty file:0:0",
        "character special file:e3:81",
        "character special file:e2:7f",
        "character special file:e2:100",
        "character special file:e2:not-a-minor",
        "character special file:invalid:81",
    ],
)
def test_invalid_alias_device_is_refused_before_changes(tmp_path, device_metadata):
    fixture = _fixture(tmp_path)
    fixture["metadata"][str(fixture["runtime_device"])] = device_metadata

    result = _run(fixture)

    assert result.returncode == 1
    assert "invalid iGPU device" in result.stderr
    assert not result.stdout
    assert not list(fixture["device_root"].glob("renderD*"))


def test_missing_alias_is_refused_before_changes(tmp_path):
    fixture = _fixture(tmp_path)
    fixture["runtime_device"].unlink()

    result = _run(fixture)

    assert result.returncode == 1
    assert "invalid iGPU device" in result.stderr
    assert not result.stdout
    assert not list(fixture["device_root"].glob("renderD*"))


def test_command_exit_status_is_preserved(tmp_path):
    fixture = _fixture(tmp_path)

    result = _run(fixture, [sys.executable, "-c", "raise SystemExit(37)"])

    assert result.returncode == 37


def test_no_command_is_refused_before_changes(tmp_path):
    fixture = _fixture(tmp_path)

    result = _run(fixture, [])

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not list(fixture["device_root"].glob("renderD*"))
