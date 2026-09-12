import os
import socket
import threading
import time

from voxd.paths import CONFIG_DIR

def _socket_path():
    return CONFIG_DIR / "voxd.sock"

# Minimum interval between accepted trigger_record messages (seconds).
# Prevents keyboard auto-repeat on the hotkey from spawning duplicate
# recording/transcription threads.
_DEBOUNCE_SEC = 0.5

def start_ipc_server(trigger_callback, settings_callback=None):
    """Starts a background thread that listens for 'trigger_record' and calls trigger_callback()."""
    sock_path = _socket_path()
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.25)
            probe.connect(str(sock_path))
        except OSError:
            sock_path.unlink()
        else:
            raise RuntimeError("another ThoughtLoud instance is already running")
        finally:
            probe.close()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    server.listen()

    last_trigger = 0.0
    lock = threading.Lock()

    def _serve_loop():
        nonlocal last_trigger
        while True:
            conn, _ = server.accept()
            try:
                conn.settimeout(1.0)
                try:
                    data = conn.recv(1024).strip()
                except OSError:
                    continue
                if data == b"show_settings" and settings_callback is not None:
                    settings_callback()
                elif data == b"trigger_record":
                    fire = False
                    with lock:
                        now = time.monotonic()
                        if now - last_trigger >= _DEBOUNCE_SEC:
                            last_trigger = now
                            fire = True
                    if fire:
                        trigger_callback()
            finally:
                conn.close()

    t = threading.Thread(target=_serve_loop, daemon=True)
    t.start()
