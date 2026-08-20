#!/usr/bin/env python3
"""Atomically configure VOXD for one validated inference runtime."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml


def _load_mapping(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not parse existing VOXD config: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("existing VOXD config must contain a top-level mapping")
    return loaded


def configure(config_path: Path, endpoint: str, model: str) -> None:
    if not endpoint.strip() or not model.strip():
        raise ValueError("endpoint and model must be non-empty")

    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    configured = dict(_load_mapping(config_path))
    configured["gemma_server_url"] = endpoint
    configured["gemma_model"] = model

    serialized = yaml.safe_dump(configured, default_flow_style=False, sort_keys=False)
    verified = yaml.safe_load(serialized)
    if not isinstance(verified, dict):
        raise ValueError("rewritten VOXD config is not a top-level mapping")
    if verified.get("gemma_server_url") != endpoint:
        raise ValueError("rewritten VOXD config has the wrong Gemma endpoint")
    if verified.get("gemma_model") != model:
        raise ValueError("rewritten VOXD config has the wrong Gemma model")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.",
        suffix=".tmp",
        dir=config_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, config_path)
        config_path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate(config_path: Path) -> None:
    _load_mapping(Path(config_path))


def main() -> None:
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--check":
            validate(Path(sys.argv[2]))
        elif len(sys.argv) == 4:
            configure(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
        else:
            raise SystemExit(
                f"usage: {sys.argv[0]} --check CONFIG_PATH | "
                "CONFIG_PATH ENDPOINT MODEL"
            )
    except ValueError as exc:
        raise SystemExit(f"could not configure VOXD: {exc}") from exc


if __name__ == "__main__":
    main()
