"""Process-local job state and isolated temporary storage for the web API."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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
    max_job_files: int
    pending_job_ttl_seconds: int
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
            max_job_files=_positive_int("QCM_MAX_JOB_FILES", 50),
            pending_job_ttl_seconds=_positive_int("QCM_PENDING_JOB_TTL_SECONDS", 3600),
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
    internal_client_id: str
    root: Path
    input_dir: Path
    processed_dir: Path
    output_dir: Path

    @property
    def client_root(self) -> Path:
        return self.input_dir / "clients" / self.internal_client_id

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
class ArtifactRecord:
    artifact_id: str
    kind: Literal["account_audit", "joint_audit", "merged_audit", "form270"]
    path: Path
    filename: str
    media_type: str
    broker: str | None = None
    account_id: str | None = None


@dataclass(slots=True)
class JobRecord:
    workspace: JobWorkspace
    status: str
    expires_at: float
    uploads: dict[str, Path] = field(default_factory=dict)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)

    @property
    def job_id(self) -> str:
        return self.workspace.job_id


class DuplicateReportError(ValueError):
    pass


class JobFileLimitError(ValueError):
    pass


class ReportNotFoundError(ValueError):
    pass


class InvalidJobStateError(ValueError):
    pass


class JobStore:
    """Concurrency-safe-enough process-local state for one-instance MVP jobs."""

    def __init__(
        self,
        root: Path,
        pending_ttl_seconds: int,
        completed_ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root.resolve()
        self.pending_ttl_seconds = pending_ttl_seconds
        self.completed_ttl_seconds = completed_ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._expired_ids: set[str] = set()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> JobRecord:
        self.cleanup_expired()
        with self._lock:
            job_uuid = uuid.uuid4()
            job_id = str(job_uuid)
            root = self.root / job_id
            workspace = JobWorkspace(
                job_id=job_id,
                internal_client_id=f"job_{job_uuid.hex}",
                root=root,
                input_dir=root / "input",
                processed_dir=root / "processed",
                output_dir=root / "output",
            )
            workspace.client_root.mkdir(parents=True)
            workspace.processed_dir.mkdir()
            workspace.output_dir.mkdir()
            record = JobRecord(
                workspace=workspace,
                status="collecting",
                expires_at=self._clock() + self.pending_ttl_seconds,
            )
            self._jobs[job_id] = record
            self._expired_ids.discard(job_id)
            return record

    def get(self, job_id: str) -> JobRecord | None:
        self.cleanup_expired()
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or not record.workspace.root.exists():
                self._jobs.pop(job_id, None)
                return None
            return record

    def was_expired(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._expired_ids

    def add_uploads(
        self,
        job_id: str,
        uploads: Sequence[tuple[str, Path]],
        *,
        max_job_files: int,
    ) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status == "completed":
                raise InvalidJobStateError("completed")
            if record.status == "processing":
                raise InvalidJobStateError("processing")
            digests = [digest for digest, _path in uploads]
            if len(set(digests)) != len(digests) or any(digest in record.uploads for digest in digests):
                raise DuplicateReportError
            if len(record.uploads) + len(uploads) > max_job_files:
                raise JobFileLimitError
            record.uploads.update(uploads)
            record.status = "collecting"
            self._refresh_pending(record)
            return record

    def mark_discovered(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status == "completed":
                raise InvalidJobStateError("completed")
            if record.status == "processing":
                raise InvalidJobStateError("processing")
            record.status = "awaiting_options"
            self._refresh_pending(record)
            return record

    def remove_upload(self, job_id: str, report_id: str) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status == "completed":
                raise InvalidJobStateError("completed")
            if record.status == "processing":
                raise InvalidJobStateError("processing")
            path = record.uploads.get(report_id)
            if path is None:
                raise ReportNotFoundError(report_id)
            if path.exists():
                self._require_managed_file(record.workspace, path)
                path.unlink()
            del record.uploads[report_id]
            parent = path.parent.resolve()
            if parent.parent == record.workspace.client_root.resolve() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            record.status = "collecting"
            self._refresh_pending(record)
            return record

    def prepare_processing(self, job_id: str) -> tuple[JobRecord, str]:
        with self._lock:
            record = self._require(job_id)
            if record.status == "completed":
                raise InvalidJobStateError("completed")
            if record.status == "processing":
                raise InvalidJobStateError("processing")
            if record.status not in {"awaiting_options", "needs_additional_reports"}:
                raise InvalidJobStateError(record.status)
            previous_status = record.status
            self._clear_directory(record.workspace.processed_dir)
            self._clear_directory(record.workspace.output_dir)
            record.artifacts.clear()
            record.status = "processing"
            self._refresh_pending(record)
            return record, previous_status

    def processing_failed(self, job_id: str, previous_status: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status != "processing":
                return
            record.status = previous_status
            self._refresh_pending(record)

    def needs_additional_reports(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status != "processing":
                raise InvalidJobStateError(record.status)
            record.status = "needs_additional_reports"
            self._refresh_pending(record)
            return record

    def complete(self, job_id: str, artifacts: Sequence[ArtifactRecord]) -> JobRecord:
        with self._lock:
            record = self._require(job_id)
            if record.status != "processing":
                raise InvalidJobStateError(record.status)
            for artifact in artifacts:
                self._require_managed_file(record.workspace, artifact.path)
            self._remove_inputs(record.workspace)
            record.uploads.clear()
            record.artifacts = {artifact.artifact_id: artifact for artifact in artifacts}
            record.status = "completed"
            record.expires_at = self._clock() + self.completed_ttl_seconds
            return record

    def delete(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.pop(job_id, None)
            self._expired_ids.discard(job_id)
            if record is not None:
                self._remove_job_root(record.workspace.root)

    def cleanup_expired(self) -> int:
        now = self._clock()
        removed = 0
        with self._lock:
            expired = [job_id for job_id, record in self._jobs.items() if record.expires_at <= now]
            for job_id in expired:
                record = self._jobs.pop(job_id)
                self._expired_ids.add(job_id)
                self._remove_job_root(record.workspace.root)
                removed += 1

            known = set(self._jobs)
            cutoff = now - self.pending_ttl_seconds
            for child in self.root.iterdir():
                if not child.is_dir() or child.name in known or not _is_uuid(child.name):
                    continue
                try:
                    is_stale = child.stat().st_mtime <= cutoff
                except FileNotFoundError:
                    continue
                if is_stale:
                    self._expired_ids.add(child.name)
                    self._remove_job_root(child)
                    removed += 1
        return removed

    def _require(self, job_id: str) -> JobRecord:
        record = self._jobs.get(job_id)
        if record is None or not record.workspace.root.exists():
            raise KeyError(job_id)
        return record

    def _refresh_pending(self, record: JobRecord) -> None:
        record.expires_at = self._clock() + self.pending_ttl_seconds

    @staticmethod
    def _clear_directory(path: Path) -> None:
        if path.exists():
            for child in path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            path.mkdir(parents=True)

    @staticmethod
    def _remove_inputs(workspace: JobWorkspace) -> None:
        input_path = workspace.input_dir.resolve()
        root = workspace.root.resolve()
        if input_path != root and input_path.is_relative_to(root):
            shutil.rmtree(input_path, ignore_errors=True)

    def _remove_job_root(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.parent != self.root or not _is_uuid(resolved.name):
            raise ValueError("Refusing to remove an unmanaged job directory")
        shutil.rmtree(resolved, ignore_errors=True)

    @staticmethod
    def _require_managed_file(workspace: JobWorkspace, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace.root.resolve()) or not resolved.is_file():
            raise ValueError("Pipeline output is missing or outside the job directory")


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False
