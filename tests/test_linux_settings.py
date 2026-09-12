from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolate_login_configuration(monkeypatch):
    # Settings saves must never call the host's user service manager in UI tests.
    import voxd.__main__ as main_mod
    monkeypatch.setattr(main_mod, "_apply_autostart", lambda enabled: 0)


def test_settings_save_preserves_empty_preference_and_normalizes_endpoint():
    from voxd.core.config import AppConfig
    from voxd.tray.settings import save_settings
    cfg = AppConfig()
    save_settings(cfg, preferences="", endpoint=" http://localhost:9292/ ", model=" gemma-e4b ", autostart=False, managed_runtime=False)
    reloaded = AppConfig()
    assert reloaded.speech_preferences == ""
    assert reloaded.gemma_server_url == "http://localhost:9292"
    assert reloaded.gemma_model == "gemma-e4b"
    assert reloaded.setup_completed is True


@pytest.mark.parametrize("endpoint,model", [("/model/path", "model"), ("ftp://host", "model"), ("http://host", "")])
def test_invalid_settings_do_not_mutate_config(endpoint, model):
    from voxd.core.config import AppConfig
    from voxd.tray.settings import save_settings
    cfg = AppConfig()
    before = dict(cfg.data)
    with pytest.raises(ValueError):
        save_settings(cfg, preferences="new", endpoint=endpoint, model=model, autostart=True, managed_runtime=False)
    assert cfg.data == before


def test_endpoint_check_requires_configured_model(monkeypatch):
    import requests
    from voxd.tray.settings import validate_endpoint
    class Session:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, url, timeout):
            assert url == "http://endpoint/v1/models"
            assert timeout == (2, 4)
            assert self.trust_env is False
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "different"}]})
    monkeypatch.setattr(requests, "Session", Session)
    with pytest.raises(ValueError, match="not listed"):
        validate_endpoint("http://endpoint", "gemma-e4b")


def test_endpoint_check_exercises_real_audio_protocol_without_typing(monkeypatch):
    import base64
    import io
    import wave
    import requests
    from voxd.tray.settings import validate_endpoint
    calls = []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, *_args, **_kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "gemma-e4b"}]})
        def post(self, url, json, timeout):
            calls.append(json)
            assert timeout == 20
            assert url.endswith("/v1/chat/completions")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": ""}}]})
    monkeypatch.setattr(requests, "Session", Session)
    message = validate_endpoint("http://endpoint", "gemma-e4b")
    audio_part = calls[0]["messages"][0]["content"][-1]
    assert audio_part["type"] == "input_audio"
    with wave.open(io.BytesIO(base64.b64decode(audio_part["input_audio"]["data"])), "rb") as audio:
        assert audio.getnframes() == 16000
        assert audio.readframes(16000) == bytes(32000)
    assert "synthetic silence" in message and "dictation test" in message


def test_managed_settings_save_separate_backend_choice():
    from voxd.core.config import AppConfig
    from voxd.tray.settings import save_settings
    cfg = AppConfig()
    save_settings(cfg, preferences="I mix English and Tamil", endpoint=cfg.gemma_server_url,
        model=cfg.gemma_model, autostart=False, managed_runtime=True, runtime_device="vulkan")
    saved = AppConfig()
    assert saved.managed_runtime_enabled is True and saved.runtime_device == "vulkan"
    assert saved.speech_preferences == "I mix English and Tamil"


_QAPP = None


def test_download_ui_cancellation_and_completion_are_real_worker_states(monkeypatch):
    global _QAPP
    from PyQt6.QtWidgets import QApplication
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray import settings
    from voxd.runtime.models import DownloadCancelled, DownloadProgress
    _QAPP = QApplication.instance() or QApplication([])
    workers = []
    monkeypatch.setattr(settings, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: workers.append(kwargs["target"])))
    class Store:
        def __init__(self, *_): pass
        def installed(self): return False
        def ensure_models(self, *, cancel, progress):
            assert cancel.is_set()
            progress(DownloadProgress("model", 1, 2, "downloading"))
            raise DownloadCancelled("Model setup cancelled. Retry to restart the download.")
    monkeypatch.setattr(settings, "ModelStore", Store)
    window = settings.SettingsWindow(AppConfig(), PortalShortcuts())
    window._download_models()
    assert window.download_active
    window.cancel_download()
    assert window.download_active  # Cancellation request is not a finished transfer.
    workers[0]()
    assert not window.download_active
    assert "cancelled" in window.download_status.text()
    assert window.download.isEnabled()
    window.shutdown()
    window.close()


def test_local_mode_hides_only_external_endpoint_group():
    global _QAPP
    from PyQt6.QtWidgets import QApplication
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray.settings import SettingsWindow
    _QAPP = QApplication.instance() or QApplication([])
    cfg = AppConfig()
    cfg.set("managed_runtime_enabled", True)
    window = SettingsWindow(cfg, PortalShortcuts())
    assert window.external_endpoint_group.isHidden()
    assert not window.endpoint.isEnabled()
    assert not window.model.isEnabled()
    assert not window.check_endpoint.isEnabled()
    assert not window.preferences.isHidden()
    window._result("endpoint", "A pending endpoint test finished")
    assert not window.check_endpoint.isEnabled()
    window.managed.setChecked(False)
    assert not window.external_endpoint_group.isHidden()
    assert window.endpoint.isEnabled()
    assert window.model.isEnabled()
    assert window.check_endpoint.isEnabled()
    assert not window.preferences.isHidden()
    window.shutdown()
    window.close()


def test_switching_to_local_ignores_unfinished_external_edits(monkeypatch):
    global _QAPP
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray import settings
    from voxd.tray.settings import SettingsWindow
    monkeypatch.setattr(settings, "Thread", lambda **kwargs: SimpleNamespace(start=kwargs["target"]))
    _QAPP = QApplication.instance() or QApplication([])
    cfg = AppConfig()
    cfg.set("managed_runtime_enabled", False)
    cfg.set("gemma_server_url", "http://existing:9292")
    cfg.set("gemma_model", "existing-model")
    window = SettingsWindow(cfg, PortalShortcuts())
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    window.endpoint.clear()
    window.model.clear()
    window.managed.setChecked(True)
    window._save()
    saved = AppConfig()
    assert errors == []
    assert saved.managed_runtime_enabled is True
    assert saved.gemma_server_url == "http://existing:9292"
    assert saved.gemma_model == "existing-model"
    window.managed.setChecked(False)
    window._save()
    assert errors and "endpoint URL" in errors[-1]
    assert AppConfig().managed_runtime_enabled is True
    window.endpoint.setText("http://replacement:9292")
    window.model.setText("replacement-model")
    window._save()
    saved = AppConfig()
    assert saved.managed_runtime_enabled is False
    assert saved.gemma_server_url == "http://replacement:9292"
    assert saved.gemma_model == "replacement-model"
    window.shutdown()
    window.close()


def test_local_save_preserves_malformed_legacy_external_endpoint():
    from voxd.core.config import AppConfig
    from voxd.tray.settings import save_settings
    cfg = AppConfig()
    cfg.set("gemma_server_url", "/legacy-invalid-endpoint")
    cfg.set("gemma_model", "legacy-model")
    save_settings(cfg, preferences="I mix Hindi and English", endpoint="", model="",
        autostart=False, managed_runtime=True)
    saved = AppConfig()
    assert saved.managed_runtime_enabled is True
    assert saved.gemma_server_url == "/legacy-invalid-endpoint"
    assert saved.gemma_model == "legacy-model"
    assert saved.speech_preferences == "I mix Hindi and English"


@pytest.mark.parametrize("enabled", [True, False])
def test_every_settings_save_reapplies_unchanged_login_preference(monkeypatch, enabled):
    global _QAPP
    from PyQt6.QtWidgets import QApplication
    import voxd.__main__ as main_mod
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray import settings
    _QAPP = QApplication.instance() or QApplication([])
    cfg = AppConfig()
    cfg.set("autostart", enabled)
    cfg.set("managed_runtime_enabled", True)
    workers, calls = [], []
    monkeypatch.setattr(settings, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: workers.append(kwargs["target"])))
    monkeypatch.setattr(main_mod, "_apply_autostart", lambda value: calls.append(value) or 0)
    window = settings.SettingsWindow(cfg, PortalShortcuts())
    for _ in range(2):
        window._save()
        assert not window.save_button.isEnabled()
        assert "Applying start at login" in window.state.text()
        workers.pop(0)()
        assert window.save_button.isEnabled()
        assert "Start at login updated" in window.state.text()
    assert calls == [enabled, enabled]
    window.shutdown()
    window.close()


def test_settings_shows_incomplete_login_application_and_allows_retry(monkeypatch):
    global _QAPP
    from PyQt6.QtWidgets import QApplication
    import voxd.__main__ as main_mod
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray import settings
    _QAPP = QApplication.instance() or QApplication([])
    cfg = AppConfig()
    cfg.set("managed_runtime_enabled", True)
    monkeypatch.setattr(settings, "Thread", lambda **kwargs: SimpleNamespace(start=kwargs["target"]))
    monkeypatch.setattr(main_mod, "_apply_autostart", lambda value: 1)
    window = settings.SettingsWindow(cfg, PortalShortcuts())
    window._save()
    assert "start at login is incomplete" in window.state.text()
    assert "Retry Save" in window.state.text()
    assert window.save_button.isEnabled()
    window.shutdown()
    window.close()


def test_config_save_failure_does_not_change_login_configuration(monkeypatch):
    global _QAPP
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray import settings
    _QAPP = QApplication.instance() or QApplication([])
    cfg = AppConfig()
    cfg.set("managed_runtime_enabled", True)
    workers, errors = [], []
    monkeypatch.setattr(settings, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: workers.append(kwargs["target"])))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    def fail_save():
        raise OSError("No space left")
    monkeypatch.setattr(cfg, "save", fail_save)
    window = settings.SettingsWindow(cfg, PortalShortcuts())
    window._save()
    assert errors == ["No space left"]
    assert not workers
    assert window.save_button.isEnabled()
    window.shutdown()
    window.close()


def test_manual_shortcut_field_preserves_special_characters(monkeypatch):
    global _QAPP
    import shlex
    from PyQt6.QtWidgets import QApplication, QLineEdit
    from voxd import branding
    from voxd.core.config import AppConfig
    from voxd.platforms.linux.shortcuts import PortalShortcuts
    from voxd.tray.settings import SettingsWindow
    _QAPP = QApplication.instance() or QApplication([])
    executable = '/tmp/50%/$voice/with "quotes" and \\ spaces/thoughtloud'
    monkeypatch.setattr(branding, "command_argv", lambda: [executable])
    window = SettingsWindow(AppConfig(), PortalShortcuts())
    commands = [field.text() for field in window.findChildren(QLineEdit) if field.isReadOnly()]
    assert len(commands) == 1
    assert shlex.split(commands[0]) == [executable, "--trigger-record"]
    window.shutdown()
    window.close()
