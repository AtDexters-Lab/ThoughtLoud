from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import yaml
from platformdirs import user_config_dir

DEFAULT_CONFIG = {
    "verbosity": False,
    "autostart": False,
    "setup_completed": False,
    "hotkey_description": "",
    "managed_runtime_enabled": True,
    "runtime_device": "cpu",
    "speech_preferences": "",
    "typing_delay": 0,
    "typing_word_delay": 10,
    "typing_start_delay": 0.15,
    "append_trailing_space": True,
    "record_chunk_seconds": 300,
    "audio_prefer_pulse": True,
    "audio_input_device": "",
    "recording_archive_enabled": False,
    "recording_archive_max_mb": 5120,
    "gemma_server_url": "http://localhost:9292",
    "gemma_model": "gemma-e4b",
    "gemma_timeout": 300,
    "gemma_segment_seconds": 10,
    "gemma_max_tokens": 1024,
}

LEGACY_SPEECH_PREFERENCES = (
    "I speak Hindi or mixed Hindi-English. Transliterate every Hindi word into "
    "natural Roman Hinglish; preserve English words and do not translate the speech."
)

CONFIG_DIR = Path(user_config_dir("voxd"))
CONFIG_PATH = CONFIG_DIR / "config.yaml"


class AppConfig:
    """Small, strict configuration surface for the E4B tray runtime."""

    def __init__(self):
        self.data = deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        user_config = {}
        if CONFIG_PATH.exists():
            try:
                loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    user_config = loaded
                    # Existing users own their configured inference endpoint.
                    # Only fresh configurations opt into the bundled runtime.
                    user_config.setdefault("managed_runtime_enabled", False)
                    # Existing installations used a Hindi-English prompt. A
                    # newly written template has an explicit empty preference,
                    # while actual old configs missing the key retain that hint.
                    if "speech_preferences" not in user_config:
                        user_config["speech_preferences"] = LEGACY_SPEECH_PREFERENCES
                else:
                    print("[config] Ignoring config because its top level is not a mapping")
            except (OSError, yaml.YAMLError) as exc:
                print(f"[config] Could not read config; using defaults: {exc}")

        # The fixed-overlap pipeline shipped with this exact default pair.
        # Migrate only that recognizable legacy default; a different segment
        # length or overlap represents an intentional user configuration.
        try:
            legacy_segment = float(user_config.get("gemma_segment_seconds"))
            legacy_overlap = float(user_config.get("gemma_segment_overlap_seconds"))
        except (TypeError, ValueError):
            pass
        else:
            if legacy_segment == 15 and legacy_overlap == 3:
                user_config["gemma_segment_seconds"] = DEFAULT_CONFIG[
                    "gemma_segment_seconds"
                ]

        # Keep the saved config aligned with the intentionally small public
        # surface by dropping unknown keys on the next save.
        for key in DEFAULT_CONFIG:
            if key in user_config:
                self.data[key] = user_config[key]

        self._normalize()
        self._sync_attributes()

        if user_config != self.data:
            self.save()

    def _normalize(self) -> None:
        self.data["typing_delay"] = self._number("typing_delay", minimum=0, maximum=1000)
        self.data["typing_word_delay"] = self._number(
            "typing_word_delay", minimum=0, maximum=1000
        )
        self.data["typing_start_delay"] = self._number(
            "typing_start_delay", minimum=0, maximum=10
        )
        self.data["record_chunk_seconds"] = int(
            self._number("record_chunk_seconds", minimum=30, maximum=3600)
        )
        self.data["recording_archive_max_mb"] = int(
            self._number("recording_archive_max_mb", minimum=100, maximum=1_000_000)
        )
        self.data["gemma_timeout"] = self._number(
            "gemma_timeout", minimum=1, maximum=3600
        )
        self.data["gemma_max_tokens"] = int(
            self._number("gemma_max_tokens", minimum=1, maximum=16384)
        )

        segment = self._number("gemma_segment_seconds", minimum=2.1, maximum=27.9)
        self.data["gemma_segment_seconds"] = segment

        for key in (
            "verbosity",
            "autostart",
            "setup_completed",
            "managed_runtime_enabled",
            "append_trailing_space",
            "audio_prefer_pulse",
            "recording_archive_enabled",
        ):
            if not isinstance(self.data[key], bool):
                self.data[key] = DEFAULT_CONFIG[key]

        for key in (
            "audio_input_device",
            "gemma_server_url",
            "gemma_model",
        ):
            if not isinstance(self.data[key], str) or not self.data[key].strip():
                self.data[key] = DEFAULT_CONFIG[key]

        if self.data["runtime_device"] not in ("cpu", "vulkan"):
            self.data["runtime_device"] = "cpu"

        for key in ("speech_preferences", "hotkey_description"):
            # Empty is meaningful: it requests neutral transcription/no saved
            # portal binding. Preserve the user's freeform text as written.
            if not isinstance(self.data[key], str):
                self.data[key] = DEFAULT_CONFIG[key]

    def _number(self, key: str, *, minimum: float, maximum: float) -> float:
        try:
            value = float(self.data[key])
        except (TypeError, ValueError):
            return float(DEFAULT_CONFIG[key])
        if not minimum <= value <= maximum:
            return float(DEFAULT_CONFIG[key])
        return value

    def _sync_attributes(self) -> None:
        for key, value in self.data.items():
            setattr(self, key, value)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_PATH.with_suffix(".yaml.tmp")
        temporary.write_text(
            yaml.safe_dump(self.data, default_flow_style=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(CONFIG_PATH)

    def set(self, key: str, value) -> None:
        if key not in DEFAULT_CONFIG:
            raise KeyError(f"Unknown config key: {key}")
        self.data[key] = value
        self._normalize()
        self._sync_attributes()


def ensure_config_file() -> Path:
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        template = files("voxd.defaults").joinpath("default_config.yaml")
        CONFIG_PATH.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    return CONFIG_PATH


_APP_CONFIG = None


def get_config() -> AppConfig:
    global _APP_CONFIG
    if _APP_CONFIG is None:
        ensure_config_file()
        _APP_CONFIG = AppConfig()
    return _APP_CONFIG
