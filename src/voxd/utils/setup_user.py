from __future__ import annotations

import os
import re
import shutil
import subprocess
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from voxd.core.config import AppConfig, CONFIG_PATH
from voxd.branding import install_desktop_entry


def _run(command: list[str], *, timeout: int = 15) -> bool:
    try:
        result = subprocess.run(command, check=False, timeout=timeout)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _typing_service_name() -> str:
    name = os.environ.get("VOXD_YDOTOOL_SERVICE", "ydotoold.service")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\.service", name):
        raise ValueError("Invalid typing service name.")
    return name


def _unit_arg(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def _install_user_ydotool_unit() -> bool:
    service = _typing_service_name()
    if (Path("/usr/lib/systemd/user") / service).exists():
        return True

    daemon = shutil.which("ydotoold")
    if not daemon:
        return False
    # ydotoold 0.1.8 ignores even --help and unconditionally opens a fixed /tmp
    # socket. Inspect its capabilities without executing a potentially old daemon.
    try:
        if b"socket-path" not in Path(daemon).read_bytes():
            return False
    except OSError:
        return False

    unit_dir = Path.home() / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    command = _unit_arg(daemon)
    socket_arg = _unit_arg(os.environ["YDOTOOL_SOCKET"]) if os.environ.get("YDOTOOL_SOCKET") else "%h/.ydotool_socket"
    command += f" --socket-path={socket_arg} --socket-own=%U:%G"
    (unit_dir / service).write_text(
        "[Unit]\n"
        "Description=ydotool user daemon\n"
        "After=default.target\n\n"
        "[Service]\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=1s\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    return True


def _install_desktop_entry() -> None:
    install_desktop_entry()


@dataclass(frozen=True)
class SetupResult:
    success: bool
    message: str


def _socket_ready(path: Path) -> bool:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.2)
        sock.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def prepare_user_setup() -> SetupResult:
    """Prepare the user's typing service without invoking the injector itself."""
    _install_desktop_entry()
    client = shutil.which("ydotool")
    if not client:
        return SetupResult(False, "Text insertion is unavailable: ydotool is missing. Reinstall the complete desktop package.")
    if not _install_user_ydotool_unit():
        return SetupResult(False, "A compatible ydotool daemon is missing. Install the complete desktop package; older 0.1.8 daemons cannot use the private socket.")
    if not shutil.which("systemctl"):
        return SetupResult(False, "Automatic typing setup needs a systemd user session. See the Linux installation guide for manual desktop setup.")
    if not _run(["systemctl", "--user", "daemon-reload"], timeout=5):
        return SetupResult(False, "Could not contact the systemd user session. Sign in to a desktop session and retry typing setup.")
    if not _run(["systemctl", "--user", "enable", "--now", _typing_service_name()], timeout=10):
        return SetupResult(False, "Typing service could not start. Check /dev/uinput access and the ydotoold user service, then retry setup.")
    path = Path(os.environ.get("YDOTOOL_SOCKET", str(Path.home() / ".ydotool_socket")))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if _socket_ready(path):
            return SetupResult(True, "Typing service is running. Test actual insertion with a short dictation into an editor.")
        time.sleep(0.1)
    return SetupResult(False, "Typing service has no accessible private socket. Check /dev/uinput access, then retry setup.")


def run_user_setup(verbose: bool = False) -> None:
    """Prepare desktop integration; model acquisition belongs to the setup UI."""
    cfg = AppConfig()
    cfg.save()
    result = prepare_user_setup()
    print(f"[setup] config: {CONFIG_PATH}")
    print(f"[setup] {result.message}")
    if verbose:
        print(f"[setup] E4B endpoint: {cfg.gemma_server_url}")
