"""Observe ydotool's virtual keyboard without grabbing or changing the device."""

from __future__ import annotations

import fcntl
import os
import select
import struct
import time
from pathlib import Path


_DEVICE_NAME = "ydotoold virtual device"
_EVENT = struct.Struct("@llHHi")
_KEY_BYTES = 96  # Linux KEY_CNT == 768, including mouse buttons.
_HOLD_LIMIT = 0.25  # Only used with ydotool's explicit 1/5 ms key hold.
_QUIET_PERIOD = 0.05
_SETTLE_TIMEOUT = 1.0


class KeyStateError(RuntimeError):
    pass


def _read_ioctl(number: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord("E") << 8) | number


class KeyboardState:
    def __init__(self, fd: int):
        self.fd = fd
        self.down_since: dict[int, float] = {}
        self.last_activity = time.monotonic()

    @classmethod
    def open(cls, socket_path: Path) -> KeyboardState:
        # Device names cannot associate arbitrary custom sockets with daemons.
        if socket_path not in {
            Path.home() / ".ydotool_socket",
            Path.home() / ".voxd_ydotool_socket",
        }:
            raise OSError("custom ydotool socket has no verified input-device mapping")
        candidates = []
        for entry in Path("/sys/class/input").glob("event*"):
            try:
                if (entry / "device/name").read_text().strip() == _DEVICE_NAME:
                    candidates.append(Path("/dev/input") / entry.name)
            except OSError:
                continue
        if len(candidates) != 1:
            raise OSError("expected one identifiable ydotool virtual keyboard")

        fd = os.open(candidates[0], os.O_RDONLY | os.O_NONBLOCK)
        try:
            # Recheck the opened device, including virtual bus/vendor/product,
            # rather than trusting a potentially stale sysfs enumeration.
            name = bytearray(256)
            fcntl.ioctl(fd, _read_ioctl(0x06, len(name)), name, True)
            identity = bytearray(8)
            fcntl.ioctl(fd, _read_ioctl(0x02, len(identity)), identity, True)
            bus, vendor, product, _version = struct.unpack("@HHHH", identity)
            if (name.split(b"\0", 1)[0] != _DEVICE_NAME.encode()
                    or (bus, vendor, product) != (0x06, 0x2333, 0x6666)):
                raise OSError("input device is not the expected ydotool virtual keyboard")
            monitor = cls(fd)
            monitor.pressed_keys()  # Verify ioctl access before starting typing.
            return monitor
        except BaseException:
            os.close(fd)
            raise

    def pressed_keys(self) -> set[int]:
        bitmap = bytearray(_KEY_BYTES)
        try:
            fcntl.ioctl(self.fd, _read_ioctl(0x18, len(bitmap)), bitmap, True)
        except OSError as exc:
            raise KeyStateError("could not read ydotool keyboard state") from exc
        # Exclude mouse buttons; VOXD only owns keyboard typing here.
        return {key for key in range(1, 256) if bitmap[key // 8] & (1 << (key % 8))}

    def drain(self) -> bool:
        """Consume transitions so repeated typing of one key is not a long hold."""
        activity = False
        for _ in range(64):
            try:
                data = os.read(self.fd, _EVENT.size * 256)
            except BlockingIOError:
                return activity
            except OSError as exc:
                raise KeyStateError("lost the ydotool keyboard event stream") from exc
            if not data or len(data) % _EVENT.size:
                raise KeyStateError("invalid ydotool keyboard event stream")
            activity = True
            now = time.monotonic()
            self.last_activity = now
            for _sec, _usec, kind, code, value in _EVENT.iter_unpack(data):
                if kind == 0 and code == 3:  # SYN_DROPPED: history is incomplete.
                    raise KeyStateError("ydotool keyboard event observation overflowed")
                if kind == 1 and 0 < code < 256:
                    if value == 0:
                        self.down_since.pop(code, None)
                    elif value == 1:
                        self.down_since.setdefault(code, now)
                    # Autorepeat (value 2) must not reset a held key's age.
        raise KeyStateError("ydotool keyboard event observation could not keep up")

    def stuck_keys(self) -> set[int]:
        self.drain()
        now = time.monotonic()
        if not any(now - since >= _HOLD_LIMIT for since in self.down_since.values()):
            return set()
        pressed = self.pressed_keys()
        # A release may have arrived while reading the bitmap. Drain again and
        # recheck age so a new press of the same character is not misclassified.
        self.drain()
        now = time.monotonic()
        return {key for key, since in self.down_since.items()
                if key in pressed and now - since >= _HOLD_LIMIT}

    def wait(self, timeout: float) -> None:
        # Wake on input instead of sleeping through a burst of characters: an
        # evdev queue can overflow in less than a 20 ms subprocess wait.
        try:
            select.select([self.fd], [], [], timeout)
        except (OSError, ValueError) as exc:
            raise KeyStateError("lost the ydotool keyboard event stream") from exc

    def settle(self, *, released: bool) -> set[int]:
        """Bounded quiet-state observation, not a daemon acknowledgement.

        Used once at completion, or before/after recovery; never per character
        or healthy chunk. Events arriving during the window restart it.
        """
        deadline = time.monotonic() + _SETTLE_TIMEOUT
        quiet_since = time.monotonic()
        previous = self.pressed_keys()
        while True:
            activity = self.drain()
            pressed = self.pressed_keys()
            now = time.monotonic()
            if activity or pressed != previous:
                quiet_since = now
            # stuck_keys() also drains, including events arriving between the
            # first drain and its bitmap check. Count that activity too.
            quiet_since = max(quiet_since, self.last_activity)
            previous = pressed
            if now - quiet_since >= _QUIET_PERIOD and (not released or not pressed):
                return pressed
            if released and self.stuck_keys():
                raise KeyStateError("ydotool key remained held after typing")
            if now >= deadline:
                raise KeyStateError("ydotool keyboard did not reach the expected quiet state")
            self.wait(min(0.01, deadline - now))

    def close(self) -> None:
        os.close(self.fd)
