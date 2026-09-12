import yaml
import pytest


def test_gemma_transcription_defaults():
    from voxd.core.config import AppConfig

    cfg = AppConfig()
    assert cfg.gemma_server_url == "http://localhost:9292"
    assert cfg.gemma_model == "gemma-e4b"
    assert cfg.gemma_timeout == 300
    assert cfg.gemma_segment_seconds == 10
    assert cfg.typing_delay == 0
    assert cfg.typing_word_delay == 10
    assert cfg.recording_archive_enabled is False
    assert cfg.recording_archive_max_mb == 5120
    assert cfg.speech_preferences == ""
    assert cfg.managed_runtime_enabled is True
    assert "gemma_transcription_prompt" not in cfg.data


def test_get_config_template_keeps_new_install_language_neutral():
    from voxd.core.config import CONFIG_PATH, get_config

    cfg = get_config()

    assert cfg.speech_preferences == ""
    assert yaml.safe_load(CONFIG_PATH.read_text())["speech_preferences"] == ""
    assert cfg.managed_runtime_enabled is True


def test_existing_config_missing_preference_migrates_and_drops_mic_autoset():
    from voxd.core.config import AppConfig, CONFIG_PATH, LEGACY_SPEECH_PREFERENCES

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("typing_delay: 3\nmic_autoset_enabled: true\nmic_autoset_level: 0.45\n")

    cfg = AppConfig()
    saved = yaml.safe_load(CONFIG_PATH.read_text())

    assert cfg.speech_preferences == LEGACY_SPEECH_PREFERENCES
    assert cfg.managed_runtime_enabled is False
    assert saved["managed_runtime_enabled"] is False
    assert saved["speech_preferences"] == LEGACY_SPEECH_PREFERENCES
    assert cfg.typing_delay == 3
    assert "mic_autoset_enabled" not in saved
    assert "mic_autoset_level" not in saved
    assert AppConfig().speech_preferences == LEGACY_SPEECH_PREFERENCES


@pytest.mark.parametrize("preference", ["", "  ", "I mix Marathi and English.\nNames: पुणे"])
def test_explicit_speech_preference_round_trips_exactly(preference):
    from voxd.core.config import AppConfig, CONFIG_PATH

    cfg = AppConfig()
    cfg.set("speech_preferences", preference)
    cfg.save()

    assert AppConfig().speech_preferences == preference
    assert yaml.safe_load(CONFIG_PATH.read_text())["speech_preferences"] == preference


def test_invalid_speech_preference_is_neutral():
    from voxd.core.config import AppConfig

    cfg = AppConfig()
    cfg.set("speech_preferences", ["Marathi", "English"])

    assert cfg.speech_preferences == ""


def test_load_drops_unknown_keys_from_saved_config():
    from voxd.core.config import AppConfig, CONFIG_PATH

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(
            {
                "typing_delay": 3,
                "removed_option": "/old/value",
                "old_feature_enabled": True,
            }
        ),
        encoding="utf-8",
    )

    cfg = AppConfig()

    assert cfg.typing_delay == 3
    assert "removed_option" not in cfg.data
    assert "old_feature_enabled" not in cfg.data
    saved = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert set(saved) == set(cfg.data)


def test_load_migrates_legacy_default_segmentation_to_vad_target():
    from voxd.core.config import AppConfig, CONFIG_PATH

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(
            {
                "gemma_segment_seconds": 15,
                "gemma_segment_overlap_seconds": 3,
            }
        ),
        encoding="utf-8",
    )

    cfg = AppConfig()

    assert cfg.gemma_segment_seconds == 10
    saved = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["gemma_segment_seconds"] == 10
    assert "gemma_segment_overlap_seconds" not in saved


def test_load_preserves_custom_segmentation_during_legacy_key_cleanup():
    from voxd.core.config import AppConfig, CONFIG_PATH

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(
            {
                "gemma_segment_seconds": 12,
                "gemma_segment_overlap_seconds": 3,
            }
        ),
        encoding="utf-8",
    )

    cfg = AppConfig()

    assert cfg.gemma_segment_seconds == 12
    saved = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["gemma_segment_seconds"] == 12
    assert "gemma_segment_overlap_seconds" not in saved


def test_invalid_values_fall_back_to_safe_defaults():
    from voxd.core.config import AppConfig, CONFIG_PATH, DEFAULT_CONFIG

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(
            {
                "typing_delay": "fast",
                "typing_word_delay": -1,
                "gemma_segment_seconds": 30,
                "append_trailing_space": "yes",
                "recording_archive_enabled": "yes",
                "recording_archive_max_mb": 0,
            }
        ),
        encoding="utf-8",
    )

    cfg = AppConfig()

    assert cfg.typing_delay == DEFAULT_CONFIG["typing_delay"]
    assert cfg.typing_word_delay == DEFAULT_CONFIG["typing_word_delay"]
    assert cfg.gemma_segment_seconds == DEFAULT_CONFIG["gemma_segment_seconds"]
    assert cfg.append_trailing_space is DEFAULT_CONFIG["append_trailing_space"]
    assert cfg.recording_archive_enabled is DEFAULT_CONFIG["recording_archive_enabled"]
    assert cfg.recording_archive_max_mb == DEFAULT_CONFIG["recording_archive_max_mb"]


def test_segment_target_must_leave_room_for_the_vad_search_window():
    from voxd.core.config import AppConfig, CONFIG_PATH, DEFAULT_CONFIG

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(
            {
                "gemma_segment_seconds": 2,
            }
        ),
        encoding="utf-8",
    )

    cfg = AppConfig()

    assert cfg.gemma_segment_seconds == DEFAULT_CONFIG["gemma_segment_seconds"]
