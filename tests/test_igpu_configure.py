import importlib.util
import stat
from pathlib import Path

import pytest
import yaml


def _load_configure_module():
    script = Path(__file__).parents[1] / "runtime/igpu/configure_voxd.py"
    spec = importlib.util.spec_from_file_location("configure_voxd", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_igpu_endpoint_update_preserves_other_semantic_config(tmp_path):
    module = _load_configure_module()
    config = tmp_path / "voxd/config.yaml"
    config.parent.mkdir()
    config.write_text(
        "typing_delay: 2\n"
        "gemma_server_url: http://localhost:9292\n"
        "gemma_model: gemma-12b\n"
        "recording_archive_enabled: true\n",
        encoding="utf-8",
    )

    module.configure(config, "http://127.0.0.1:9394", "gemma-e4b")

    assert yaml.safe_load(config.read_text(encoding="utf-8")) == {
        "typing_delay": 2,
        "gemma_server_url": "http://127.0.0.1:9394",
        "gemma_model": "gemma-e4b",
        "gemma_segment_seconds": 10,
        "recording_archive_enabled": True,
    }
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_igpu_endpoint_update_creates_minimal_valid_config(tmp_path):
    module = _load_configure_module()
    config = tmp_path / "voxd/config.yaml"

    module.configure(config, "http://127.0.0.1:9494", "gemma-e4b")

    assert yaml.safe_load(config.read_text(encoding="utf-8")) == {
        "gemma_server_url": "http://127.0.0.1:9494",
        "gemma_model": "gemma-e4b",
        "gemma_segment_seconds": 10,
    }
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


@pytest.mark.parametrize("content", ["gemma_model: [\n", "- not\n- a\n- mapping\n"])
def test_invalid_existing_config_is_rejected_without_modification(tmp_path, content):
    module = _load_configure_module()
    config = tmp_path / "voxd/config.yaml"
    config.parent.mkdir()
    config.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        module.configure(config, "http://127.0.0.1:9394", "gemma-e4b")

    assert config.read_text(encoding="utf-8") == content


def test_validation_accepts_missing_or_mapping_config_and_rejects_invalid(tmp_path):
    module = _load_configure_module()
    config = tmp_path / "voxd/config.yaml"

    module.validate(config)
    config.parent.mkdir()
    config.write_text("typing_delay: 2\n", encoding="utf-8")
    module.validate(config)
    config.write_text("typing_delay: [\n", encoding="utf-8")
    with pytest.raises(ValueError):
        module.validate(config)
