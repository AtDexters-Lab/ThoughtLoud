from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, Qt
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from voxd.core.config import get_config
from voxd.core.voxd_core import CoreProcessThread
from voxd.utils.ipc_server import start_ipc_server


ASSETS_DIR = (Path(__file__).resolve().parent / ".." / "assets").resolve()
_RECORDING_ICONS = [f"voxd-{index}.png" for index in range(1, 10)]
_WORKING_ICONS = ["voxd-0.png", "voxd-9.png", "voxd-1.png", "voxd-9.png"]


class VoxdTrayApp(QObject):
    def __init__(self):
        super().__init__()
        self.cfg = get_config()
        self.status = "Ready"
        self.thread: CoreProcessThread | None = None

        self.idle_icon = QIcon(str(ASSETS_DIR / "voxd-0.png"))
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
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._advance_frame)
        self.animation_frames: list[QIcon] = []
        self.animation_index = 0

        self.set_status("Ready")
        self.tray.show()

    def toggle_recording(self) -> None:
        if self.status == "Recording":
            if self.thread and self.thread.isRunning():
                self.thread.stop_recording()
            return
        if self.status != "Ready":
            return

        self.thread = CoreProcessThread(self.cfg)
        self.thread.status_changed.connect(self.set_status, Qt.ConnectionType.QueuedConnection)
        self.thread.finished.connect(self._on_finished)
        self.set_status("Recording")
        self.thread.start()

    def set_status(self, status: str) -> None:
        if QApplication.instance().thread() != QThread.currentThread():
            QTimer.singleShot(0, lambda: self.set_status(status))
            return

        self.status = status
        self.tray.setToolTip(f"VOXD - {status}")
        if status == "Recording":
            self._start_animation(self.recording_icons, 500)
            self.record_action.setText("Stop Recording")
        elif status in {"Transcribing", "Typing"}:
            self._start_animation(self.working_icons, 1000)
            self.record_action.setText(f"{status}…")
        else:
            self._stop_animation()
            self.record_action.setText("Start Recording")

        idle_or_recording = status in {"Ready", "Recording"}
        self.record_action.setEnabled(idle_or_recording)
        self.quit_action.setEnabled(status == "Ready")

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

    @staticmethod
    def quit_app() -> None:
        QApplication.quit()


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray_app = VoxdTrayApp()
    start_ipc_server(lambda: QTimer.singleShot(0, tray_app.toggle_recording))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
