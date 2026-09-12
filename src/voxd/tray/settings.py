from __future__ import annotations

import io
import wave
from threading import Event, Thread
import time
from urllib.parse import urlparse

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QScrollArea,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from voxd.platforms.linux.audio import probe_input
from voxd.paths import DATA_DIR
from voxd.runtime.models import ModelStore, MODEL_ARTIFACTS


def validate_endpoint(url: str, model: str) -> str:
    import requests
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter an http:// or https:// endpoint URL.")
    if not model.strip():
        raise ValueError("Enter a model identifier.")
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(f"{url.rstrip('/')}/v1/models", timeout=(2, 4))
        response.raise_for_status()
        data = response.json()
        names = [entry.get("id") for entry in data.get("data", []) if isinstance(entry, dict)]
        if model not in names:
            raise ValueError("Endpoint responded, but the configured model is not listed.")
        # Send synthetic silence through the real backend protocol, never the
        # microphone. Text-only /v1/models health is insufficient for audio ASR.
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(16000 * 2))
        from voxd.core.gemma_transcriber import GemmaAudioTranscriber
        transcriber = GemmaAudioTranscriber(server_url=url, model=model,
            timeout=20, attempts=1, max_tokens=16, session=session)
        transcriber.transcribe_segments([(0, audio.getvalue(), False)])
    return "Audio request accepted using synthetic silence. Real speech quality and text insertion still need a dictation test."


def save_settings(cfg, *, preferences, endpoint, model, autostart, managed_runtime=None, runtime_device=None):
    use_local = cfg.managed_runtime_enabled if managed_runtime is None else managed_runtime
    if not use_local:
        parsed = urlparse(endpoint.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Enter an http:// or https:// endpoint URL.")
        if not model.strip():
            raise ValueError("Enter a model identifier.")
    cfg.set("speech_preferences", preferences)
    if not use_local:
        # Local mode does not consume these hidden fields. Keep the last saved
        # external configuration, even if the user left an unfinished edit.
        cfg.set("gemma_server_url", endpoint.strip().rstrip("/"))
        cfg.set("gemma_model", model.strip())
    cfg.set("autostart", autostart)
    if managed_runtime is not None:
        cfg.set("managed_runtime_enabled", managed_runtime)
    if runtime_device is not None:
        cfg.set("runtime_device", runtime_device)
    cfg.set("setup_completed", True)
    cfg.save()


class SettingsWindow(QWidget):
    result = pyqtSignal(str, str)
    download_progress = pyqtSignal(object)
    record_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, cfg, shortcuts):
        super().__init__()
        self.cfg = cfg
        self.shortcuts = shortcuts
        self._model_store = ModelStore(DATA_DIR / "models")
        self._download_cancel = Event()
        self.download_active = False
        self.quit_after_download = False
        self._closed = False
        self.setWindowTitle("Setup and settings")
        self.resize(620, 700)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        intro = QLabel("Think aloud to your computer, in your own words.\nUse the system-default microphone, then press your shortcut to start or stop.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.state = QLabel("Idle")
        layout.addWidget(self.state)
        self.record = QPushButton("Start Recording")
        self.record.clicked.connect(self.record_requested)
        layout.addWidget(self.record)
        self.managed = QCheckBox("Run the transcription model on this computer")
        self.managed.setChecked(getattr(cfg, "managed_runtime_enabled", False))
        layout.addWidget(self.managed)
        self.device = QComboBox()
        self.device.addItem("CPU (compatible baseline)", "cpu")
        self.device.addItem("Vulkan GPU (requires a compatible driver)", "vulkan")
        self.device.setCurrentIndex(1 if getattr(cfg, "runtime_device", "cpu") == "vulkan" else 0)
        layout.addWidget(self.device)
        self.runtime_status = QLabel("Accelerator changes apply after restarting the app once its local runtime has started.")
        self.runtime_status.setWordWrap(True)
        layout.addWidget(self.runtime_status)
        self.download = QPushButton(f"Download local models ({sum(a.size for a in MODEL_ARTIFACTS) / 1e9:.1f} GB)")
        self.download.clicked.connect(self._download_models)
        layout.addWidget(self.download)
        self.cancel_download_button = QPushButton("Cancel download")
        self.cancel_download_button.clicked.connect(self.cancel_download)
        self.cancel_download_button.setEnabled(False)
        layout.addWidget(self.cancel_download_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.download_status = QLabel("Local models installed and verified." if self._model_store.installed() else "Local models are not installed. Download verifies both files before they can be used.")
        self.download_status.setWordWrap(True)
        layout.addWidget(self.download_status)
        form = QFormLayout()
        self.preferences = QTextEdit()
        self.preferences.setPlaceholderText("Optional: I mix Marathi and English, often using software engineering terms.")
        self.preferences.setPlainText(cfg.speech_preferences)
        self.preferences.setMaximumHeight(90)
        form.addRow("How do you like to talk?", self.preferences)
        layout.addLayout(form)
        self.external_endpoint_group = QWidget()
        external_layout = QVBoxLayout(self.external_endpoint_group)
        external_layout.setContentsMargins(0, 0, 0, 0)
        external_form = QFormLayout()
        self.endpoint = QLineEdit(cfg.gemma_server_url)
        self.model = QLineEdit(cfg.gemma_model)
        external_form.addRow("Transcription endpoint", self.endpoint)
        external_form.addRow("Model", self.model)
        external_layout.addLayout(external_form)
        self.check_endpoint = QPushButton("Test audio endpoint")
        self.check_endpoint.clicked.connect(self._check_endpoint)
        external_layout.addWidget(self.check_endpoint)
        self.endpoint_status = QLabel("Existing compatible Gemma audio service. The local model option manages its own endpoint.")
        self.endpoint_status.setWordWrap(True)
        external_layout.addWidget(self.endpoint_status)
        layout.addWidget(self.external_endpoint_group)
        self.setup_typing = QPushButton("Prepare text insertion")
        self.setup_typing.clicked.connect(self.prepare_typing)
        layout.addWidget(self.setup_typing)
        self.typing_status = QLabel("Text insertion needs the desktop package's typing service.")
        self.typing_status.setWordWrap(True)
        layout.addWidget(self.typing_status)
        self.check_mic = QPushButton("Check microphone status")
        self.check_mic.clicked.connect(self._check_mic)
        layout.addWidget(self.check_mic)
        self.mic_status = QLabel("Microphone is checked when recording starts. System volume and mute settings are respected.")
        self.mic_status.setWordWrap(True)
        layout.addWidget(self.mic_status)
        hotkey = QPushButton("Configure hotkey with desktop")
        hotkey.clicked.connect(shortcuts.configure)
        layout.addWidget(hotkey)
        self.hotkey_status = QLabel("No portal shortcut active in this session.")
        self.hotkey_status.setWordWrap(True)
        shortcuts.changed.connect(self.hotkey_status.setText)
        layout.addWidget(self.hotkey_status)
        fallback = QLabel("If your desktop does not support this dialog: open System Settings → Keyboard → Custom Shortcuts. Add a shortcut named ‘Dictation’ with the command below. Keep this app running.")
        fallback.setWordWrap(True)
        layout.addWidget(fallback)
        from voxd.branding import shell_command_text
        command = QLineEdit(shell_command_text("--trigger-record"))
        command.setReadOnly(True)
        layout.addWidget(command)
        self.autostart = QCheckBox("Start at login")
        self.autostart.setChecked(cfg.autostart)
        layout.addWidget(self.autostart)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save settings")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        quit_button = QPushButton("Quit app")
        quit_button.clicked.connect(self.quit_requested)
        buttons.addWidget(quit_button)
        layout.addLayout(buttons)
        self.result.connect(self._result)
        self.download_progress.connect(self._progress)
        self.managed.toggled.connect(self._runtime_mode_changed)
        self._runtime_mode_changed(self.managed.isChecked())

    def _runtime_mode_changed(self, enabled):
        self.device.setEnabled(enabled)
        self.external_endpoint_group.setVisible(not enabled)
        self.endpoint.setEnabled(not enabled)
        self.model.setEnabled(not enabled)
        self.check_endpoint.setEnabled(not enabled)

    def _download_models(self):
        if self.download_active:
            return
        self.download_active = True
        self._download_cancel.clear()
        self.download.setEnabled(False)
        self.cancel_download_button.setEnabled(True)
        self.download_status.setText("Checking local models…")
        def acquire():
            last_progress = 0.0
            def progress(value):
                nonlocal last_progress
                now = time.monotonic()
                if value.stage == "complete" or now - last_progress >= 0.1:
                    if not self._closed:
                        self.download_progress.emit(value)
                    last_progress = now
            try:
                self._model_store.ensure_models(cancel=self._download_cancel, progress=progress)
                message = "Local models installed and verified. Save settings to use them; the first recording loads the model."
            except Exception as exc:
                message = str(exc)
            if not self._closed:
                self.result.emit("download", message)
        Thread(target=acquire, daemon=True, name="voxd-model-download").start()

    def _progress(self, progress):
        if self._download_cancel.is_set():
            return
        self.progress.setValue(int(100 * progress.downloaded_bytes / progress.total_bytes))
        stage = progress.stage.capitalize()
        if progress.attempt > 1:
            stage += f" (retry {progress.attempt - 1})"
        self.download_status.setText(
            f"{stage}: {progress.downloaded_bytes / 1e9:.2f} / {progress.total_bytes / 1e9:.2f} GB"
        )

    def cancel_download(self):
        self._download_cancel.set()
        self.cancel_download_button.setEnabled(False)
        self.download_status.setText("Cancelling; the in-progress transfer will be discarded. Waiting for the current network request…")

    def shutdown(self):
        self._closed = True
        self._download_cancel.set()

    def prepare_typing(self):
        if not self.setup_typing.isEnabled():
            return
        self.setup_typing.setEnabled(False)
        self.typing_status.setText("Preparing text insertion…")
        def prepare():
            try:
                from voxd.utils.setup_user import prepare_user_setup
                message = prepare_user_setup().message
            except Exception as exc:
                message = f"Text insertion setup failed: {exc}"
            if not self._closed:
                self.result.emit("typing", message)
        Thread(target=prepare, daemon=True, name="voxd-typing-setup").start()

    def _check_endpoint(self):
        self.check_endpoint.setEnabled(False)
        endpoint, model = self.endpoint.text().strip(), self.model.text().strip()
        self.endpoint_status.setText("Checking model and sending one second of synthetic silence…")
        def check():
            try:
                message = validate_endpoint(endpoint, model)
            except Exception as exc:
                message = f"Endpoint check failed: {exc}"
            self.result.emit("endpoint", message)
        Thread(target=check, daemon=True).start()

    def _check_mic(self):
        self.check_mic.setEnabled(False)
        def check():
            status = probe_input(self.cfg.audio_input_device, self.cfg.audio_prefer_pulse)
            self.result.emit("mic", status.detail)
        Thread(target=check, daemon=True).start()

    def _result(self, kind, message):
        if kind == "endpoint":
            self.endpoint_status.setText(message)
            self.check_endpoint.setEnabled(not self.managed.isChecked())
        elif kind == "typing":
            self.typing_status.setText(message)
            self.setup_typing.setEnabled(True)
        elif kind == "download":
            self.download_active = False
            self.download.setEnabled(True)
            self.cancel_download_button.setEnabled(False)
            self.download_status.setText(message)
            if self.quit_after_download:
                self.quit_requested.emit()
        elif kind == "autostart":
            self.state.setText(message)
            self.save_button.setEnabled(True)
        else:
            self.mic_status.setText(message)
            self.check_mic.setEnabled(True)

    def _save(self):
        try:
            save_settings(self.cfg, preferences=self.preferences.toPlainText(),
                          endpoint=self.endpoint.text(), model=self.model.text(),
                          autostart=self.autostart.isChecked(),
                          managed_runtime=self.managed.isChecked(), runtime_device=self.device.currentData())
            from voxd.__main__ import _apply_autostart
            self.save_button.setEnabled(False)
            self.state.setText("Settings saved. Applying start at login…")
            enabled = self.cfg.autostart
            def apply_login_setting():
                try:
                    ok = _apply_autostart(enabled) == 0
                except OSError:
                    ok = False
                message = ("Settings saved. Start at login updated. Changes apply to your next recording."
                           if ok else "Settings saved, but start at login is incomplete. Retry Save; "
                           "the previous login configuration may still be active.")
                self.result.emit("autostart", message)
            # Reapply even an unchanged preference so existing service users migrate.
            Thread(target=apply_login_setting, daemon=True).start()
        except (ValueError, OSError, RuntimeError) as exc:
            self.save_button.setEnabled(True)
            QMessageBox.warning(self, "Settings", str(exc))
            return
