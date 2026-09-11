import errno
import struct
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def load_keyboard_module(isolate_xdg_dirs):
    # conftest adds src/ to sys.path in its per-test isolation fixture.
    global keyboard
    import voxd.core.keyboard_state as keyboard


class Clock:
    def __init__(self, monkeypatch):
        self.now = 0.0
        monkeypatch.setattr(keyboard.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(keyboard.time, "sleep", self.advance)
        monkeypatch.setattr(keyboard.select, "select", lambda _r, _w, _e, timeout: self.advance(timeout))

    def advance(self, seconds):
        self.now += seconds


def _event(key, value, kind=1):
    return struct.pack("@llHHi", 0, 0, kind, key, value)


def _reader(monkeypatch, batches):
    pending = iter(batches)

    def read(_fd, _length):
        item = next(pending, BlockingIOError())
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(keyboard.os, "read", read)


def test_key_bitmap_excludes_mouse_buttons_and_reserved_key(monkeypatch):
    def ioctl(fd, request, bitmap, mutate):
        assert fd == 12
        assert request == keyboard._read_ioctl(0x18, 96)
        assert mutate is True
        for code in (0, 30, 42, 125, 255, 256, 272):
            bitmap[code // 8] |= 1 << (code % 8)

    monkeypatch.setattr(keyboard.fcntl, "ioctl", ioctl)
    assert keyboard.KeyboardState(12).pressed_keys() == {30, 42, 125, 255}


def test_key_bitmap_failure_is_observation_error(monkeypatch):
    def ioctl(*_args):
        raise OSError(errno.ENODEV, "gone")

    monkeypatch.setattr(keyboard.fcntl, "ioctl", ioctl)
    with pytest.raises(keyboard.KeyStateError, match="read.*state"):
        keyboard.KeyboardState(12).pressed_keys()


def test_repeated_character_transitions_do_not_accumulate_hold_age(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    _reader(monkeypatch, [_event(30, 1), BlockingIOError()])
    monitor.drain()
    clock.advance(0.3)
    _reader(monkeypatch, [_event(30, 0) + _event(30, 1), BlockingIOError()])
    monkeypatch.setattr(monitor, "pressed_keys", lambda: pytest.fail("fresh press needs no ioctl"))
    assert monitor.stuck_keys() == set()
    assert monitor.down_since == {30: clock.now}


def test_shift_with_letter_and_normal_releases_are_not_stuck(monkeypatch):
    Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    _reader(monkeypatch, [
        _event(42, 1) + _event(30, 1) + _event(30, 0) + _event(42, 0),
        BlockingIOError(),
    ])
    assert monitor.stuck_keys() == set()
    assert monitor.down_since == {}


def test_autorepeat_and_duplicate_down_do_not_renew_hold_age(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    _reader(monkeypatch, [_event(30, 1), BlockingIOError()])
    monitor.drain()
    clock.advance(0.3)
    _reader(monkeypatch, [_event(30, 2) + _event(30, 1), BlockingIOError()])
    monkeypatch.setattr(monitor, "pressed_keys", lambda: {30})
    assert monitor.stuck_keys() == {30}
    assert monitor.down_since[30] == 0


def test_stale_down_event_requires_current_pressed_confirmation(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    monitor.down_since[30] = 0
    clock.advance(0.3)
    _reader(monkeypatch, [])
    monkeypatch.setattr(monitor, "pressed_keys", lambda: set())
    assert monitor.stuck_keys() == set()


def test_release_and_repress_during_ioctl_does_not_misclassify_new_press(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    monitor.down_since[30] = 0
    clock.advance(0.3)
    _reader(monkeypatch, [
        BlockingIOError(),
        _event(30, 0) + _event(30, 1),
        BlockingIOError(),
    ])
    monkeypatch.setattr(monitor, "pressed_keys", lambda: {30})
    assert monitor.stuck_keys() == set()
    assert monitor.down_since[30] == clock.now


@pytest.mark.parametrize("data", [
    b"", b"incomplete", _event(3, 0, kind=0), OSError(errno.ENODEV, "gone"),
])
def test_lost_or_incomplete_event_history_aborts_observation(monkeypatch, data):
    _reader(monkeypatch, [data])
    with pytest.raises(keyboard.KeyStateError):
        keyboard.KeyboardState(12).drain()


def test_event_flood_fails_within_a_bounded_number_of_reads(monkeypatch):
    reads = []

    def read(_fd, _length):
        reads.append(1)
        return _event(30, 1) + _event(30, 0)

    monkeypatch.setattr(keyboard.os, "read", read)
    with pytest.raises(keyboard.KeyStateError, match="could not keep up"):
        keyboard.KeyboardState(12).drain()
    assert len(reads) < 100


def test_monitor_wait_uses_event_readiness(monkeypatch):
    calls = []
    monkeypatch.setattr(keyboard.select, "select", lambda *args: calls.append(args))
    keyboard.KeyboardState(12).wait(0.02)
    assert calls == [([12], [], [], 0.02)]


@pytest.mark.parametrize("error", [OSError(errno.ENODEV, "gone"), ValueError("closed")])
def test_monitor_wait_failure_is_observation_loss(monkeypatch, error):
    def select(*_args):
        raise error

    monkeypatch.setattr(keyboard.select, "select", select)
    with pytest.raises(keyboard.KeyStateError, match="lost.*event stream"):
        keyboard.KeyboardState(12).wait(0.02)


def test_settle_waits_for_late_release_and_then_a_quiet_window(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    last_phase = [0]

    def phase():
        return 0 if clock.now < 0.03 else 1 if clock.now < 0.08 else 2

    def drain():
        current = phase()
        changed = current != last_phase[0]
        last_phase[0] = current
        return changed

    monkeypatch.setattr(monitor, "drain", drain)
    monkeypatch.setattr(monitor, "pressed_keys", lambda: {30} if phase() == 1 else set())
    assert monitor.settle(released=True) == set()
    assert 0.13 <= clock.now <= 0.16


def test_settle_counts_late_event_pair_consumed_by_secondary_drain(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    reads_per_tick = {}
    delivered = []

    def read(_fd, _length):
        tick = round(clock.now * 100)
        reads_per_tick[tick] = reads_per_tick.get(tick, 0) + 1
        # settle's first drain sees no events. The press/release pair arrives
        # during stuck_keys' subsequent drain, without changing the bitmap.
        if tick == 4 and reads_per_tick[tick] == 2:
            delivered.append(clock.now)
            return _event(30, 1) + _event(30, 0)
        raise BlockingIOError()

    monkeypatch.setattr(keyboard.os, "read", read)
    monkeypatch.setattr(monitor, "pressed_keys", lambda: set())

    assert monitor.settle(released=True) == set()
    assert delivered == [pytest.approx(0.04)]
    assert clock.now >= 0.09
    assert clock.now - delivered[0] >= 0.05


def test_settle_can_report_quiet_held_keys_for_targeted_recovery(monkeypatch):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    monkeypatch.setattr(monitor, "drain", lambda: False)
    monkeypatch.setattr(monitor, "pressed_keys", lambda: {30, 42})
    assert monitor.settle(released=False) == {30, 42}
    assert 0.05 <= clock.now < 0.08


@pytest.mark.parametrize("activity,pressed", [(False, {30}), (True, set())])
def test_settle_is_bounded_when_held_or_never_quiet(monkeypatch, activity, pressed):
    clock = Clock(monkeypatch)
    monitor = keyboard.KeyboardState(12)
    monkeypatch.setattr(monitor, "drain", lambda: activity)
    monkeypatch.setattr(monitor, "pressed_keys", lambda: pressed)
    with pytest.raises(keyboard.KeyStateError, match="quiet state"):
        monitor.settle(released=True)
    assert 1 <= clock.now < 1.02


def _discovery(monkeypatch, tmp_path, *, count=1, identity=(0x06, 0x2333, 0x6666, 1), name=None):
    entries = []
    for index in range(count):
        entry = tmp_path / f"event{index}"
        (entry / "device").mkdir(parents=True)
        (entry / "device/name").write_text(keyboard._DEVICE_NAME)
        entries.append(entry)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Path, "glob", lambda _self, pattern: iter(entries))
    opened, closed = [], []

    def open_device(path, flags):
        opened.append((path, flags))
        return 12

    def ioctl(fd, request, buffer, mutate):
        number = request & 0xFF
        if number == 0x06:
            encoded = (name or keyboard._DEVICE_NAME).encode()
            buffer[:len(encoded)] = encoded
        elif number == 0x02:
            buffer[:] = struct.pack("@HHHH", *identity)
        elif number != 0x18:
            pytest.fail(f"unexpected ioctl {number}")

    monkeypatch.setattr(keyboard.os, "open", open_device)
    monkeypatch.setattr(keyboard.os, "close", closed.append)
    monkeypatch.setattr(keyboard.fcntl, "ioctl", ioctl)
    return opened, closed


def test_discovery_opens_only_identified_virtual_device_read_only(monkeypatch, tmp_path):
    opened, closed = _discovery(monkeypatch, tmp_path)
    monitor = keyboard.KeyboardState.open(tmp_path / ".ydotool_socket")
    assert opened == [(Path("/dev/input/event0"), keyboard.os.O_RDONLY | keyboard.os.O_NONBLOCK)]
    monitor.close()
    assert closed == [12]


@pytest.mark.parametrize("count", [0, 2])
def test_discovery_refuses_missing_or_ambiguous_devices(monkeypatch, tmp_path, count):
    opened, _ = _discovery(monkeypatch, tmp_path, count=count)
    with pytest.raises(OSError, match="one identifiable"):
        keyboard.KeyboardState.open(tmp_path / ".ydotool_socket")
    assert opened == []


def test_discovery_refuses_custom_socket_without_opening_device(monkeypatch, tmp_path):
    opened, _ = _discovery(monkeypatch, tmp_path)
    with pytest.raises(OSError, match="custom.*socket"):
        keyboard.KeyboardState.open(tmp_path / "other.sock")
    assert opened == []


@pytest.mark.parametrize("options", [
    {"identity": (3, 0x2333, 0x6666, 1)}, {"name": "some other keyboard"},
])
def test_discovery_rechecks_identity_and_closes_rejected_device(monkeypatch, tmp_path, options):
    _, closed = _discovery(monkeypatch, tmp_path, **options)
    with pytest.raises(OSError, match="expected.*virtual keyboard"):
        keyboard.KeyboardState.open(tmp_path / ".ydotool_socket")
    assert closed == [12]
