import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _installer_fixture(tmp_path):
    repo = Path(__file__).parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    systemctl_log = tmp_path / "systemctl.log"
    curl_log = tmp_path / "curl.log"

    _write_executable(
        fake_bin / "docker",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "if [ \"$1 $2\" = 'container inspect' ]; then\n"
        "  [ \"${FAKE_EXISTING_CONTAINER:-0}\" = 1 ] && exit 0\n"
        "  exit 1\n"
        "fi\n"
        "if [ \"$1\" = create ]; then\n"
        "  [ \"${FAKE_CREATE_FAIL:-0}\" = 1 ] && exit 125\n"
        "  printf '%s\\n' candidate-container-id\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = start ]; then\n"
        "  [ \"${FAKE_START_FAIL:-0}\" = 1 ] && exit 1\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1 $2\" = 'rm -f' ] && "
        "[ \"${FAKE_REMOVE_FAIL:-0}\" = 1 ]; then\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(
        fake_bin / "curl",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_CURL_LOG\"\n"
        "case \"$*\" in\n"
        "  *input_audio*)\n"
        "    [ \"${FAKE_AUDIO_FAIL:-0}\" = 1 ] && exit 22\n"
        "    if [ \"${FAKE_BAD_AUDIO_JSON:-0}\" = 1 ]; then\n"
        "      printf '%s\\n' '{\"unexpected\":true}'\n"
        "    else\n"
        "      printf '%s\\n' '{\"choices\":[{\"message\":{\"content\":\"\"}}]}'\n"
        "    fi\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
    )
    _write_executable(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_SYSTEMCTL_LOG\"\n"
        "if [ \"$*\" = '--user restart voxd-tray.service' ] && "
        "[ \"${FAKE_RESTART_FAIL:-0}\" = 1 ]; then\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(fake_bin / "python3", "#!/bin/sh\nexit 99\n")
    system_cp = shutil.which("cp")
    assert system_cp is not None
    _write_executable(
        fake_bin / "cp",
        "#!/bin/sh\n"
        "case \"${2:-}\" in\n"
        "  *.config.yaml.backup.*)\n"
        "    [ \"${FAKE_RESTORE_FAIL:-0}\" = 1 ] && exit 1\n"
        "    ;;\n"
        "esac\n"
        f'exec "{system_cp}" "$@"\n',
    )

    build_bin = tmp_path / "build/bin"
    build_bin.mkdir(parents=True)
    _write_executable(build_bin / "llama-server", "#!/bin/sh\nexit 0\n")
    (build_bin / "libllama.so").write_bytes(b"new runtime")
    llama_swap = tmp_path / "llama-swap"
    _write_executable(llama_swap, "#!/bin/sh\nexit 0\n")

    device_root = tmp_path / "dev/dri"
    device_root.mkdir(parents=True)
    (device_root / "renderD129").touch()
    data_home = tmp_path / "data"
    config_home = tmp_path / "config"
    voxd_config = config_home / "voxd/config.yaml"
    voxd_config.parent.mkdir(parents=True)
    voxd_config.write_text(
        "typing_delay: 2\n"
        "gemma_server_url: http://localhost:9292\n"
        "gemma_model: gemma-12b\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "DEVICE_ROOT": str(device_root),
            "XDG_DATA_HOME": str(data_home),
            "XDG_CONFIG_HOME": str(config_home),
            "FAKE_DOCKER_LOG": str(docker_log),
            "FAKE_SYSTEMCTL_LOG": str(systemctl_log),
            "FAKE_CURL_LOG": str(curl_log),
            "VOXD_PYTHON": sys.executable,
        }
    )
    return {
        "command": [
            "bash",
            str(repo / "runtime/igpu/install.sh"),
            str(build_bin),
            str(llama_swap),
        ],
        "env": env,
        "voxd_config": voxd_config,
        "docker_log": docker_log,
        "systemctl_log": systemctl_log,
        "curl_log": curl_log,
        "runtime_dir": data_home / "voxd/llama-vulkan",
        "runtime_config_dir": config_home / "voxd/igpu-runtime",
    }


def _run(fixture):
    return subprocess.run(
        fixture["command"],
        env=fixture["env"],
        text=True,
        capture_output=True,
    )


def test_fresh_install_validates_audio_and_configures_voxd(tmp_path):
    fixture = _installer_fixture(tmp_path)

    result = _run(fixture)

    assert result.returncode == 0, result.stderr
    configured = yaml.safe_load(fixture["voxd_config"].read_text(encoding="utf-8"))
    assert configured["gemma_server_url"] == "http://127.0.0.1:9394"
    assert configured["gemma_model"] == "gemma-e4b"
    assert fixture["runtime_dir"].is_dir()
    assert fixture["runtime_config_dir"].is_dir()
    docker_calls = fixture["docker_log"].read_text(encoding="utf-8")
    assert "create --name voxd-gemma-igpu" in docker_calls
    assert "start candidate-container-id" in docker_calls
    assert "rename" not in docker_calls
    curl_calls = fixture["curl_log"].read_text(encoding="utf-8")
    assert "/v1/chat/completions" in curl_calls
    assert "input_audio" in curl_calls
    assert "--user restart voxd-tray.service" in fixture[
        "systemctl_log"
    ].read_text(encoding="utf-8")


def test_existing_container_is_refused_without_mutation(tmp_path):
    fixture = _installer_fixture(tmp_path)
    original_config = fixture["voxd_config"].read_bytes()
    fixture["env"]["FAKE_EXISTING_CONTAINER"] = "1"

    result = _run(fixture)

    assert result.returncode != 0
    assert "refusing to modify it" in result.stderr
    assert fixture["voxd_config"].read_bytes() == original_config
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()
    assert "create --name" not in fixture["docker_log"].read_text(encoding="utf-8")


def test_missing_selected_voxd_python_is_refused_before_staging(tmp_path):
    fixture = _installer_fixture(tmp_path)
    fixture["env"]["VOXD_PYTHON"] = str(tmp_path / "missing-python")

    result = _run(fixture)

    assert result.returncode == 2
    assert "missing VOXD Python environment" in result.stderr
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()
    assert not fixture["docker_log"].exists()


def test_existing_managed_path_is_refused_without_mutation(tmp_path):
    fixture = _installer_fixture(tmp_path)
    fixture["runtime_dir"].mkdir(parents=True)
    sentinel = fixture["runtime_dir"] / "keep"
    sentinel.write_text("existing", encoding="utf-8")
    original_config = fixture["voxd_config"].read_bytes()

    result = _run(fixture)

    assert result.returncode != 0
    assert "installation path already exists" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "existing"
    assert fixture["voxd_config"].read_bytes() == original_config
    assert "create --name" not in fixture["docker_log"].read_text(encoding="utf-8")


def test_invalid_voxd_config_is_refused_before_staging(tmp_path):
    fixture = _installer_fixture(tmp_path)
    invalid = b"gemma_model: [\n"
    fixture["voxd_config"].write_bytes(invalid)

    result = _run(fixture)

    assert result.returncode != 0
    assert "could not parse existing VOXD config" in result.stderr
    assert fixture["voxd_config"].read_bytes() == invalid
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()
    assert "create --name" not in fixture["docker_log"].read_text(encoding="utf-8")


def test_staging_failure_removes_only_new_install_paths(tmp_path):
    fixture = _installer_fixture(tmp_path)
    (tmp_path / "build/bin/libllama.so").unlink()

    result = _run(fixture)

    assert result.returncode != 0
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()
    assert "create --name" not in fixture["docker_log"].read_text(encoding="utf-8")


def test_failure_never_removes_shared_parent_directories(tmp_path):
    fixture = _installer_fixture(tmp_path)
    fixture["voxd_config"].unlink()
    fixture["voxd_config"].parent.rmdir()
    (tmp_path / "build/bin/libllama.so").unlink()

    result = _run(fixture)

    assert result.returncode != 0
    assert fixture["runtime_dir"].parent.is_dir()
    assert fixture["runtime_config_dir"].parent.is_dir()
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()


def test_audio_failure_removes_owned_container_and_files(tmp_path):
    fixture = _installer_fixture(tmp_path)
    original_config = fixture["voxd_config"].read_bytes()
    fixture["env"]["FAKE_AUDIO_FAIL"] = "1"

    result = _run(fixture)

    assert result.returncode != 0
    docker_calls = fixture["docker_log"].read_text(encoding="utf-8")
    assert "rm -f candidate-container-id" in docker_calls
    assert fixture["voxd_config"].read_bytes() == original_config
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()


def test_optimized_python_still_rejects_invalid_smoke_response(tmp_path):
    fixture = _installer_fixture(tmp_path)
    original_config = fixture["voxd_config"].read_bytes()
    fixture["env"]["FAKE_BAD_AUDIO_JSON"] = "1"
    fixture["env"]["PYTHONOPTIMIZE"] = "1"

    result = _run(fixture)

    assert result.returncode != 0
    assert fixture["voxd_config"].read_bytes() == original_config
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()


def test_tray_restart_failure_restores_config_and_install_state(tmp_path):
    fixture = _installer_fixture(tmp_path)
    original_config = fixture["voxd_config"].read_bytes()
    fixture["env"]["FAKE_RESTART_FAIL"] = "1"

    result = _run(fixture)

    assert result.returncode != 0
    assert fixture["voxd_config"].read_bytes() == original_config
    assert not fixture["runtime_dir"].exists()
    assert not fixture["runtime_config_dir"].exists()
    assert fixture["systemctl_log"].read_text(encoding="utf-8").count(
        "--user restart voxd-tray.service"
    ) == 2


def test_restore_failure_retains_recovery_backup(tmp_path):
    fixture = _installer_fixture(tmp_path)
    original_config = fixture["voxd_config"].read_bytes()
    fixture["env"]["FAKE_RESTART_FAIL"] = "1"
    fixture["env"]["FAKE_RESTORE_FAIL"] = "1"

    result = _run(fixture)

    assert result.returncode != 0
    assert fixture["voxd_config"].read_bytes() != original_config
    backups = list(
        fixture["voxd_config"].parent.glob(".config.yaml.backup.*")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_config
    assert str(backups[0]) in result.stderr


def test_container_removal_failure_retains_its_bind_sources(tmp_path):
    fixture = _installer_fixture(tmp_path)
    fixture["env"]["FAKE_AUDIO_FAIL"] = "1"
    fixture["env"]["FAKE_REMOVE_FAIL"] = "1"

    result = _run(fixture)

    assert result.returncode != 0
    assert "retaining runtime files" in result.stderr
    assert fixture["runtime_dir"].is_dir()
    assert fixture["runtime_config_dir"].is_dir()
