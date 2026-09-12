from pathlib import Path

import pytest


def test_typing_setup_starts_only_user_daemon_and_does_not_send_input(monkeypatch):
    from voxd.utils import setup_user
    monkeypatch.delenv("VOXD_YDOTOOL_SERVICE", raising=False)
    calls = []
    monkeypatch.setattr(setup_user, "_install_desktop_entry", lambda: None)
    monkeypatch.setattr(setup_user, "_install_user_ydotool_unit", lambda: True)
    monkeypatch.setattr(setup_user.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(setup_user, "_run", lambda command, **kwargs: calls.append(command) or True)
    monkeypatch.setattr(setup_user, "_socket_ready", lambda path: True)
    result = setup_user.prepare_user_setup()
    assert result.success
    assert calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "ydotoold.service"]]
    assert "Test actual insertion" in result.message


@pytest.mark.parametrize("missing", ["ydotool", "systemctl"])
def test_typing_setup_reports_missing_dependency_without_starting(monkeypatch, missing):
    from voxd.utils import setup_user
    monkeypatch.setattr(setup_user, "_install_desktop_entry", lambda: None)
    monkeypatch.setattr(setup_user, "_install_user_ydotool_unit", lambda: True)
    monkeypatch.setattr(setup_user.shutil, "which", lambda name: None if name == missing else name)
    monkeypatch.setattr(setup_user, "_run", lambda *_args, **_kwargs: pytest.fail("must not start service"))
    assert not setup_user.prepare_user_setup().success


def test_old_daemon_is_inspected_without_executing_help(monkeypatch, tmp_path):
    from voxd.utils import setup_user
    old_daemon = tmp_path / "ydotoold"
    old_daemon.write_bytes(b"old daemon /tmp/.ydotool_socket")
    original_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: False if str(path) == "/usr/lib/systemd/user/ydotoold.service" else original_exists(path))
    monkeypatch.setattr(setup_user.shutil, "which", lambda name: str(old_daemon))
    monkeypatch.setattr(setup_user.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("old daemon ignores help and starts"))
    assert not setup_user._install_user_ydotool_unit()


def test_compatible_daemon_gets_private_user_socket(monkeypatch, tmp_path):
    from voxd.utils import setup_user
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    monkeypatch.delenv("VOXD_YDOTOOL_SERVICE", raising=False)
    daemon = tmp_path / "ydotoold"
    daemon.write_bytes(b"modern daemon socket-path socket-own")
    original_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: False if str(path) == "/usr/lib/systemd/user/ydotoold.service" else original_exists(path))
    monkeypatch.setattr(setup_user.shutil, "which", lambda name: str(daemon))
    assert setup_user._install_user_ydotool_unit()
    unit = (tmp_path / ".config/systemd/user/ydotoold.service").read_text()
    assert "--socket-path=%h/.ydotool_socket" in unit
    assert "--socket-own=%U:%G" in unit


def test_packaged_setup_uses_its_own_service_and_socket(monkeypatch, tmp_path):
    from voxd.utils import setup_user
    calls, paths = [], []
    monkeypatch.setenv("VOXD_YDOTOOL_SERVICE", "voxd-ydotoold.service")
    monkeypatch.setenv("YDOTOOL_SOCKET", str(tmp_path / ".voxd_ydotool_socket"))
    monkeypatch.setattr(setup_user, "_install_desktop_entry", lambda: None)
    monkeypatch.setattr(setup_user, "_install_user_ydotool_unit", lambda: True)
    monkeypatch.setattr(setup_user.shutil, "which", lambda name: name)
    monkeypatch.setattr(setup_user, "_run", lambda command, **kwargs: calls.append(command) or True)
    monkeypatch.setattr(setup_user, "_socket_ready", lambda path: paths.append(path) or True)
    assert setup_user.prepare_user_setup().success
    assert calls[-1][-1] == "voxd-ydotoold.service"
    assert paths == [tmp_path / ".voxd_ydotool_socket"]
