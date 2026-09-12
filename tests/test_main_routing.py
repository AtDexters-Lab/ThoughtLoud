import sys


def test_default_invocation_routes_to_tray(monkeypatch):
    import voxd.__main__ as main_module
    import voxd.tray.tray_main as tray_module

    called = []
    monkeypatch.setattr(sys, "argv", ["voxd"])
    monkeypatch.setattr(tray_module, "main", lambda: called.append("tray"))

    main_module.main()

    assert called == ["tray"]


def test_trigger_exit_status_reflects_ipc_delivery(monkeypatch):
    import voxd.__main__ as main_module
    import voxd.utils.ipc_client as ipc_client

    monkeypatch.setattr(sys, "argv", ["voxd", "--trigger-record"])
    monkeypatch.setattr(ipc_client, "send_trigger", lambda: False)

    try:
        main_module.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("trigger mode did not exit")


def test_settings_reuses_existing_app(monkeypatch):
    import voxd.__main__ as main_module
    import voxd.utils.ipc_client as ipc_client
    import voxd.tray.tray_main as tray_module
    monkeypatch.setattr(sys, "argv", ["voxd", "--settings"])
    monkeypatch.setattr(ipc_client, "send_settings", lambda: True)
    monkeypatch.setattr(tray_module, "main", lambda **_: (_ for _ in ()).throw(AssertionError("duplicate app")))
    main_module.main()


def test_settings_opens_first_instance_when_no_app_running(monkeypatch):
    import voxd.__main__ as main_module
    import voxd.utils.ipc_client as ipc_client
    import voxd.tray.tray_main as tray_module
    called = []
    monkeypatch.setattr(sys, "argv", ["voxd", "--settings"])
    monkeypatch.setattr(ipc_client, "send_settings", lambda: False)
    monkeypatch.setattr(tray_module, "main", lambda **kwargs: called.append(kwargs))
    main_module.main()
    assert called == [{"show_settings": True}]


def test_frozen_entry_uses_bundled_input_helpers(monkeypatch, tmp_path):
    import os
    import voxd.__main__ as main_module
    # setdefault creates previously absent keys; isolate the mapping itself so
    # those application writes cannot leak into later source-install tests.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "voxd"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    monkeypatch.delenv("VOXD_YDOTOOL_SERVICE", raising=False)
    main_module._configure_bundled_paths()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "libexec")
    assert os.environ["YDOTOOL_SOCKET"] == str(tmp_path / ".voxd_ydotool_socket")
    assert os.environ["VOXD_YDOTOOL_SERVICE"] == "voxd-ydotoold.service"
