import importlib
import types

import pytest


def test_parse_bool_true_false(monkeypatch, tmp_path):
    # Isolate config directory and HOME
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("HOME", str(tmp_path))

    # Import module fresh
    import voxd.__main__ as main_mod
    importlib.reload(main_mod)

    assert main_mod._parse_bool("true") is True
    assert main_mod._parse_bool("True") is True
    assert main_mod._parse_bool("1") is True
    assert main_mod._parse_bool("yes") is True
    assert main_mod._parse_bool("on") is True

    assert main_mod._parse_bool("false") is False
    assert main_mod._parse_bool("False") is False
    assert main_mod._parse_bool("0") is False
    assert main_mod._parse_bool("no") is False
    assert main_mod._parse_bool("off") is False


def test_recording_archive_enable_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("HOME", str(tmp_path))

    import voxd.__main__ as main_mod
    import voxd.core.config as config_mod

    importlib.reload(config_mod)
    importlib.reload(main_mod)

    assert main_mod._handle_recording_archive("true") == 0
    assert config_mod.AppConfig().recording_archive_enabled is True

    assert main_mod._handle_recording_archive("false") == 0
    assert config_mod.AppConfig().recording_archive_enabled is False


@pytest.fixture
def autostart(monkeypatch, tmp_path):
    import voxd.__main__ as main_mod
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "custom-config"))
    monkeypatch.setattr(main_mod, "_voxd_command", lambda: "/usr/bin/voxd")
    return main_mod, tmp_path / "custom-config/autostart/voxd-tray.desktop"


@pytest.mark.parametrize("service_state", ["absent", "no-user-bus", "no-systemctl"])
def test_xdg_enable_disable_without_legacy_service(autostart, monkeypatch, service_state):
    main_mod, desktop = autostart
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert args == ["systemctl", "--user", "is-enabled", "voxd-tray.service"]
        if service_state == "no-systemctl":
            raise FileNotFoundError("systemctl")
        return types.SimpleNamespace(returncode=4 if service_state == "absent" else 1)
    monkeypatch.setattr(main_mod.subprocess, "run", run)
    assert main_mod._handle_autostart("true") == 0
    assert "Exec=/usr/bin/voxd --tray" in desktop.read_text()
    assert main_mod.AppConfig().autostart is True
    assert main_mod._handle_autostart("false") == 0
    assert not desktop.exists()
    assert main_mod.AppConfig().autostart is False
    assert len(calls) == 2


@pytest.mark.parametrize("enabled", [True, False])
def test_xdg_preference_applied_before_legacy_disable_without_stopping(autostart, monkeypatch, enabled):
    main_mod, desktop = autostart
    desktop.parent.mkdir(parents=True)
    desktop.write_text("old entry")
    calls = []
    legacy_enabled = True
    def run(args, **kwargs):
        nonlocal legacy_enabled
        calls.append(args)
        assert desktop.exists() is enabled
        if enabled:
            assert "Exec=/usr/bin/voxd --tray" in desktop.read_text()
        assert kwargs["timeout"] == 5
        if args == ["systemctl", "--user", "disable", "voxd-tray.service"]:
            legacy_enabled = False
            return types.SimpleNamespace(returncode=0)
        assert args == ["systemctl", "--user", "is-enabled", "voxd-tray.service"]
        return types.SimpleNamespace(returncode=0 if legacy_enabled else 1)
    monkeypatch.setattr(main_mod.subprocess, "run", run)
    assert main_mod._apply_autostart(enabled) == 0
    assert not legacy_enabled
    assert calls == [
        ["systemctl", "--user", "is-enabled", "voxd-tray.service"],
        ["systemctl", "--user", "disable", "voxd-tray.service"],
        ["systemctl", "--user", "is-enabled", "voxd-tray.service"],
    ]
    assert not (desktop.parents[1] / "systemd/user/voxd-tray.service").exists()


def test_failed_xdg_write_preserves_existing_entry_and_legacy_service(autostart, monkeypatch):
    main_mod, desktop = autostart
    desktop.parent.mkdir(parents=True)
    desktop.write_text("previous entry")
    calls = []
    monkeypatch.setattr(main_mod.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    def fail_replace(self, target):
        raise OSError("disk full")
    monkeypatch.setattr(main_mod.Path, "replace", fail_replace)
    assert main_mod._apply_autostart(True) == 1
    assert desktop.read_text() == "previous entry"
    assert list(desktop.parent.iterdir()) == [desktop]
    assert calls == []


def test_failed_xdg_removal_keeps_legacy_service_untouched(autostart, monkeypatch):
    main_mod, desktop = autostart
    desktop.parent.mkdir(parents=True)
    desktop.write_text("previous entry")
    calls = []
    monkeypatch.setattr(main_mod.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    def fail_unlink(self, **kwargs):
        raise PermissionError("read only")
    monkeypatch.setattr(main_mod.Path, "unlink", fail_unlink)
    assert main_mod._apply_autostart(False) == 1
    assert desktop.exists()
    assert calls == []


@pytest.mark.parametrize("failure", ["disable-error", "disable-timeout", "still-enabled"])
def test_known_enabled_service_failure_reports_incomplete_application(autostart, monkeypatch, capsys, failure):
    main_mod, desktop = autostart
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert desktop.exists()
        if args[2] == "disable":
            if failure == "disable-timeout":
                raise main_mod.subprocess.TimeoutExpired(args, 5)
            return types.SimpleNamespace(returncode=1 if failure == "disable-error" else 0)
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(main_mod.subprocess, "run", run)
    assert main_mod._apply_autostart(True) == 1
    assert "Incomplete" in capsys.readouterr().err
    assert all("--now" not in call for call in calls)
    assert all(call[2] in {"is-enabled", "disable"} for call in calls)


def test_xdg_path_defaults_to_home_config(autostart, monkeypatch, tmp_path):
    main_mod, _ = autostart
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert main_mod._xdg_autostart_path() == tmp_path / ".config/autostart/voxd-tray.desktop"
