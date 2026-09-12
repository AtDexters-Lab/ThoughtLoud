from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication


_APP = None


class FakeCore(QObject):
    status_changed = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    instances = []
    def __init__(self, cfg, **kwargs):
        super().__init__()
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.instances.append(self)
    def start(self): self.started = True
    def stop_recording(self): self.stopped = True
    def isRunning(self): return self.started


@pytest.fixture
def tray(monkeypatch):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    from voxd.tray import tray_main
    from voxd.core.config import AppConfig
    cfg = AppConfig()
    cfg.set("setup_completed", True)
    cfg.set("managed_runtime_enabled", False)
    monkeypatch.setattr(tray_main, "get_config", lambda: cfg)
    monkeypatch.setattr(tray_main, "Thread", lambda **_: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(tray_main, "CoreProcessThread", FakeCore)
    monkeypatch.setattr(tray_main.QSystemTrayIcon, "isSystemTrayAvailable", lambda *_: True)
    FakeCore.instances = []
    tray = tray_main.VoxdTrayApp()
    yield tray
    tray._shutdown()
    tray.settings.close()
    tray.tray.hide()
    if tray._alert is not None:
        tray._alert.close()


def test_muted_start_is_blocked_with_visible_window(tray):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    tray._complete_preflight(tray._preflight_id, InputStatus("source", True))
    assert tray.status == "Ready"
    assert FakeCore.instances == []
    assert tray._alert.isVisible()


@pytest.mark.parametrize("status", [False, None])
def test_unmuted_or_unknown_mic_starts_recording(tray, status):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    tray._complete_preflight(tray._preflight_id, InputStatus("source", status))
    assert tray.status == "Recording"
    assert FakeCore.instances[0].started
    recorder_factory = FakeCore.instances[0].kwargs["recorder_factory"]
    assert recorder_factory.keywords["stream_factory"].keywords["source"] == "source"


def test_positive_mute_without_capture_source_does_not_block(tray):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    tray._complete_preflight(tray._preflight_id, InputStatus(None, True))
    assert tray.status == "Recording"


def test_second_trigger_cancels_pending_start(tray):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    request = tray._preflight_id
    tray.toggle_recording()
    tray._complete_preflight(request, InputStatus("source", False))
    assert tray.status == "Ready"
    assert FakeCore.instances == []


def test_timed_out_probe_cannot_later_start_second_recording(tray):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    request = tray._preflight_id
    tray._preflight_timeout()
    tray._complete_preflight(request, InputStatus("source", False))
    assert len(FakeCore.instances) == 1
    assert FakeCore.instances[0].kwargs["recorder_factory"] is None


def test_shutdown_invalidates_pending_start(tray):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    request = tray._preflight_id
    tray._shutdown()
    tray._complete_preflight(request, InputStatus("source", False))
    tray.toggle_recording()
    assert FakeCore.instances == []


def test_recording_stop_bypasses_probe(tray):
    from voxd.platforms.linux.audio import InputStatus
    tray.toggle_recording()
    tray._complete_preflight(tray._preflight_id, InputStatus())
    request = tray._preflight_id
    tray.toggle_recording()
    assert FakeCore.instances[0].stopped
    assert tray._preflight_id == request


def test_managed_controller_is_reused_and_actual_accelerator_shown(tray, monkeypatch):
    import voxd.runtime
    from voxd.platforms.linux.audio import InputStatus
    made = []
    class Runtime:
        def __init__(self, _model_dir, *, accelerator):
            self.accelerator = accelerator
            made.append(self)
    monkeypatch.setattr(voxd.runtime, "ManagedRuntime", Runtime)
    tray.cfg.set("managed_runtime_enabled", True)
    tray.toggle_recording()
    tray._complete_preflight(tray._preflight_id, InputStatus())
    assert FakeCore.instances[-1].kwargs["runtime"] is made[0]
    tray.thread = None
    tray.set_status("Ready")
    tray.cfg.set("runtime_device", "vulkan")
    tray.toggle_recording()
    tray._complete_preflight(tray._preflight_id, InputStatus())
    assert len(made) == 1
    assert "CPU" in tray.settings.runtime_status.text()
    assert "restarting" in tray.settings.runtime_status.text()


def test_quit_cleans_runtime_in_worker_before_application_exit(tray, monkeypatch):
    from voxd.tray import tray_main
    workers, events = [], []
    monkeypatch.setattr(tray_main, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: workers.append(kwargs["target"])))
    tray.runtime = SimpleNamespace(close=lambda: events.append("closed"))
    tray.shutdown_finished.connect(lambda: events.append("quit"))
    tray.quit_app()
    assert tray._closing
    assert events == []
    assert tray.status == "Closing"
    workers[0]()
    assert events == ["closed", "quit"]


def test_first_run_prepares_typing_without_terminal_step(tray, monkeypatch):
    from voxd.tray import tray_main
    from voxd.core.config import AppConfig
    from voxd.tray.settings import SettingsWindow
    events = []
    cfg = AppConfig()
    assert not cfg.setup_completed
    monkeypatch.setattr(tray_main, "get_config", lambda: cfg)
    monkeypatch.setattr(SettingsWindow, "prepare_typing", lambda self: events.append("prepared"))
    first_run = tray_main.VoxdTrayApp()
    QApplication.processEvents()
    assert events == ["prepared"]
    first_run._shutdown()
    first_run.settings.close()
    first_run.tray.hide()


def test_public_window_tray_and_icons_use_thoughtloud(tray):
    assert tray.settings.windowTitle() == "Setup and settings"
    assert tray.tray.toolTip() == "ThoughtLoud - Ready"
    for icon in [tray.idle_icon] + tray.recording_icons + tray.working_icons:
        for size in (16, 32, 64, 256):
            assert not icon.pixmap(size, size).isNull()


@pytest.fixture
def startup_tray(tray, monkeypatch):
    from voxd.tray import tray_main
    from voxd.tray.settings import SettingsWindow
    made = []
    state = SimpleNamespace(available=False, now=100.0)
    monkeypatch.setattr(tray_main.QSystemTrayIcon, "isSystemTrayAvailable", lambda *_: state.available)
    monkeypatch.setattr(tray_main, "monotonic", lambda: state.now)
    monkeypatch.setattr(SettingsWindow, "prepare_typing", lambda *_: None)

    def create(*, setup_completed=True, show_settings=False):
        tray.cfg.set("setup_completed", setup_completed)
        instance = tray_main.VoxdTrayApp(show_settings=show_settings)
        made.append(instance)
        return instance

    yield create, state
    for instance in made:
        instance._shutdown()
        instance.settings.close()
        instance.tray.hide()


def test_configured_login_with_ready_tray_stays_in_background(startup_tray):
    create, state = startup_tray
    state.available = True
    instance = create()
    assert not instance.settings.isVisible()
    assert not instance.startup_timer.isActive()


def test_configured_login_waits_for_tray_host_without_opening_settings(startup_tray):
    create, state = startup_tray
    instance = create()
    assert instance.startup_timer.isActive()
    assert not instance.settings.isVisible()
    state.now += 1
    instance._check_startup_tray()
    assert instance.startup_timer.isActive()
    assert not instance.settings.isVisible()
    state.available = True
    instance._check_startup_tray()
    assert not instance.startup_timer.isActive()
    assert not instance.settings.isVisible()


def test_missing_tray_host_exposes_settings_after_bounded_grace(startup_tray):
    from voxd.tray.tray_main import _TRAY_AVAILABILITY_GRACE_MS
    create, state = startup_tray
    instance = create()
    state.now += _TRAY_AVAILABILITY_GRACE_MS / 1000 - 0.001
    instance._check_startup_tray()
    assert not instance.settings.isVisible()
    state.now += 0.001
    instance._check_startup_tray()
    assert instance.settings.isVisible()
    assert not instance.startup_timer.isActive()


@pytest.mark.parametrize("setup_completed,show_settings", [(False, False), (True, True)])
def test_onboarding_and_explicit_settings_open_without_waiting(startup_tray, setup_completed, show_settings):
    create, _ = startup_tray
    instance = create(setup_completed=setup_completed, show_settings=show_settings)
    assert instance.settings.isVisible()
    assert not instance.startup_timer.isActive()


@pytest.mark.parametrize("cancel", ["settings", "shutdown"])
def test_cancelled_startup_check_never_reopens_settings(startup_tray, cancel):
    create, state = startup_tray
    instance = create()
    assert instance.startup_timer.isActive()
    if cancel == "settings":
        instance.show_settings()
        instance.settings.hide()
    else:
        instance._shutdown()
    assert not instance.startup_timer.isActive()
    state.now += 60
    instance._check_startup_tray()
    assert not instance.settings.isVisible()


def test_hotkey_before_tray_deadline_keeps_the_dictation_target_focused(startup_tray):
    from voxd.platforms.linux.audio import InputStatus
    create, state = startup_tray
    instance = create()
    assert instance.startup_timer.isActive()
    instance.toggle_recording()
    assert instance.status == "Checking microphone"
    assert not instance.startup_timer.isActive()
    instance._complete_preflight(instance._preflight_id, InputStatus())
    assert instance.status == "Recording"
    for status in ("Recording", "Transcribing", "Typing", "Ready"):
        instance.set_status(status)
        state.now += 60
        instance._check_startup_tray()
        assert not instance.settings.isVisible()
