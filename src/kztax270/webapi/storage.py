"""Ephemeral, job-isolated filesystem storage for sensitive uploads and outputs."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kztax270.config import ProjectPaths


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


@dataclass(frozen=True, slots=True)
class WebApiSettings:
    job_root: Path
    max_upload_bytes: int
    max_files: int
    job_ttl_seconds: int
    cors_origins: tuple[str, ...]
    project_paths: ProjectPaths = field(default_factory=ProjectPaths)

    @property
    def max_upload_mb(self) -> float:
        return self.max_upload_bytes / (1024 * 1024)

    @classmethod
    def from_env(cls) -> WebApiSettings:
        max_upload_mb = _positive_float("QCM_MAX_UPLOAD_MB", 50.0)
        origins = tuple(
            origin.strip()
            for origin in os.getenv("QCM_CORS_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        )
        root = Path(os.getenv("QCM_JOB_ROOT", str(Path(tempfile.gettempdir()) / "qcm-tax-270"))).expanduser()
        defaults = ProjectPaths()
        return cls(
            job_root=root.resolve(),
            max_upload_bytes=int(max_upload_mb * 1024 * 1024),
            max_files=_positive_int("QCM_MAX_FILES", 10),
            job_ttl_seconds=_positive_int("QCM_JOB_TTL_SECONDS", 900),
            cors_origins=origins,
            project_paths=ProjectPaths(
                raw_data=defaults.raw_data.resolve(),
                processed_data=defaults.processed_data.resolve(),
                output_data=defaults.output_data.resolve(),
                nbk_rates=defaults.nbk_rates.resolve(),
                reference_data=defaults.reference_data.resolve(),
                form270_template=defaults.form270_template.resolve(),
            ),
        )


@dataclass(frozen=True, slots=True)
class JobWorkspace:
    job_id: str
    root: Path
    input_dir: Path
    processed_dir: Path
    output_dir: Path

    def project_paths(self, permanent_paths: ProjectPaths) -> ProjectPaths:
        return ProjectPaths(
            raw_data=self.input_dir,
            processed_data=self.processed_dir,
            output_data=self.output_dir,
            nbk_rates=permanent_paths.nbk_rates,
            reference_data=permanent_paths.reference_data,
            form270_template=permanent_paths.form270_template,
        )


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    root: Path
    audit_path: Path
    form270_path: Path
    form270_media_type: str
    expires_at: float


class JobStore:
    """In-memory job index backed by isolated temporary directories."""

    def __init__(
        self,
        root: Path,
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root.resolve()
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._active: dict[str, JobWorkspace] = {}
        self._completed: dict[str, JobRecord] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def create_workspace(self) -> JobWorkspace:
        self.cleanup_expired()
        with self._lock:
            job_id = str(uuid.uuid4())
            root = self.root / job_id
            workspace = JobWorkspace(
                job_id=job_id,
                root=root,
                input_dir=root / "input",
                processed_dir=root / "processed",
                output_dir=root / "output",
            )
            workspace.input_dir.mkdir(parents=True)
            workspace.processed_dir.mkdir()
            workspace.output_dir.mkdir()
            self._active[job_id] = workspace
            return workspace

    def complete(
        self,
        workspace: JobWorkspace,
        *,
        audit_path: Path,
        form270_path: Path,
        form270_media_type: str,
    ) -> JobRecord:
        with self._lock:
            self._require_managed_path(workspace, audit_path)
            self._require_managed_path(workspace, form270_path)
            self._remove_inputs(workspace)
            record = JobRecord(
                job_id=workspace.job_id,
                root=workspace.root,
                audit_path=audit_path,
                form270_path=form270_path,
                form270_media_type=form270_media_type,
                expires_at=self._clock() + self.ttl_seconds,
            )
            self._active.pop(workspace.job_id, None)
            self._completed[workspace.job_id] = record
            return record

    def discard(self, workspace: JobWorkspace) -> None:
        with self._lock:
            self._active.pop(workspace.job_id, None)
            self._completed.pop(workspace.job_id, None)
            self._remove_job_root(workspace.root)

    def get(self, job_id: str) -> JobRecord | None:
        self.cleanup_expired()
        with self._lock:
            record = self._completed.get(job_id)
            if record is None:
                return None
            if not record.root.exists():
                self._completed.pop(job_id, None)
                return None
            return record

    def delete(self, job_id: str) -> None:
        with self._lock:
            workspace = self._active.pop(job_id, None)
            record = self._completed.pop(job_id, None)
            root = workspace.root if workspace is not None else record.root if record is not None else None
            if root is not None:
                self._remove_job_root(root)

    def cleanup_expired(self) -> int:
        now = self._clock()
        removed = 0
        with self._lock:
            expired = [job_id for job_id, record in self._completed.items() if record.expires_at <= now]
            for job_id in expired:
                record = self._completed.pop(job_id)
                self._remove_job_root(record.root)
                removed += 1

            known = set(self._active) | set(self._completed)
            cutoff = now - self.ttl_seconds
            for child in self.root.iterdir():
                if not child.is_dir() or child.name in known or not _is_uuid(child.name):
                    continue
                try:
                    is_stale = child.stat().st_mtime <= cutoff
                except FileNotFoundError:
                    continue
                if is_stale:
                    self._remove_job_root(child)
                    removed += 1
        return removed

    def _remove_inputs(self, workspace: JobWorkspace) -> None:
        input_path = workspace.input_dir.resolve()
        if input_path != workspace.root.resolve() and input_path.is_relative_to(workspace.root.resolve()):
            shutil.rmtree(input_path, ignore_errors=True)

    def _remove_job_root(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.root or not _is_uuid(resolved.name):
            raise ValueError("Refusing to remove an unmanaged job directory")
        shutil.rmtree(resolved, ignore_errors=True)

    @staticmethod
    def _require_managed_path(workspace: JobWorkspace, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace.root.resolve()) or not resolved.is_file():
            raise ValueError("Pipeline output is missing or outside the job directory")


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False
