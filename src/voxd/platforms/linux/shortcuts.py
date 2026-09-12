"""Optional XDG GlobalShortcuts portal connection, owned for the app lifetime."""
from __future__ import annotations

import asyncio
import threading
import uuid

from PyQt6.QtCore import QObject, pyqtSignal


SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
APP_ID = "voxd-tray"


class PortalError(RuntimeError):
    def __init__(self, name, detail=""):
        self.name = name
        super().__init__(f"{name}: {detail}" if detail else name)



def _shortcut_error_message(error, *, has_active_session=False):
    detail = str(error)
    fallback = "Use the desktop shortcut instructions below."
    if "UnknownMethod" in detail or "UnknownInterface" in detail:
        if has_active_session:
            return "This desktop cannot change the hotkey from this app. Your existing shortcut remains active; change it in your desktop keyboard settings."
        return "This desktop does not support hotkey setup from this app. " + fallback
    if "cancelled" in detail.lower():
        return "Hotkey setup was cancelled. Choose Configure hotkey to try again, or use the desktop shortcut instructions below."
    if "denied" in detail.lower() or "NotAllowed" in detail:
        return "Your desktop did not allow hotkey setup. " + fallback
    if isinstance(error, asyncio.TimeoutError) or "timed out" in detail.lower():
        return "Hotkey setup timed out. Try again, or use the desktop shortcut instructions below."
    return "Could not configure a hotkey with your desktop. Try again, or use the desktop shortcut instructions below."


class PortalShortcuts(QObject):
    activated = pyqtSignal()
    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = None
        self.bus = None
        self.session = None
        self.pending = None
        self.closed = False
        self.worker = None
        self.requests = {}

    def configure(self):
        if self.closed or (self.pending is not None and not self.pending.done()):
            return
        if self.worker is None:
            self.worker = threading.Thread(target=self._run, daemon=True, name="voxd-shortcuts")
            self.worker.start()
        elif self.loop is not None:
            self.pending = asyncio.run_coroutine_threadsafe(self._configure(), self.loop)

    def _run(self):
        if self.closed:
            return
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.pending = self.loop.create_task(self._configure())
        try:
            self.loop.run_forever()
        finally:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(self.loop), return_exceptions=True))
            self.loop.close()

    async def _call(self, member, signature="", body=None, *, path=PATH, interface=INTERFACE):
        from dbus_next import Message, MessageType
        response = await asyncio.wait_for(self.bus.call(Message(
            destination=SERVICE, path=path, interface=interface, member=member,
            signature=signature, body=body or [],
        )), timeout=5)
        if response.message_type == MessageType.ERROR:
            raise PortalError(response.error_name or "Portal request failed", " ".join(str(item) for item in response.body))
        return response.body

    async def _request(self, member, signature, body):
        from dbus_next import Variant
        token = "voxd_" + uuid.uuid4().hex
        body[-1]["handle_token"] = Variant("s", token)
        sender = self.bus.unique_name[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        future = asyncio.get_running_loop().create_future()
        self.requests[path] = future
        try:
            result = await self._call(member, signature, body)
            # Current portals use our handle token. Handle legacy returned paths too.
            returned = result[0]
            self.requests[returned] = future
            code, values = await asyncio.wait_for(future, timeout=60)
            if code != 0:
                raise RuntimeError("Shortcut configuration cancelled" if code == 1 else "Shortcut request denied")
            return values
        except asyncio.TimeoutError:
            await self._call("Close", path=path, interface="org.freedesktop.portal.Request")
            raise RuntimeError("Shortcut configuration timed out")
        finally:
            self.requests = {key: value for key, value in self.requests.items() if value is not future}

    def _message(self, message):
        from dbus_next import MessageType
        if self.closed or message.message_type != MessageType.SIGNAL:
            return
        if message.interface == "org.freedesktop.portal.Request" and message.member == "Response":
            future = self.requests.get(message.path)
            if future is not None and not future.done():
                future.set_result(message.body)
        elif message.interface == INTERFACE:
            if not message.body or message.body[0] != self.session:
                return
            if message.member == "Activated" and message.body[1] == "toggle-recording":
                self.activated.emit()
            elif message.member == "ShortcutsChanged":
                self._show_binding(message.body[1])
        elif message.interface == "org.freedesktop.portal.Session" and message.member == "Closed":
            if message.path == self.session:
                self.session = None
                self.changed.emit("Shortcut session closed. Configure it again or use a desktop shortcut.")

    def _show_binding(self, shortcuts):
        for key, values in shortcuts:
            if key == "toggle-recording":
                description = values.get("trigger_description")
                self.changed.emit(f"Active shortcut: {description.value if description else 'assigned by your desktop'}")
                return
        self.changed.emit("No shortcut assigned. Use Configure hotkey or the desktop shortcut instructions.")

    async def _connect(self):
        from dbus_next import Message, MessageType
        from dbus_next.aio import MessageBus
        self.bus = await asyncio.wait_for(MessageBus().connect(), timeout=5)
        try:
            self.bus.add_message_handler(self._message)
            # This is a separate peer from Qt's bus: the portal must know its app ID
            # before any portal call. Older portals may not implement the Registry.
            try:
                await self._call("Register", "sa{sv}", [APP_ID, {}],
                                 interface="org.freedesktop.host.portal.Registry")
            except PortalError as exc:
                if exc.name not in {"org.freedesktop.DBus.Error.UnknownMethod", "org.freedesktop.DBus.Error.UnknownInterface"}:
                    raise
            response = await asyncio.wait_for(self.bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                body=[f"type='signal',sender='{SERVICE}'"],
            )), timeout=5)
            if response.message_type == MessageType.ERROR:
                raise RuntimeError("Could not subscribe to desktop shortcuts")
        except Exception:
            self.bus.disconnect()
            self.bus = None
            raise

    async def _configure(self):
        existing_session = self.session
        try:
            from dbus_next import Variant
            if self.bus is None:
                await self._connect()
            if self.closed:
                return
            if self.session:
                await self._call("ConfigureShortcuts", "osa{sv}", [self.session, "", {}])
                return
            self.changed.emit("Waiting for desktop hotkey configuration…")
            values = await self._request("CreateSession", "a{sv}", [{
                "session_handle_token": Variant("s", "voxd_" + uuid.uuid4().hex),
            }])
            self.session = values["session_handle"].value
            values = await self._request("BindShortcuts", "oa(sa{sv})sa{sv}", [
                self.session, [["toggle-recording", {"description": Variant("s", "Start or stop dictation")}]], "", {},
            ])
            self._show_binding(values["shortcuts"].value)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if existing_session and (any(name in str(exc) for name in (
                "org.freedesktop.DBus.Error.UnknownObject",
                "org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.NameHasNoOwner",
            )) or ("org.freedesktop.DBus.Error.AccessDenied" in str(exc) and "Invalid session" in str(exc))):
                # A restarted portal no longer owns this session. Recreate it;
                # UnknownMethod on a healthy version-1 portal remains harmless.
                self.session = None
                self.bus.disconnect()
                self.bus = None
                await self._configure()
                return
            print(f"[shortcuts] {type(exc).__name__}: {exc}", flush=True)
            self.changed.emit(_shortcut_error_message(exc, has_active_session=bool(existing_session)))
            if self.session and self.bus and not existing_session:
                try:
                    await self._call("Close", path=self.session, interface="org.freedesktop.portal.Session")
                except Exception:
                    pass
                self.session = None

    def close(self):
        self.closed = True
        if self.loop is not None and not self.loop.is_closed():
            def stop():
                if self.bus is not None:
                    self.bus.disconnect()
                self.loop.stop()
            self.loop.call_soon_threadsafe(stop)
