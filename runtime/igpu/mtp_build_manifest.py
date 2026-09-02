#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


SCHEMA = 2
ATOMIC_SOURCE_COMMIT = "0a635dcd92ba66c75fccfef91c3e106f4668f367"
MTP_HEAD_NAME = "gemma-4-E4B-it-assistant.Q8_0.gguf"
MTP_HEAD_SHA256 = "eb576734fe210b551d091761fe83ab701c8e01ff708015a51172a4c0b04459e3"
CMAKE_OPTIONS = [
    "GGML_VULKAN=ON",
    "GGML_CCACHE=OFF",
    "LLAMA_CURL=OFF",
    "LLAMA_BUILD_SERVER=ON",
    "LLAMA_BUILD_TESTS=OFF",
    "LLAMA_BUILD_EXAMPLES=OFF",
    "LLAMA_BUILD_TOOLS=ON",
    "CMAKE_BUILD_TYPE=Release",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_files(runtime_dir: Path) -> dict[str, dict[str, str]]:
    paths = [runtime_dir / "llama-server", *sorted(runtime_dir.glob("lib*.so*"))]
    files: dict[str, dict[str, str]] = {}
    for path in paths:
        if path.parent != runtime_dir or path.name in files:
            raise SystemExit(f"invalid or duplicate runtime file: {path}")
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).name != target:
                raise SystemExit(f"runtime symlink must have a local filename target: {path}")
            files[path.name] = {"type": "symlink", "target": target}
        elif path.is_file():
            files[path.name] = {"type": "file", "sha256": sha256(path)}
        else:
            raise SystemExit(f"missing runtime file: {path}")

    if len(files) == 1:
        raise SystemExit(f"no shared libraries found in runtime directory: {runtime_dir}")
    for name, entry in files.items():
        if entry["type"] == "symlink" and entry["target"] not in files:
            raise SystemExit(f"runtime symlink target is not manifested: {name}")
    return files


def expected_payload(runtime_dir: Path, patch: Path) -> dict:
    return {
        "schema": SCHEMA,
        "source_commit": ATOMIC_SOURCE_COMMIT,
        "patch_sha256": sha256(patch),
        "cmake_options": CMAKE_OPTIONS,
        "mtp_head": {"name": MTP_HEAD_NAME, "sha256": MTP_HEAD_SHA256},
        "runtime_files": runtime_files(runtime_dir),
    }


def create_manifest(runtime_dir: Path, patch: Path, output: Path) -> None:
    payload = expected_payload(runtime_dir, patch)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    output.chmod(0o644)


def read_manifest(manifest: Path) -> dict:
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid MTP build manifest: {exc}") from exc


def verify_manifest(
    runtime_dir: Path, patch: Path, manifest: Path, mtp_head: Path | None
) -> dict:
    payload = read_manifest(manifest)
    if payload != expected_payload(runtime_dir, patch):
        raise SystemExit(
            "MTP build manifest does not match the runtime files, patch, or build contract"
        )
    if mtp_head is not None and sha256(mtp_head) != MTP_HEAD_SHA256:
        raise SystemExit("MTP assistant GGUF does not match the validated artifact")
    return payload


def stage_runtime(
    source_dir: Path,
    destination_dir: Path,
    patch: Path,
    manifest: Path,
    mtp_head: Path,
) -> None:
    payload = verify_manifest(source_dir, patch, manifest, mtp_head)
    if not destination_dir.is_dir() or any(destination_dir.iterdir()):
        raise SystemExit(f"runtime staging directory must exist and be empty: {destination_dir}")

    for name, entry in payload["runtime_files"].items():
        source = source_dir / name
        destination = destination_dir / name
        if entry["type"] == "symlink":
            destination.symlink_to(entry["target"])
        else:
            shutil.copy2(source, destination)
    staged_head = destination_dir / MTP_HEAD_NAME
    shutil.copyfile(mtp_head, staged_head)
    staged_head.chmod(0o600)
    staged_manifest = destination_dir / manifest.name
    shutil.copyfile(manifest, staged_manifest)
    staged_manifest.chmod(0o644)

    verify_manifest(destination_dir, patch, staged_manifest, staged_head)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("create", "verify", "stage"):
        child = subparsers.add_parser(command)
        child.add_argument("--runtime-dir", type=Path, required=True)
        child.add_argument("--patch", type=Path, required=True)
        child.add_argument("--manifest", type=Path, required=True)
        if command in ("verify", "stage"):
            child.add_argument("--mtp-head", type=Path, required=True)
        if command == "stage":
            child.add_argument("--destination-dir", type=Path, required=True)

    args = parser.parse_args()
    if not args.runtime_dir.is_dir():
        parser.error(f"missing runtime directory: {args.runtime_dir}")
    if not args.patch.is_file():
        parser.error(f"missing required file: {args.patch}")

    if args.command == "create":
        os.umask(0o022)
        create_manifest(args.runtime_dir, args.patch, args.manifest)
    else:
        if not args.manifest.is_file():
            parser.error(f"missing required file: {args.manifest}")
        if not args.mtp_head.is_file():
            parser.error(f"missing required file: {args.mtp_head}")
        if args.command == "verify":
            verify_manifest(args.runtime_dir, args.patch, args.manifest, args.mtp_head)
        else:
            stage_runtime(
                args.runtime_dir,
                args.destination_dir,
                args.patch,
                args.manifest,
                args.mtp_head,
            )


if __name__ == "__main__":
    main()
