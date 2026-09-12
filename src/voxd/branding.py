"""Public identity and narrow compatibility updates for generated launchers."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

APP_NAME = "ThoughtLoud"
ICON_NAME = "thoughtloud"
DESKTOP_ID = "voxd-tray"  # Existing desktop/portal registrations remain valid.
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def command_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        renamed = executable.with_name("thoughtloud")
        return [str(renamed if renamed.exists() else executable)]
    invoked = Path(sys.argv[0])
    if invoked.name == "thoughtloud" and invoked.exists():
        return [str(invoked.resolve())]
    executable = shutil.which("thoughtloud")
    if executable:
        return [executable]
    if invoked.name == "voxd" and invoked.exists():
        return [str(invoked.resolve())]
    executable = shutil.which("voxd")
    return [executable] if executable else [sys.executable, "-m", "voxd"]


def command_text() -> str:
    """Encode the command for a desktop file's Exec field only."""
    def quote(value):
        value = value.replace("%", "%%")
        if re.fullmatch(r"[A-Za-z0-9_./:%+=,@-]+", value):
            return value
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return '"' + escaped.replace("\\", "\\\\") + '"'
    return " ".join(quote(arg) for arg in command_argv())


def shell_command_text(*arguments: str) -> str:
    """Quote a command for manual shortcuts parsed by a shell or GLib."""
    return shlex.join(command_argv() + list(arguments))


def _xdg_root(variable: str, fallback: str) -> Path:
    path = Path(os.environ.get(variable) or Path.home() / fallback)
    return path if path.is_absolute() else Path.home() / fallback


def desktop_entry_path() -> Path:
    return _xdg_root("XDG_DATA_HOME", ".local/share") / "applications" / f"{DESKTOP_ID}.desktop"


def autostart_path() -> Path:
    return _xdg_root("XDG_CONFIG_HOME", ".config") / "autostart" / f"{DESKTOP_ID}.desktop"


def install_user_icon() -> None:
    path = _xdg_root("XDG_DATA_HOME", ".local/share") / "icons/hicolor/scalable/apps/thoughtloud.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(files("voxd").joinpath("assets", "thoughtloud.svg").read_bytes())


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".thoughtloud-", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _recognized_legacy(content: str, *, autostart: bool) -> bool:
    lines = content.splitlines()
    if not lines or lines[0] != "[Desktop Entry]":
        return False
    fields = {}
    for line in lines[1:]:
        if not line or "=" not in line:
            return False
        key, value = line.split("=", 1)
        if key in fields:
            return False
        fields[key] = value
    if fields.get("Type") != "Application" or fields.get("Name") != "VOXD":
        return False
    if autostart:
        required = {"Type", "Name", "Exec", "X-GNOME-Autostart-enabled"}
        if not required <= fields.keys() or fields.keys() - required - {"Icon", "Hidden"}:
            return False
        if fields["X-GNOME-Autostart-enabled"] not in {"true", "false"}:
            return False
        if fields.get("Hidden", "false") not in {"true", "false"} or fields.get("Icon", "voxd") != "voxd":
            return False
    else:
        required = {"Type", "Name", "Comment", "Exec", "Icon", "Terminal", "Categories"}
        if fields.keys() != required:
            return False
        if (fields["Comment"] != "E4B voice typing" or fields["Icon"] != "voxd"
                or fields["Terminal"] != "false" or fields["Categories"] != "Utility;AudioVideo;"):
            return False
    known_commands = {"voxd", "/usr/bin/voxd", "/usr/local/bin/voxd", "/opt/voxd/voxd",
                      str(Path.home() / ".local/bin/voxd"), f"{sys.executable} -m voxd"}
    legacy = shutil.which("voxd")
    if legacy:
        known_commands.add(legacy)
    invoked = Path(sys.argv[0])
    if invoked.name == "voxd" and invoked.exists():
        known_commands.add(str(invoked.resolve()))
    modes = ["--tray"] if autostart else ["--tray", "--settings"]
    return fields["Exec"] in {f"{command} {mode}" for command in known_commands for mode in modes}


def _refresh_generated_entry(path: Path, *, autostart: bool) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    content = path.read_text(encoding="utf-8")
    if path.is_symlink() or not _recognized_legacy(content, autostart=autostart):
        if "Name=VOXD" in content or path.is_symlink():
            print(f"[branding] Preserved customized launcher {path}. Update its Name/Icon "
                  "to ThoughtLoud/thoughtloud and its command manually if desired.", file=sys.stderr)
        return False
    install_user_icon()
    mode = "--tray" if autostart else "--settings"
    lines = []
    for line in content.splitlines():
        if line.startswith("Name="):
            line = f"Name={APP_NAME}"
        elif line.startswith("Icon="):
            line = f"Icon={ICON_NAME}"
        elif line.startswith("Exec="):
            line = f"Exec={command_text()} {mode}"
        lines.append(line)
    if not any(line.startswith("Icon=") for line in lines):
        lines.append(f"Icon={ICON_NAME}")
    _atomic_write(path, "\n".join(lines) + "\n")
    return True


def migrate_generated_launchers() -> None:
    """Refresh existing metadata only; never enable startup or touch services."""
    for path, is_autostart in [(desktop_entry_path(), False), (autostart_path(), True)]:
        try:
            _refresh_generated_entry(path, autostart=is_autostart)
        except (OSError, UnicodeError) as exc:
            print(f"[branding] Could not refresh {path}: {exc}", file=sys.stderr)


def install_desktop_entry() -> None:
    install_user_icon()
    path = desktop_entry_path()
    if path.exists() or path.is_symlink():
        _refresh_generated_entry(path, autostart=False)
        return
    _atomic_write(path, "[Desktop Entry]\nType=Application\n"
                  f"Name={APP_NAME}\nComment=Long, natural voice dictation to your computer\n"
                  f"Exec={command_text()} --settings\nIcon={ICON_NAME}\n"
                  "Terminal=false\nCategories=Utility;AudioVideo;\n")
