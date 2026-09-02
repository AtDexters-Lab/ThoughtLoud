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
