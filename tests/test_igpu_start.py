import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).parents[1] / "runtime/igpu/start-container.sh"

def fixture(tmp_path, node="renderD129", pci="0000:c8:00.0"):
    dev = tmp_path / "dev/dri"
    sys = tmp_path / "sys"
    dev.mkdir(parents=True)
    add_gpu(dev, sys, node, pci)
    (dev / "thoughtloud-igpu").symlink_to(node)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    log = tmp_path / "docker.log"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'if [ "$1" = inspect ]; then printf "%s\\n" "$MAPPING"; fi\n'
    )
    docker.chmod(0o755)
    env = os.environ | {
        "DEVICE_ROOT": str(dev), "SYSFS_ROOT": str(sys),
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "DOCKER_LOG": str(log),
        "MAPPING": f"1|{dev}/thoughtloud-igpu:/dev/dri/thoughtloud-igpu:rwm;",
    }
    return dev, sys, env, log


def add_gpu(dev, sys, node, pci, device_id="0x1900", target="/dev/null"):
    device = sys / "devices" / pci
    device.mkdir(parents=True)
    (device / "vendor").write_text("0x1002\n")
    (device / "device").write_text(device_id + "\n")
    drm = sys / "class/drm" / node
    drm.mkdir(parents=True)
    (drm / "device").symlink_to(device)
    (dev / node).symlink_to(target)


def run(env, name="voxd-gemma-igpu"):
    return subprocess.run(["bash", str(SCRIPT), name], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("node,pci", [("renderD128", "0000:c6:00.0"), ("renderD129", "0000:c8:00.0")])
def test_boot_start_resolves_hardware_identity_after_both_numbers_change(tmp_path, node, pci):
    dev, sys, env, log = fixture(tmp_path, node, pci)
    add_gpu(dev, sys, "renderD130", "0000:03:00.0", "0x7551", "/dev/zero")
    result = run(env, "custom-igpu")
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[-1] == "start custom-igpu"


def test_multiple_matching_gpus_block_boot_start(tmp_path):
    dev, sys, env, log = fixture(tmp_path)
    add_gpu(dev, sys, "renderD130", "0000:c9:00.0")
    result = run(env)
    assert result.returncode != 0
    assert "found 2" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("failure", ["missing", "wrong_gpu", "not_device"])
def test_invalid_alias_blocks_boot_start(tmp_path, failure):
    dev, sys, env, log = fixture(tmp_path)
    alias = dev / "thoughtloud-igpu"
    alias.unlink()
    if failure == "wrong_gpu":
        add_gpu(dev, sys, "renderD128", "0000:03:00.0", "0x7551", "/dev/zero")
        alias.symlink_to("renderD128")
    elif failure == "not_device":
        alias.write_text("not a render node")
    result = run(env)
    assert result.returncode != 0
    assert "missing or mismatched" in result.stderr
    assert not log.exists()


def test_old_container_mapping_is_not_started(tmp_path):
    _, _, env, log = fixture(tmp_path)
    env["MAPPING"] = "1|/dev/dri/by-path/pci-0000:c6:00.0-render:/dev/dri/renderD128:rwm;"
    result = run(env)
    assert result.returncode != 0
    assert "explicit migration required" in result.stderr
    assert len(log.read_text().splitlines()) == 1


def test_container_name_cannot_be_a_docker_option(tmp_path):
    _, _, env, log = fixture(tmp_path)
    result = run(env, "--all")
    assert result.returncode == 2
    assert not log.exists()
