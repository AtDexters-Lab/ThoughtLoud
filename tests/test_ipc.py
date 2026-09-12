import pytest


class _FakeThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        return None


class _FakeSocket:
    def __init__(self, path, *, connect_error=False):
        self.path = path
        self.connect_error = connect_error

    def settimeout(self, _timeout):
        return None

    def connect(self, _path):
        if self.connect_error:
            raise OSError("stale")

    def bind(self, _path):
        self.path.touch()

    def listen(self):
        return None

    def close(self):
        return None


def test_ipc_socket_is_private(monkeypatch):
    import voxd.utils.ipc_server as ipc_server

    path = ipc_server._socket_path()
    modes = []
    monkeypatch.setattr(
        ipc_server.socket,
        "socket",
        lambda *_args: _FakeSocket(path),
    )
    monkeypatch.setattr(ipc_server.threading, "Thread", _FakeThread)
    monkeypatch.setattr(ipc_server.os, "chmod", lambda target, mode: modes.append((target, mode)))

    ipc_server.start_ipc_server(lambda: None)

    assert modes == [(path, 0o600)]


def test_active_tray_socket_is_not_replaced(monkeypatch):
    import voxd.utils.ipc_server as ipc_server

    path = ipc_server._socket_path()
    path.touch()
    monkeypatch.setattr(ipc_server.socket, "socket", lambda *_args: _FakeSocket(path))

    with pytest.raises(RuntimeError, match="already running"):
        ipc_server.start_ipc_server(lambda: None)

    assert path.exists()


def test_stale_tray_socket_is_replaced(monkeypatch):
    import voxd.utils.ipc_server as ipc_server

    path = ipc_server._socket_path()
    path.touch()
    sockets = iter([_FakeSocket(path, connect_error=True), _FakeSocket(path)])
    monkeypatch.setattr(ipc_server.socket, "socket", lambda *_args: next(sockets))
    monkeypatch.setattr(ipc_server.threading, "Thread", _FakeThread)
    monkeypatch.setattr(ipc_server.os, "chmod", lambda *_args: None)

    ipc_server.start_ipc_server(lambda: None)

    assert path.exists()


def test_trigger_client_reports_delivery_failure(monkeypatch):
    import voxd.utils.ipc_client as ipc_client

    path = ipc_client._socket_path()
    monkeypatch.setattr(
        ipc_client.socket,
        "socket",
        lambda *_args: _FakeSocket(path, connect_error=True),
    )

    assert ipc_client.send_trigger() is False


def test_settings_client_uses_distinct_command(monkeypatch):
    import voxd.utils.ipc_client as ipc_client
    sent = []
    sock = _FakeSocket(ipc_client._socket_path())
    sock.sendall = sent.append
    monkeypatch.setattr(ipc_client.socket, "socket", lambda *_args: sock)
    assert ipc_client.send_settings()
    assert sent == [b"show_settings"]
