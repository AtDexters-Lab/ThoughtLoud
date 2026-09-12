import importlib.metadata
import sys
from pathlib import Path

import pytest


@pytest.fixture
def branding(monkeypatch, tmp_path):
    from voxd import branding
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(branding, "command_text", lambda: "/usr/bin/thoughtloud")
    monkeypatch.setattr(branding.shutil, "which", lambda name: None)
    return branding


def legacy_desktop(command="/usr/bin/voxd --tray"):
    return ("[Desktop Entry]\nType=Application\nName=VOXD\nComment=E4B voice typing\n"
            f"Exec={command}\nIcon=voxd\nTerminal=false\nCategories=Utility;AudioVideo;\n")


@pytest.mark.parametrize("mode", ["--tray", "--settings"])
def test_generated_user_launcher_is_rebranded_with_compatible_id(branding, mode):
    path = branding.desktop_entry_path()
    path.parent.mkdir(parents=True)
    path.write_text(legacy_desktop(f"/usr/bin/voxd {mode}"))
    branding.migrate_generated_launchers()
    content = path.read_text()
    assert path.name == "voxd-tray.desktop"
    assert "Name=ThoughtLoud\n" in content and "Icon=thoughtloud\n" in content
    assert "Exec=/usr/bin/thoughtloud --settings\n" in content
    assert not branding.autostart_path().exists()
    assert (path.parents[1] / "icons/hicolor/scalable/apps/thoughtloud.svg").exists()


@pytest.mark.parametrize("enabled,hidden", [("true", None), ("false", None), ("true", "true"), ("false", "false")])
def test_autostart_metadata_refresh_preserves_enabled_and_hidden_flags(branding, enabled, hidden):
    path = branding.autostart_path()
    path.parent.mkdir(parents=True)
    content = ("[Desktop Entry]\nType=Application\nName=VOXD\nExec=/usr/bin/voxd --tray\n"
               f"X-GNOME-Autostart-enabled={enabled}\n")
    if hidden is not None:
        content += f"Hidden={hidden}\n"
    path.write_text(content)
    branding.migrate_generated_launchers()
    updated = path.read_text()
    assert "Name=ThoughtLoud\n" in updated and "Icon=thoughtloud\n" in updated
    assert "Exec=/usr/bin/thoughtloud --tray\n" in updated
    assert f"X-GNOME-Autostart-enabled={enabled}\n" in updated
    assert (f"Hidden={hidden}\n" in updated) if hidden is not None else "Hidden=" not in updated
    assert not branding.desktop_entry_path().exists()


@pytest.mark.parametrize("custom", [
    legacy_desktop("/custom/wrapper --tray"),
    legacy_desktop() + "OnlyShowIn=GNOME;\n",
    legacy_desktop().replace("Icon=voxd", "Icon=custom"),
    legacy_desktop().replace("Exec=/usr/bin/voxd --tray", "Exec=/usr/bin/voxd --tray --custom"),
])
def test_customized_launchers_are_preserved_with_guidance(branding, custom, capsys):
    path = branding.desktop_entry_path()
    path.parent.mkdir(parents=True)
    path.write_text(custom)
    branding.migrate_generated_launchers()
    assert path.read_text() == custom
    assert "Preserved customized launcher" in capsys.readouterr().err
    # Running user setup must also preserve this customization.
    branding.install_desktop_entry()
    assert path.read_text() == custom


def test_startup_migration_does_not_create_missing_entries_or_change_config(branding):
    from voxd.core.config import AppConfig, CONFIG_PATH
    cfg = AppConfig()
    cfg.set("autostart", True)
    cfg.save()
    original = CONFIG_PATH.read_bytes()
    branding.migrate_generated_launchers()
    assert not branding.desktop_entry_path().exists()
    assert not branding.autostart_path().exists()
    assert CONFIG_PATH.read_bytes() == original


def test_symlink_launcher_target_is_untouched(branding, tmp_path, capsys):
    target = tmp_path / "custom.desktop"
    target.write_text(legacy_desktop())
    path = branding.desktop_entry_path()
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    branding.migrate_generated_launchers()
    assert path.is_symlink()
    assert target.read_text() == legacy_desktop()
    assert "Preserved customized launcher" in capsys.readouterr().err


def test_setup_installs_new_name_and_svg_under_old_desktop_id(branding):
    branding.install_desktop_entry()
    content = branding.desktop_entry_path().read_text()
    assert "Name=ThoughtLoud\n" in content
    assert "Icon=thoughtloud\n" in content
    assert "Exec=/usr/bin/thoughtloud --settings\n" in content
    assert not branding.autostart_path().exists()


def test_command_prefers_thoughtloud_over_legacy_invocation(monkeypatch, tmp_path):
    from voxd import branding
    legacy = tmp_path / "voxd"
    legacy.touch()
    monkeypatch.setattr(sys, "argv", [str(legacy)])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(branding.shutil, "which", lambda name: "/usr/bin/thoughtloud" if name == "thoughtloud" else str(legacy))
    assert branding.command_argv() == ["/usr/bin/thoughtloud"]


def test_command_legacy_alias_remains_usable(monkeypatch, tmp_path):
    from voxd import branding
    legacy = tmp_path / "voxd"
    legacy.touch()
    monkeypatch.setattr(sys, "argv", [str(legacy)])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(branding.shutil, "which", lambda name: None)
    assert branding.command_argv() == [str(legacy)]


def test_frozen_command_resolves_public_executable_from_legacy_alias(monkeypatch, tmp_path):
    from voxd import branding
    executable = tmp_path / "thoughtloud"
    executable.touch()
    legacy = tmp_path / "voxd"
    legacy.symlink_to(executable)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(legacy))
    assert branding.command_argv() == [str(executable)]


@pytest.mark.parametrize("legacy_only", [False, True])
def test_version_prefers_new_distribution_with_legacy_fallback(monkeypatch, legacy_only):
    import voxd.__main__ as main
    calls = []
    def version(name):
        calls.append(name)
        if legacy_only and name == "thoughtloud":
            raise importlib.metadata.PackageNotFoundError(name)
        return "1.4.0"
    monkeypatch.setattr(main.importlib.metadata, "version", version)
    assert main._get_version() == "1.4.0"
    assert calls == (["thoughtloud", "voxd"] if legacy_only else ["thoughtloud"])


def test_both_cli_names_present_public_help(monkeypatch, capsys):
    import voxd.__main__ as main
    for executable in ("thoughtloud", "voxd"):
        monkeypatch.setattr(sys, "argv", [executable, "--help"])
        with pytest.raises(SystemExit) as exit:
            main.main()
        assert exit.value.code == 0
        output = capsys.readouterr().out
        assert "usage: thoughtloud" in output and "ThoughtLoud" in output
        assert "VOXD" not in output


def test_original_svg_states_are_valid_and_referenced():
    QSvgRenderer = pytest.importorskip("PyQt6.QtSvg").QSvgRenderer
    from voxd.branding import ASSETS_DIR
    from voxd.tray import tray_main
    names = ["thoughtloud.png"] + tray_main._RECORDING_ICONS + tray_main._WORKING_ICONS
    assert len(names) == 9
    for name in names:
        path = (ASSETS_DIR / name).with_suffix(".svg")
        assert name.startswith("thoughtloud") and QSvgRenderer(str(path)).isValid()
        assert "SPDX-License-Identifier: MIT" in path.read_text()


def test_failed_metadata_replace_preserves_original_launcher(branding, monkeypatch, capsys):
    path = branding.desktop_entry_path()
    path.parent.mkdir(parents=True)
    original = legacy_desktop()
    path.write_text(original)
    def fail_replace(self, target):
        raise OSError("Read-only launchers")
    monkeypatch.setattr(Path, "replace", fail_replace)
    branding.migrate_generated_launchers()
    assert path.read_text() == original
    assert list(path.parent.iterdir()) == [path]
    assert "Could not refresh" in capsys.readouterr().err


def test_gui_startup_only_refreshes_metadata_without_applying_autostart(monkeypatch):
    import voxd.__main__ as main
    import voxd.tray.tray_main as tray_main
    events = []
    monkeypatch.setattr(sys, "argv", ["thoughtloud"])
    monkeypatch.setattr(main, "migrate_generated_launchers", lambda: events.append("metadata"))
    monkeypatch.setattr(main, "_apply_autostart", lambda *_: pytest.fail("startup must not reapply preferences"))
    monkeypatch.setattr(tray_main, "main", lambda: events.append("tray"))
    main.main()
    assert events == ["metadata", "tray"]


@pytest.mark.parametrize("executable", [
    "/tmp/50%/thoughtloud",
    "/tmp/$PATH/thoughtloud",
    '/tmp/with "double" and \'single\' quotes/thoughtloud',
    "/tmp/back\\slash/thoughtloud",
    "/tmp/with spaces/thoughtloud",
])
def test_manual_shortcut_roundtrips_literal_executable_and_trigger(monkeypatch, executable):
    import shlex
    from voxd import branding
    monkeypatch.setattr(branding, "command_argv", lambda: [executable])
    assert shlex.split(branding.shell_command_text("--trigger-record")) == [executable, "--trigger-record"]


def test_desktop_and_manual_shortcuts_use_separate_percent_encoding(monkeypatch):
    import shlex
    from voxd import branding
    executable = "/tmp/50%/thoughtloud"
    monkeypatch.setattr(branding, "command_argv", lambda: [executable])
    assert branding.command_text() == "/tmp/50%%/thoughtloud"
    assert shlex.split(branding.shell_command_text("--trigger-record")) == [executable, "--trigger-record"]
