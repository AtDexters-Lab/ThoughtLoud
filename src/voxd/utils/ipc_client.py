import socket

from voxd.paths import CONFIG_DIR


def _socket_path():
    return CONFIG_DIR / "voxd.sock"

def send_trigger() -> bool:
    """Connect to the running app and send 'trigger_record'."""
    return _send(b"trigger_record", report_error=True)

def send_settings() -> bool:
    """Show settings in an existing instance, if any."""
    return _send(b"show_settings", report_error=False)

def _send(message: bytes, *, report_error: bool) -> bool:
    path = str(_socket_path())
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(1.0)
        sock.connect(path)
        sock.sendall(message)
        return True
    except Exception as e:
        if report_error:
            print(f"[IPC] Could not send trigger: {e}")
        return False
    finally:
        sock.close()
