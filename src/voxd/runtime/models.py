"""Pinned model acquisition. Call ensure_models from a background worker.

Interrupted transfers restart from byte zero. No partial file is ever treated as
an installed model, and no response is appended using an unverified Range.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests


MODEL_BASE_URL = (
    "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/"
    "653803f092503c04a65164346f3208a36e707693/"
)


@dataclass(frozen=True)
class ModelArtifact:
    filename: str
    size: int
    sha256: str


MODEL_ARTIFACTS = (
    ModelArtifact(
        "gemma-4-E4B-it-Q8_0.gguf", 8192951456,
        "a2232a649523c36bf530f1dc3614eb8c800645c4227390381c8b05d4d6eee05a",
    ),
    ModelArtifact(
        "mmproj-F16.gguf", 990372672,
        "ddf46c21d7078e95338cfc22306b19b276a29a5ad089023449dd54d4b6170a51",
    ),
)


class ModelDownloadError(RuntimeError):
    """A model could not be acquired or verified; safe to retry setup."""


class DownloadCancelled(ModelDownloadError):
    """The caller cancelled model setup or runtime readiness."""


@dataclass(frozen=True)
class DownloadProgress:
    filename: str
    downloaded_bytes: int
    total_bytes: int
    stage: str
    attempt: int = 1


@dataclass(frozen=True)
class ModelPaths:
    model: Path
    projector: Path


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise DownloadCancelled("Model setup cancelled. Retry to restart the download.")


class ModelStore:
    """Install two verified files into the app's own model directory.

    Verification receipts avoid hashing nine GB at every shortcut press. A
    receipt is accepted only for the exact pinned digest and unchanged file
    identity, size and timestamps. Unreceipted existing files are hashed first.
    """

    def __init__(
        self, model_dir, *, session=None, artifacts=MODEL_ARTIFACTS,
        base_url=MODEL_BASE_URL, attempts: int = 3,
    ):
        self.model_dir = Path(model_dir)
        self.artifacts = tuple(artifacts)
        if len(self.artifacts) != 2 or any(
            Path(a.filename).name != a.filename or a.size <= 0
            for a in self.artifacts
        ):
            raise ValueError("Expected target and projector artifacts with plain filenames")
        if attempts < 1:
            raise ValueError("attempts must be positive")
        self.total_bytes = sum(a.size for a in self.artifacts)
        self.base_url = base_url
        self.attempts = attempts
        self.session = session if session is not None else requests.Session()
        self._lock = threading.Lock()

    @property
    def model_paths(self) -> ModelPaths:
        return ModelPaths(*(self.model_dir / a.filename for a in self.artifacts))

    def _receipt_path(self, artifact) -> Path:
        return self.model_dir / (artifact.filename + ".verified.json")

    def _identity(self, artifact) -> dict:
        stat = (self.model_dir / artifact.filename).stat()
        return {
            "sha256": artifact.sha256, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
            "inode": stat.st_ino,
        }

    def _verified(self, artifact) -> bool:
        try:
            identity = self._identity(artifact)
            return identity["size"] == artifact.size and json.loads(
                self._receipt_path(artifact).read_text(encoding="utf-8")
            ) == identity
        except (OSError, ValueError):
            return False

    def installed(self) -> bool:
        """Fast check for unchanged, previously hash-verified model files."""
        return all(self._verified(a) for a in self.artifacts)

    def _write_receipt(self, artifact) -> None:
        path = self._receipt_path(artifact)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.model_dir,
                prefix=path.name + ".", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(self._identity(artifact), output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _validate_existing(self, artifact, cancel) -> bool:
        if self._verified(artifact):
            return True
        path = self.model_dir / artifact.filename
        if not path.is_file() or path.stat().st_size != artifact.size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            while chunk := source.read(1024 * 1024):
                _check_cancel(cancel)
                digest.update(chunk)
            after = os.fstat(source.fileno())
        if digest.hexdigest() != artifact.sha256 or (
            before.st_mtime_ns, before.st_ctime_ns, before.st_size, before.st_ino
        ) != (
            after.st_mtime_ns, after.st_ctime_ns, after.st_size, after.st_ino
        ) or path.stat() != after:
            return False
        self._write_receipt(artifact)
        return True

    def ensure_models(
        self, *, cancel: threading.Event | None = None,
        progress: Callable[[DownloadProgress], None] | None = None,
    ) -> ModelPaths:
        cancel = cancel if cancel is not None else threading.Event()
        while not self._lock.acquire(timeout=0.1):
            _check_cancel(cancel)
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            verified = 0
            pending = []
            for artifact in self.artifacts:
                _check_cancel(cancel)
                self._notify(progress, artifact, verified, "checking")
                if self._validate_existing(artifact, cancel):
                    verified += artifact.size
                else:
                    pending.append(artifact)
            # Preflight the entire remaining download before transferring GBs.
            # Existing files stay in place until their replacements are verified.
            required = sum(a.size for a in pending) + 64 * 1024 * 1024
            if pending and shutil.disk_usage(self.model_dir).free < required:
                raise ModelDownloadError(
                    "Not enough disk space to download the local models. "
                    f"Free at least {required / 1e9:.2f} GB in {self.model_dir}."
                )
            for artifact in pending:
                self._download(artifact, verified, cancel, progress)
                verified += artifact.size
            self._notify(progress, self.artifacts[-1], verified, "complete")
            return self.model_paths
        except OSError as exc:
            raise ModelDownloadError(f"Could not store models in {self.model_dir}: {exc}") from exc
        finally:
            self._lock.release()

    def _notify(self, callback, artifact, count, stage, attempt=1):
        if callback is not None:
            callback(DownloadProgress(artifact.filename, count, self.total_bytes, stage, attempt))

    def _download(self, artifact, verified, cancel, progress):
        last_error = None
        for attempt in range(1, self.attempts + 1):
            _check_cancel(cancel)
            temporary = None
            try:
                self._notify(progress, artifact, verified, "downloading", attempt)
                with self.session.get(
                    self.base_url + artifact.filename, stream=True, timeout=(10, 30),
                ) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ModelDownloadError("The model server did not return a complete file")
                    digest = hashlib.sha256()
                    downloaded = 0
                    with tempfile.NamedTemporaryFile(
                        mode="wb", dir=self.model_dir,
                        prefix=artifact.filename + ".", suffix=".part", delete=False,
                    ) as output:
                        temporary = Path(output.name)
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            _check_cancel(cancel)
                            if not chunk:
                                continue
                            downloaded += len(chunk)
                            if downloaded > artifact.size:
                                raise ModelDownloadError("Model download exceeded its pinned size")
                            output.write(chunk)
                            digest.update(chunk)
                            self._notify(progress, artifact, verified + downloaded, "downloading", attempt)
                        _check_cancel(cancel)
                        if downloaded != artifact.size or digest.hexdigest() != artifact.sha256:
                            raise ModelDownloadError("Model size or SHA256 verification failed")
                        output.flush()
                        os.fsync(output.fileno())
                    _check_cancel(cancel)
                    os.replace(temporary, self.model_dir / artifact.filename)
                    self._write_receipt(artifact)
                    return
            except DownloadCancelled:
                raise
            except (requests.RequestException, ModelDownloadError) as exc:
                last_error = exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            if attempt < self.attempts:
                cancel.wait(min(2 ** (attempt - 1), 4))
        raise ModelDownloadError(
            f"Could not download {artifact.filename} after {self.attempts} attempts: "
            f"{last_error}. Check your connection and retry; incomplete files are discarded."
        ) from last_error
