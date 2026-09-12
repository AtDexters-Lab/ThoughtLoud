import asyncio
from types import SimpleNamespace

import pytest
from dbus_next import Message, Variant


def test_portal_uses_current_session_and_displays_actual_binding():
    from voxd.platforms.linux.shortcuts import PortalShortcuts, INTERFACE, PATH
    portal = PortalShortcuts()
    portal.session = "/session/current"
    activations, changes = [], []
    portal.activated.connect(lambda: activations.append(True))
    portal.changed.connect(changes.append)
    for session in ("/session/old", "/session/current"):
        portal._message(Message.new_signal(PATH, INTERFACE, "Activated", "osta{sv}", [session, "toggle-recording", 1, {}]))
    portal._message(Message.new_signal(PATH, INTERFACE, "ShortcutsChanged", "oa(sa{sv})", [portal.session, [["toggle-recording", {"trigger_description": Variant("s", "Meta+Space")}]]]))
    assert activations == [True]
    assert changes == ["Active shortcut: Meta+Space"]
    portal.close()
    portal._message(Message.new_signal(PATH, INTERFACE, "Activated", "osta{sv}", [portal.session, "toggle-recording", 2, {}]))
    assert activations == [True]


@pytest.mark.parametrize("code", [0, 1, 2])
def test_fast_portal_response_is_subscribed_before_request(monkeypatch, code):
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    portal = PortalShortcuts()
    portal.bus = SimpleNamespace(unique_name=":1.42")
    async def call(member, signature, body):
        path = next(iter(portal.requests))
        portal._message(Message.new_signal(path, "org.freedesktop.portal.Request", "Response", "ua{sv}", [code, {"session_handle": Variant("s", "/session/new")}]))
        return [path]
    monkeypatch.setattr(portal, "_call", call)
    async def check():
        if code:
            with pytest.raises(RuntimeError, match="cancelled|denied"):
                await portal._request("CreateSession", "a{sv}", [{}])
        else:
            result = await portal._request("CreateSession", "a{sv}", [{}])
            assert result["session_handle"].value == "/session/new"
        assert portal.requests == {}
    asyncio.run(check())


def test_unsupported_reconfigure_does_not_drop_working_session(monkeypatch):
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    portal = PortalShortcuts()
    portal.session = "/session/active"
    portal.bus = object()
    calls, changes = [], []
    portal.changed.connect(changes.append)
    async def call(member, *_args, **_kwargs):
        calls.append(member)
        raise RuntimeError("org.freedesktop.DBus.Error.UnknownMethod")
    monkeypatch.setattr(portal, "_call", call)
    asyncio.run(portal._configure())
    assert calls == ["ConfigureShortcuts"]
    assert portal.session == "/session/active"
    assert "existing shortcut remains active" in changes[-1]
    assert "org.freedesktop" not in changes[-1]


@pytest.mark.parametrize("error", ["org.freedesktop.DBus.Error.UnknownObject", "org.freedesktop.DBus.Error.AccessDenied: Invalid session"])
def test_lost_portal_session_is_recreated_after_restart(monkeypatch, error):
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    portal = PortalShortcuts()
    portal.session = "/session/stale"
    portal.bus = SimpleNamespace(disconnect=lambda: None)
    calls = []
    async def call(member, *_args, **_kwargs):
        calls.append(member)
        raise RuntimeError(error)
    async def request(member, *_args):
        calls.append(member)
        if member == "CreateSession":
            return {"session_handle": Variant("s", "/session/new")}
        return {"shortcuts": Variant("a(sa{sv})", [["toggle-recording", {"trigger_description": Variant("s", "Meta+Space")} ]])}
    async def connect():
        portal.bus = object()
    monkeypatch.setattr(portal, "_connect", connect)
    monkeypatch.setattr(portal, "_call", call)
    monkeypatch.setattr(portal, "_request", request)
    asyncio.run(portal._configure())
    assert calls == ["ConfigureShortcuts", "CreateSession", "BindShortcuts"]
    assert portal.session == "/session/new"


@pytest.mark.parametrize("registry_available", [True, False])
def test_registers_raw_dbus_connection_before_portal_calls(monkeypatch, registry_available):
    from dbus_next import MessageType
    import dbus_next.aio
    from voxd.platforms.linux.shortcuts import PortalShortcuts, APP_ID
    portal = PortalShortcuts()
    calls = []
    class Bus:
        async def connect(self): return self
        def add_message_handler(self, _handler): pass
        async def call(self, message):
            calls.append(message)
            if message.member == "Register" and not registry_available:
                return SimpleNamespace(message_type=MessageType.ERROR,
                    error_name="org.freedesktop.DBus.Error.UnknownMethod", body=["not implemented"])
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[])
    monkeypatch.setattr(dbus_next.aio, "MessageBus", Bus)
    asyncio.run(portal._connect())
    assert [message.member for message in calls] == ["Register", "AddMatch"]
    assert calls[0].body == [APP_ID, {}]
    assert calls[0].interface == "org.freedesktop.host.portal.Registry"


@pytest.mark.parametrize("error,expected", [
    ("org.freedesktop.DBus.Error.UnknownMethod: No such interface org.freedesktop.portal.GlobalShortcuts", "does not support"),
    ("org.freedesktop.DBus.Error.AccessDenied: rejected", "did not allow"),
    ("Shortcut configuration cancelled", "was cancelled"),
    ("Shortcut configuration timed out", "timed out"),
    ("org.freedesktop.portal.Error.Failed: internal detail", "Could not configure"),
])
def test_portal_error_ui_is_actionable_without_raw_dbus_details(monkeypatch, capsys, error, expected):
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    portal = PortalShortcuts()
    portal.bus = object()
    changes = []
    portal.changed.connect(changes.append)
    async def request(*_args):
        raise RuntimeError(error)
    monkeypatch.setattr(portal, "_request", request)
    asyncio.run(portal._configure())
    assert expected in changes[-1]
    assert "desktop shortcut instructions" in changes[-1]
    assert "org.freedesktop" not in changes[-1]
    assert "GlobalShortcuts" not in changes[-1]
    assert error in capsys.readouterr().out
