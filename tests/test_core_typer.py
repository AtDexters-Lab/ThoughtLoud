from pathlib import Path

import pytest


def _make_ydotool_typer():
    from voxd.core.typer import YdotoolTyper

    typer = object.__new__(YdotoolTyper)
    typer.tool = "/usr/bin/ydotool"
    typer.supports_key_hold = True
    typer.supports_word_pacing = True
    typer.socket_path = Path("/unused/in-tests")
    typer.delay_ms = 0.0
    typer.delay_str = "0"
    typer.legacy_delay_ms = 1.0
    typer.legacy_delay_str = "1"
    typer.word_delay_ms = 10.0
    typer.word_delay_str = "10"
    typer.start_delay = 0.0
    typer.cfg = type("Cfg", (), {"data": {"append_trailing_space": True}})()
    # Existing CLI tests deliberately exercise compatibility mode, without ever
    # discovering or opening the desktop's real virtual keyboard.
    typer._open_key_monitor = lambda: None
    return typer


def test_missing_ydotool_is_a_hard_failure():
    typer = _make_ydotool_typer()
    typer.tool = None

    with pytest.raises(RuntimeError, match="not installed"):
        typer.type("hello")


def test_custom_ydotool_socket_is_respected(monkeypatch):
    from voxd.core.typer import YdotoolTyper

    monkeypatch.setenv("YDOTOOL_SOCKET", "/tmp/custom-ydotool.sock")
    monkeypatch.setattr(YdotoolTyper, "_find_tool", lambda _self: "/usr/bin/ydotool")

    assert YdotoolTyper().socket_path == Path("/tmp/custom-ydotool.sock")


def test_daemon_readiness_uses_ydotool_datagram_socket(monkeypatch, tmp_path):
    import voxd.core.typer as typer_module

    typer = _make_ydotool_typer()
    typer.socket_path = tmp_path / "ydotool.sock"
    typer.socket_path.touch()
    socket_types = []

    class FakeProbe:
        def settimeout(self, _timeout):
            pass

        def connect(self, path):
            assert path == str(typer.socket_path)

        def close(self):
            pass

    def fake_socket(_family, socket_type):
        socket_types.append(socket_type)
        return FakeProbe()

    monkeypatch.setattr(typer_module.socket, "socket", fake_socket)

    assert typer._daemon_socket_ready() is True
    assert socket_types == [typer_module.socket.SOCK_DGRAM]


def test_ydotool_types_long_text_as_real_keystroke_chunks(monkeypatch):
    typer = _make_ydotool_typer()
    calls = []

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("timeout"), kwargs.get("input")))
        return Result()

    monkeypatch.setattr(typer, "_ensure_daemon", lambda: True)
    monkeypatch.setattr("voxd.core.typer.subprocess.run", fake_run)
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda *_: None)

    text = ("namaste duniya this is a long Hinglish transcript " * 30).strip()
    typer.type(text)

    type_calls = [
        (cmd, timeout, kwargs_input)
        for cmd, timeout, kwargs_input in calls
        if cmd[1] == "type"
    ]
    assert len(type_calls) > 1
    rendered_chunks = ["".join(cmd[cmd.index("--") + 1 :]) for cmd, _, _ in type_calls]
    assert "".join(rendered_chunks) == text + " "
    assert all(len(chunk) <= 400 for chunk in rendered_chunks)
    assert all(
        cmd[2:11] == ["-d", "0", "-H", "1", "-D", "10", "-e", "0", "--"]
        for cmd, _, _ in type_calls
    )
    assert all(input_text is None for _, _, input_text in type_calls)
    assert all(timeout >= 5 for _, timeout, _ in type_calls)
    assert max(timeout for _, timeout, _ in type_calls) > 5


@pytest.mark.parametrize(
    "text",
    [
        "  hello  world",
        "\t\nhello \nworld\t",
        "   \t\n",
        "-leading --option-like text",
    ],
)
def test_word_runs_preserve_every_character(text):
    from voxd.core.typer import YdotoolTyper

    assert "".join(YdotoolTyper._word_runs(text)) == text


def test_word_pacing_requires_complete_type_cli_capabilities(tmp_path):
    from voxd.core.typer import YdotoolTyper

    tool = tmp_path / "ydotool"
    tool.write_bytes(
        b"Usage: type [OPTION]... [STRINGS]...\0"
        b"key-hold=N\0"
        b"Delay N milliseconds between command line strings\0"
        b"escape=BOOL\0"
        b"hd:D:H:f:e:\0"
    )
    assert YdotoolTyper._supports_word_pacing(str(tool)) is True

    tool.write_bytes(b"next-delay\0key-hold\0")
    assert YdotoolTyper._supports_word_pacing(str(tool)) is False


def test_ydotool_chunk_limit_includes_boundary_whitespace():
    from voxd.core.typer import YdotoolTyper

    text = "a" * 400 + " " + "tail"
    chunks = list(YdotoolTyper._split_chunks(text, max_chars=400))

    assert "".join(chunks) == text
    assert all(len(chunk) <= 400 for chunk in chunks)


def test_ydotool_stops_after_failed_chunk(monkeypatch):
    typer = _make_ydotool_typer()
    attempts = []

    def fake_run_tool(cmd, *, timeout, input_text):
        attempts.append(cmd)
        return len(attempts) == 1

    monkeypatch.setattr(typer, "_ensure_daemon", lambda: True)
    monkeypatch.setattr(typer, "_run_tool", fake_run_tool)
    monkeypatch.setattr(typer, "_release_keys", lambda: None)
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="complete transcript"):
        typer.type("word " * 300)

    assert len(attempts) == 2


def test_ubuntu_legacy_ydotool_cli_uses_key_delay_and_stdin(monkeypatch):
    typer = _make_ydotool_typer()
    typer.supports_key_hold = False
    typer.supports_word_pacing = False
    typer.delay_ms = 2.0
    typer.delay_str = "2"
    typer.legacy_delay_ms = 2.0
    typer.legacy_delay_str = "2"
    calls = []

    def fake_run_tool(command, *, timeout, input_text):
        calls.append((command, timeout, input_text))
        return True

    monkeypatch.setattr(typer, "_ensure_daemon", lambda: True)
    monkeypatch.setattr(typer, "_run_tool", fake_run_tool)
    monkeypatch.setattr(typer, "_release_keys", lambda: None)
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda *_: None)

    typer.type("-leading option-like text")

    command, _, input_text = calls[0]
    assert command[2:] == ["--key-delay", "2", "--file", "-"]
    assert input_text == "-leading option-like text "


def test_partial_modern_ydotool_keeps_legacy_stdin_timing(monkeypatch):
    typer = _make_ydotool_typer()
    typer.supports_word_pacing = False
    calls = []

    def fake_run_tool(command, *, timeout, input_text):
        calls.append((command, timeout, input_text))
        return True

    monkeypatch.setattr(typer, "_ensure_daemon", lambda: True)
    monkeypatch.setattr(typer, "_run_tool", fake_run_tool)
    monkeypatch.setattr(typer, "_release_keys", lambda: None)
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda *_: None)

    typer.type("hello world")

    command, _, input_text = calls[0]
    assert command[2:] == ["-d", "1", "-H", "5", "-f", "-"]
    assert input_text == "hello world "


class FakeMonitor:
    def __init__(self, events, *, initial=None, stuck=None, recovery_keys=None, lost=False):
        self.events = events
        self.initial = initial or set()
        self.stuck = stuck or set()
        self.recovery_keys = recovery_keys or set()
        self.lost = lost

    def pressed_keys(self):
        self.events.append("initial-state")
        return self.initial

    def stuck_keys(self):
        from voxd.core.keyboard_state import KeyStateError

        self.events.append("observe")
        if self.lost:
            raise KeyStateError("event stream lost")
        return self.stuck

    def wait(self, timeout):
        self.events.append(("wait-events", timeout))

    def settle(self, *, released):
        from voxd.core.keyboard_state import KeyStateError

        self.events.append(("settle", released))
        if self.lost:
            raise KeyStateError("event stream lost")
        return set() if released else self.recovery_keys

    def close(self):
        self.events.append("close")


def _install_monitor(monkeypatch, typer, monitor):
    monkeypatch.setattr(typer, "_ensure_daemon", lambda: True)
    monkeypatch.setattr(typer, "_open_key_monitor", lambda: monitor)


def _process_factory(monkeypatch, events, *, hang=False, returncode=0, clock=None, unreap=False):
    import subprocess

    calls = []

    class Stdin:
        def write(self, value):
            events.append(("stdin", value))

        def close(self):
            events.append("close-stdin")

    class Process:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            events.append("spawn")
            self.returncode = None
            self.killed = False
            self.stdin = Stdin() if kwargs["stdin"] == subprocess.PIPE else None

        def poll(self):
            if hang:
                if clock is not None:
                    clock[0] += 6
            else:
                self.returncode = returncode
            return self.returncode

        def kill(self):
            self.killed = True
            events.append("kill")

        def wait(self, timeout):
            if unreap:
                raise subprocess.TimeoutExpired(calls[-1][0], timeout)
            if self.killed:
                self.returncode = -9
            events.append("reap")
            return self.returncode

    monkeypatch.setattr("voxd.core.typer.subprocess.Popen", Process)
    return calls


def test_monitored_long_text_has_no_per_chunk_pause_or_cleanup(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    _install_monitor(monkeypatch, typer, FakeMonitor(events))
    calls = _process_factory(monkeypatch, events)
    monkeypatch.setattr(typer, "_release_keys", lambda *_: pytest.fail("healthy typing released keys"))
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda *_: pytest.fail("healthy chunks paused"))

    text = ("Several words, uppercase SHIFT and repeated eeeeee.\n" * 35).rstrip()
    typer.type(text)

    commands = [command for command, _ in calls]
    assert len(commands) > 3
    assert "".join("".join(command[11:]) for command in commands) == text + " "
    assert all(command[2:11] == ["-d", "0", "-H", "1", "-D", "10", "-e", "0", "--"]
               for command in commands)
    assert all(len("".join(command[11:])) <= 400 for command in commands)
    assert [event for event in events if isinstance(event, tuple) and event[0] == "settle"] == [
        ("settle", True)
    ]
    assert events[-1] == "close"


def test_stuck_recovery_stops_and_reaps_before_targeted_release_and_fails_insertion(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    monitor = FakeMonitor(events, stuck={30}, recovery_keys={30, 42})
    _install_monitor(monkeypatch, typer, monitor)
    calls = _process_factory(monkeypatch, events, hang=True)

    def release(keys=None):
        events.append(("release", keys))
        return True

    monkeypatch.setattr(typer, "_release_keys", release)
    with pytest.raises(RuntimeError, match="remained held"):
        typer.type("word " * 300)

    assert len(calls) == 1
    assert events.index("kill") < events.index("reap") < events.index(("settle", False))
    assert events.index(("settle", False)) < events.index(("release", {30, 42}))
    assert events.index(("release", {30, 42})) < events.index(("settle", True))
    assert events[-1] == "close"


def test_preexisting_held_key_refuses_typing_without_releasing_it(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    _install_monitor(monkeypatch, typer, FakeMonitor(events, initial={42}))
    monkeypatch.setattr(typer, "_run_tool", lambda *_args, **_kwargs: pytest.fail("typing started"))
    monkeypatch.setattr(typer, "_release_keys", lambda *_: pytest.fail("unowned key released"))

    with pytest.raises(RuntimeError, match="already held.*not started"):
        typer.type("hello")

    assert events == ["initial-state", "close"]


def test_final_state_failure_recovers_but_never_returns_success(monkeypatch):
    from voxd.core.keyboard_state import KeyStateError

    typer = _make_ydotool_typer()
    events = []
    monitor = FakeMonitor(events)
    released_checks = 0

    def settle(*, released):
        nonlocal released_checks
        events.append(("settle", released))
        if released:
            released_checks += 1
            if released_checks == 1:
                raise KeyStateError("keyboard did not settle")
            return set()
        return {30}

    monitor.settle = settle
    _install_monitor(monkeypatch, typer, monitor)
    _process_factory(monkeypatch, events)
    monkeypatch.setattr(typer, "_release_keys", lambda keys: events.append(("release", keys)) or True)

    with pytest.raises(RuntimeError, match="did not settle"):
        typer.type("hello")

    assert ("release", {30}) in events
    assert released_checks == 2
    assert events[-1] == "close"


@pytest.mark.parametrize("failure_at", ["read", "wait"])
def test_lost_monitor_stops_process_before_compatibility_recovery_and_reports_failure(monkeypatch, failure_at):
    from voxd.core.keyboard_state import KeyStateError

    typer = _make_ydotool_typer()
    events = []
    monitor = FakeMonitor(events, lost=True)
    if failure_at == "wait":
        def fail_wait(_timeout):
            raise KeyStateError("event stream lost")
        monitor.wait = fail_wait
    _install_monitor(monkeypatch, typer, monitor)
    _process_factory(monkeypatch, events, hang=True)
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(typer, "_release_keys", lambda keys=None: events.append(("release", keys)) or True)

    with pytest.raises(RuntimeError, match="recovery failed.*event stream lost"):
        typer.type("hello")

    assert events.index("reap") < events.index(("release", None))
    assert ("settle", True) not in events
    assert events[-1] == "close"


def test_monitored_timeout_reaps_before_recovery_and_stops_future_chunks(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    clock = [0.0]
    _install_monitor(monkeypatch, typer, FakeMonitor(events, recovery_keys={30}))
    calls = _process_factory(monkeypatch, events, hang=True, clock=clock)
    monkeypatch.setattr("voxd.core.typer.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(typer, "_release_keys", lambda keys: events.append(("release", keys)) or True)

    with pytest.raises(RuntimeError, match="complete transcript"):
        typer.type("word " * 300)

    assert len(calls) == 1
    assert events.index("reap") < events.index(("release", {30}))
    assert events[-1] == "close"


def test_failed_targeted_release_is_reported_as_recovery_failure(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    _install_monitor(monkeypatch, typer, FakeMonitor(events, stuck={30}, recovery_keys={30}))
    _process_factory(monkeypatch, events, hang=True)
    monkeypatch.setattr(typer, "_release_keys", lambda keys: False)

    with pytest.raises(RuntimeError, match="recovery failed.*key-release command failed"):
        typer.type("hello")

    assert ("settle", True) not in events
    assert events[-1] == "close"


def test_unreaped_typing_process_refuses_concurrent_cleanup(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    _install_monitor(monkeypatch, typer, FakeMonitor(events, stuck={30}, recovery_keys={30}))
    _process_factory(monkeypatch, events, hang=True, unreap=True)
    monkeypatch.setattr(typer, "_release_keys", lambda *_: pytest.fail("cleanup raced live process"))

    with pytest.raises(RuntimeError, match="could not be stopped.*refusing concurrent cleanup"):
        typer.type("hello")

    assert "kill" in events
    assert ("settle", False) not in events
    assert events[-1] == "close"


def test_monitored_stdin_chunk_is_written_once_and_closed_before_observation(monkeypatch):
    typer = _make_ydotool_typer()
    typer.supports_word_pacing = False
    events = []
    _install_monitor(monkeypatch, typer, FakeMonitor(events))
    calls = _process_factory(monkeypatch, events)

    typer.type("hello uppercase SHIFT")

    assert calls[0][0][2:] == ["-d", "1", "-H", "5", "-f", "-"]
    assert [event for event in events if isinstance(event, tuple) and event[0] == "stdin"] == [
        ("stdin", "hello uppercase SHIFT ")
    ]
    assert events.index("close-stdin") < events.index("observe")
    assert events[-1] == "close"


def test_failed_client_with_all_keys_up_still_fails_without_cleanup(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    _install_monitor(monkeypatch, typer, FakeMonitor(events))
    calls = _process_factory(monkeypatch, events, returncode=1)
    monkeypatch.setattr(typer, "_release_keys", lambda *_: pytest.fail("all-up failure needs no release"))

    with pytest.raises(RuntimeError, match="complete transcript"):
        typer.type("word " * 300)

    assert len(calls) == 1
    assert ("settle", False) in events
    assert ("settle", True) in events
    assert events[-1] == "close"


@pytest.mark.parametrize("supports_key_hold,error", [
    (False, None), (True, OSError("permission denied")),
])
def test_monitor_unavailable_uses_explicit_compatibility_fallback(monkeypatch, capsys, supports_key_hold, error):
    from voxd.core.typer import YdotoolTyper

    typer = _make_ydotool_typer()
    typer.supports_key_hold = supports_key_hold

    def open_monitor(_path):
        if error:
            raise error
        pytest.fail("legacy CLI attempted monitored typing")

    monkeypatch.setattr("voxd.core.typer.KeyboardState.open", open_monitor)
    assert YdotoolTyper._open_key_monitor(typer) is None
    assert "compatibility cleanup" in capsys.readouterr().out


@pytest.mark.parametrize("modern", [True, False])
def test_targeted_release_has_appropriate_cli_and_only_observed_keys(monkeypatch, modern):
    typer = _make_ydotool_typer()
    typer.supports_key_hold = modern
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("voxd.core.typer.subprocess.run", run)
    assert typer._release_keys({42, 30}) is True
    assert calls == [["/usr/bin/ydotool", "key", *(["-d", "0"] if modern else []), "30:0", "42:0"]]


def test_compatibility_typing_retains_per_chunk_drain_and_cleanup(monkeypatch):
    typer = _make_ydotool_typer()
    events = []
    monkeypatch.setattr(typer, "_ensure_daemon", lambda: True)
    monkeypatch.setattr(typer, "_run_tool", lambda *_args, **_kwargs: events.append("type") or True)
    monkeypatch.setattr(typer, "_release_keys", lambda: events.append("release") or True)
    monkeypatch.setattr("voxd.core.typer.time.sleep", lambda seconds: events.append(("sleep", seconds)))

    typer.type("word " * 200)

    assert len(events) >= 6
    assert len(events) % 3 == 0
    assert all(events[index:index + 3] == ["type", ("sleep", 0.05), "release"]
               for index in range(0, len(events), 3))
