from __future__ import annotations

import sys
from functools import partial
from threading import Thread
from time import monotonic

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from voxd.core.config import get_config
from voxd.branding import APP_NAME, ASSETS_DIR, DESKTOP_ID
from voxd.core.voxd_core import CoreProcessThread
from voxd.utils.ipc_server import start_ipc_server
from voxd.platforms.linux.audio import InputStatus, PulseInputStream, probe_input
from voxd.platforms.linux.shortcuts import PortalShortcuts
from voxd.tray.settings import SettingsWindow


_RECORDING_ICONS = [f"thoughtloud-recording-{index}.png" for index in range(1, 5)]
_WORKING_ICONS = [f"thoughtloud-working-{index}.png" for index in range(1, 5)]
_TRAY_AVAILABILITY_GRACE_MS = 5000
_TRAY_RECHECK_MS = 250


class VoxdTrayApp(QObject):
    preflight_finished = pyqtSignal(int, object)
    trigger_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    shutdown_finished = pyqtSignal()

    def __init__(self, *, show_settings=False):
        super().__init__()
        self.cfg = get_config()
        self.status = "Ready"
        self.thread: CoreProcessThread | None = None
        self.runtime = None
        self._preflight_id = 0
        self._closing = False
        self._alert = None
        self.preflight_finished.connect(self._complete_preflight)
        self.trigger_requested.connect(self.toggle_recording)
        self.settings_requested.connect(self.show_settings)
        self.shutdown_finished.connect(QApplication.quit)
        self.preflight_timer = QTimer(self)
        self.preflight_timer.setSingleShot(True)
        self.preflight_timer.timeout.connect(self._preflight_timeout)
        self.startup_timer = QTimer(self)
        self.startup_timer.timeout.connect(self._check_startup_tray)
        self._tray_ready_deadline = 0.0
        self.shortcuts = PortalShortcuts(self)
        self.shortcuts.activated.connect(self.toggle_recording)
        self.shortcuts.changed.connect(self._shortcut_changed)
        self.settings = SettingsWindow(self.cfg, self.shortcuts)
        self.settings.record_requested.connect(self.toggle_recording)
        self.settings.quit_requested.connect(self.quit_app)
        QApplication.instance().aboutToQuit.connect(self._shutdown)

        # PNG renders of our SVG artwork work with distribution Qt builds that
        # do not install the optional SVG icon plugin.
        self.idle_icon = QIcon(str(ASSETS_DIR / "thoughtloud.png"))
        self.recording_icons = [QIcon(str(ASSETS_DIR / name)) for name in _RECORDING_ICONS]
        self.working_icons = [QIcon(str(ASSETS_DIR / name)) for name in _WORKING_ICONS]

        self.tray = QSystemTrayIcon(self.idle_icon)
        self.menu = QMenu()
        self.record_action = QAction("Start Recording")
        self.record_action.triggered.connect(self.toggle_recording)
        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(self.record_action)
        self.menu.addSeparator()
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.show_settings)
        self.menu.addAction(settings_action)
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._advance_frame)
        self.animation_frames: list[QIcon] = []
        self.animation_index = 0

        self.set_status("Ready")
        self.tray.show()
        if show_settings or not self.cfg.setup_completed:
            self.show_settings()
        elif not self.tray.isSystemTrayAvailable():
            # A desktop's tray host can appear after its login applications.
            # Keep first-run setup immediate, but give configured sessions a
            # short grace period before exposing the no-tray settings fallback.
            self._tray_ready_deadline = monotonic() + _TRAY_AVAILABILITY_GRACE_MS / 1000
            self.startup_timer.start(_TRAY_RECHECK_MS)
        if not self.cfg.setup_completed:
            QTimer.singleShot(0, self.settings.prepare_typing)
        if self.cfg.hotkey_description:
            QTimer.singleShot(0, self.shortcuts.configure)

    def show_settings(self):
        self.startup_timer.stop()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _check_startup_tray(self):
        if self._closing or not self.startup_timer.isActive():
            self.startup_timer.stop()
            return
        if self.tray.isSystemTrayAvailable():
            self.startup_timer.stop()
        elif monotonic() >= self._tray_ready_deadline:
            self.show_settings()

    def _shortcut_changed(self, description):
        if description.startswith("Active shortcut:"):
            self.cfg.set("hotkey_description", description)
            self.cfg.save()

    def toggle_recording(self) -> None:
        if self._closing:
            return
        if self.status == "Recording":
            if self.thread:
                self.thread.stop_recording()
            return
        if self.status == "Checking microphone":
            # A second intentional trigger cancels this start; the worker's late
            # result must never restart recording.
            self._preflight_id += 1
            self.preflight_timer.stop()
            self.set_status("Ready")
            return
        if self.status != "Ready":
            return
        # An intentional shortcut establishes the user's dictation target.
        # Never raise the delayed no-tray fallback over that target.
        self.startup_timer.stop()
        self._preflight_id += 1
        request_id = self._preflight_id
        device, prefer_pulse = self.cfg.audio_input_device, self.cfg.audio_prefer_pulse
        self.set_status("Checking microphone")
        self.preflight_timer.start(1800)
        def check():
            result = probe_input(device, prefer_pulse)
            if not self._closing:
                self.preflight_finished.emit(request_id, result)
        Thread(target=check, daemon=True, name="voxd-microphone-check").start()

    def _preflight_timeout(self):
        self._complete_preflight(self._preflight_id, InputStatus())

    def _complete_preflight(self, request_id, result):
        if self._closing or request_id != self._preflight_id or self.status != "Checking microphone":
            return
        self._preflight_id += 1
        self.preflight_timer.stop()
        if result.muted is True and result.source:
            self.set_status("Ready")
            self._show_error("Microphone is muted. Unmute it and press the shortcut again.", title="Microphone is muted")
            return
        recorder_factory = None
        if result.source:
            from voxd.core.recorder import AudioRecorder
            recorder_factory = partial(AudioRecorder, stream_factory=partial(PulseInputStream, source=result.source))
        runtime = None
        if self.cfg.managed_runtime_enabled:
            if self.runtime is None:
                try:
                    from voxd.runtime import ManagedRuntime
                    from voxd.paths import DATA_DIR
                    self.runtime = ManagedRuntime(DATA_DIR / "models", accelerator=self.cfg.runtime_device)
                except Exception as exc:
                    self.set_status("Ready")
                    self._show_error(str(exc))
                    return
            runtime = self.runtime
            self.settings.runtime_status.setText(
                f"Local runtime uses {runtime.accelerator.upper()}. Accelerator changes apply after restarting the app."
            )
        self.thread = CoreProcessThread(self.cfg, recorder_factory=recorder_factory, runtime=runtime)
        self.thread.error.connect(self._show_error)
        self.thread.status_changed.connect(self.set_status, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self._on_finished)
        self.set_status("Recording")
        self.thread.start()

    def _show_error(self, message, *, title="Dictation needs attention"):
        if self._closing:
            return
        if self._alert is not None:
            self._alert.close()
        self._alert = QMessageBox(QMessageBox.Icon.Warning, title, message)
        self._alert.show()
        self._alert.raise_()
        self._alert.activateWindow()

    def set_status(self, status: str) -> None:
        if QApplication.instance().thread() != QThread.currentThread():
            QTimer.singleShot(0, lambda: self.set_status(status))
            return

        self.status = status
        self.tray.setToolTip(f"{APP_NAME} - {status}")
        self.settings.state.setText("Idle" if status == "Ready" else status)
        if status == "Recording":
            self._start_animation(self.recording_icons, 500)
            self.record_action.setText("Stop Recording")
        elif status == "Checking microphone":
            self._stop_animation()
            self.record_action.setText("Cancel microphone check")
        elif status in {"Transcribing", "Typing"}:
            self._start_animation(self.working_icons, 1000)
            self.record_action.setText(f"{status}…")
        else:
            self._stop_animation()
            self.record_action.setText("Start Recording")

        idle_or_recording = status in {"Ready", "Recording", "Checking microphone"}
        self.record_action.setEnabled(idle_or_recording)
        self.quit_action.setEnabled(status in {"Ready", "Checking microphone"})
        self.settings.record.setText(self.record_action.text())
        self.settings.record.setEnabled(idle_or_recording)

    def _on_finished(self, _transcript: str) -> None:
        if self.thread is not None:
            self.thread.deleteLater()
            self.thread = None
        self.set_status("Ready")

    def _start_animation(self, frames: list[QIcon], total_period_ms: int) -> None:
        if not frames:
            return
        self.animation_frames = frames
        self.animation_index = 0
        self.tray.setIcon(frames[0])
        self.animation_timer.start(max(1, total_period_ms // len(frames)))

    def _stop_animation(self) -> None:
        self.animation_timer.stop()
        self.animation_frames = []
        self.tray.setIcon(self.idle_icon)

    def _advance_frame(self) -> None:
        if not self.animation_frames:
            return
        self.animation_index = (self.animation_index + 1) % len(self.animation_frames)
        self.tray.setIcon(self.animation_frames[self.animation_index])

    def _shutdown(self):
        self._closing = True
        self._preflight_id += 1
        self.preflight_timer.stop()
        self.startup_timer.stop()
        self.shortcuts.close()
        self.settings.shutdown()

    def quit_app(self) -> None:
        if self.status not in {"Ready", "Checking microphone"}:
            self.settings.state.setText("Stop recording and wait for dictation to finish before quitting.")
            return
        self._closing = True
        self._preflight_id += 1
        self.preflight_timer.stop()
        if self.settings.download_active:
            self.settings.quit_after_download = True
            self.settings.cancel_download()
            self.show_settings()
            return
        self._shutdown()
        if self.runtime is not None:
            runtime, self.runtime = self.runtime, None
            self.set_status("Closing")
            def close_runtime():
                try:
                    runtime.close()
                finally:
                    self.shutdown_finished.emit()
            Thread(target=close_runtime, daemon=True, name="voxd-runtime-close").start()
        else:
            QApplication.quit()


def main(*, show_settings=False) -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(DESKTOP_ID)
    app.setWindowIcon(QIcon(str(ASSETS_DIR / "thoughtloud.png")))
    tray_app = VoxdTrayApp(show_settings=show_settings)
    start_ipc_server(tray_app.trigger_requested.emit, tray_app.settings_requested.emit)
    try:
        result = app.exec()
    finally:
        # Also reclaim the owned child when Qt quits through another route
        # (for example the desktop session), outside the explicit Quit action.
        tray_app._shutdown()
        if tray_app.runtime is not None:
            tray_app.runtime.close()
    sys.exit(result)


if __name__ == "__main__":
    main()
