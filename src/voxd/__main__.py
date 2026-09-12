from __future__ import annotations

import argparse
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from voxd.core.config import AppConfig
from voxd.branding import APP_NAME, ICON_NAME, autostart_path, command_text, migrate_generated_launchers


def _configure_bundled_paths() -> None:
    """Use package-owned input helpers even when launched without the wrapper."""
    if not getattr(sys, "frozen", False):
        return
    helpers = str(Path(sys.executable).resolve().parent / "libexec")
    os.environ["PATH"] = helpers + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("VOXD_YDOTOOL_SERVICE", "voxd-ydotoold.service")
    os.environ.setdefault("YDOTOOL_SOCKET", str(Path.home() / ".voxd_ydotool_socket"))


def _parse_bool(value: str) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"1", "true", "on", "yes", "y"}:
        return True
    if normalized in {"0", "false", "off", "no", "n"}:
        return False
    raise ValueError(f"expected true/false, got: {value}")


def _get_version() -> str:
    for distribution in ("thoughtloud", "voxd"):
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            pass
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.startswith("version = "):
                return line.split('"', 2)[1]
    return "unknown"


def _voxd_command() -> str:
    """Compatibility import for existing settings and integrations."""
    return command_text()


def _xdg_autostart_path() -> Path:
    return autostart_path()


def _set_xdg_autostart(enabled: bool) -> bool:
    path = _xdg_autostart_path()
    temporary = None
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".voxd-tray-", delete=False) as output:
                temporary = Path(output.name)
                output.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={APP_NAME}\n"
                    f"Icon={ICON_NAME}\n"
                    f"Exec={_voxd_command()} --tray\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
            temporary.replace(path)
        else:
            path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        print(f"[autostart] Could not update {path}: {exc}", file=sys.stderr)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _handle_autostart(value: str) -> int:
    enabled = _parse_bool(value)
    cfg = AppConfig()
    cfg.set("autostart", enabled)
    cfg.save()

    return _apply_autostart(enabled)


def _apply_autostart(enabled: bool) -> int:
    """Change login configuration without starting/stopping the running app."""
    # Install the desktop-session route before retiring the old service route.
    if not _set_xdg_autostart(enabled):
        return 1
    command = ["systemctl", "--user"]
    options = dict(check=False, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        legacy = subprocess.run(command + ["is-enabled", "voxd-tray.service"], **options)
    except FileNotFoundError:
        legacy = None  # Non-systemd desktops are supported.
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[autostart] Could not check the previous login service: {exc}", file=sys.stderr)
        return 1
    if legacy is not None and legacy.returncode == 0:
        try:
            result = subprocess.run(command + ["disable", "voxd-tray.service"], **options)
            if result.returncode != 0:
                raise RuntimeError("systemctl disable failed")
            result = subprocess.run(command + ["is-enabled", "voxd-tray.service"], **options)
            if result.returncode == 0:
                raise RuntimeError("the service is still enabled")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"[autostart] Incomplete: the previous voxd-tray.service login route "
                  f"could not be disabled ({exc}). It may still start at login.", file=sys.stderr)
            return 1
    print(f"[autostart] {'enabled' if enabled else 'disabled'} (XDG desktop entry)")
    return 0


def _handle_recording_archive(value: str) -> int:
    enabled = _parse_bool(value)
    cfg = AppConfig()
    cfg.set("recording_archive_enabled", enabled)
    cfg.save()
    print(f"[archive] {'enabled' if enabled else 'disabled'}")
    return 0


def _diagnose(cfg: AppConfig) -> int:
    import requests

    print(f"E4B endpoint: {cfg.gemma_server_url}")
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(f"{cfg.gemma_server_url.rstrip('/')}/v1/models", timeout=5)
        print(f"E4B service: {'ok' if response.ok else f'HTTP {response.status_code}'}")
    except requests.RequestException as exc:
        print(f"E4B service: unavailable ({exc})")

    socket_path = Path(os.environ.get("YDOTOOL_SOCKET", str(Path.home() / ".ydotool_socket")))
    print(f"ydotool: {shutil.which('ydotool') or 'missing'}")
    print(f"ydotool socket: {'ok' if socket_path.exists() else 'missing'} ({socket_path})")
    print(
        f"recording archive: {'enabled' if cfg.recording_archive_enabled else 'disabled'} "
        f"({cfg.recording_archive_max_mb} MB max, ffmpeg={shutil.which('ffmpeg') or 'missing'})"
    )

    try:
        import sounddevice as sd

        device = sd.query_devices(kind="input")
        print(f"audio input: {device.get('name')} @ {device.get('default_samplerate')} Hz")
    except Exception as exc:
        print(f"audio input: unavailable ({exc})")
    return 0


def main() -> None:
    _configure_bundled_paths()
    parser = argparse.ArgumentParser(prog="thoughtloud", description="ThoughtLoud — long, natural voice dictation")
    parser.add_argument("--tray", action="store_true", help="run the tray service (default)")
    parser.add_argument("--trigger-record", action="store_true", help="toggle recording in the tray")
    parser.add_argument("--settings", action="store_true", help="open setup and settings")
    parser.add_argument("--setup", action="store_true", help="configure ydotool and desktop integration")
    parser.add_argument("--autostart", metavar="BOOL", help="enable or disable tray autostart")
    parser.add_argument(
        "--archive-recordings",
        metavar="BOOL",
        help="enable or disable private FLAC recording history",
    )
    parser.add_argument("--diagnose", action="store_true", help="check E4B, ydotool, and audio")
    parser.add_argument("--version", action="store_true", help="print the installed version")
    args = parser.parse_args()

    if args.version:
        print(_get_version())
        return
    if args.trigger_record:
        from voxd.utils.ipc_client import send_trigger

        raise SystemExit(0 if send_trigger() else 1)
    if args.autostart is not None:
        raise SystemExit(_handle_autostart(args.autostart))
    if args.archive_recordings is not None:
        raise SystemExit(_handle_recording_archive(args.archive_recordings))
    if args.setup:
        from voxd.utils.setup_user import run_user_setup

        run_user_setup()
        return

    if not args.diagnose:
        migrate_generated_launchers()

    if args.settings:
        from voxd.utils.ipc_client import send_settings
        if send_settings():
            return

    cfg = AppConfig()
    if args.diagnose:
        raise SystemExit(_diagnose(cfg))

    print("ThoughtLoud: E4B transcription + ydotool typing", flush=True)
    from voxd.tray.tray_main import main as tray_main

    if args.settings:
        tray_main(show_settings=True)
    else:
        tray_main()


if __name__ == "__main__":
    main()
