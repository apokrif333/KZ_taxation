"""FastAPI transport for the domain-level multi-account FrontPipeline."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, NoReturn

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from kztax270.brokers.account_detection import BROKER_REPORT_SPECS
from kztax270.config import ProjectPaths
from kztax270.excel.joint_workbook import joint_workbook_path
from kztax270.front_pipeline import (
    FRONT_BROKER_FOLDERS,
    FrontPipeline,
    FrontPipelineResult,
    InvalidReportPeriodError,
)

from .schemas import (
    ArtifactResponse,
    BrokerConfigResponse,
    ConfigResponse,
    CreateJobResponse,
    DeleteJobResponse,
    DiscoverResponse,
    DiscoveredAccountResponse,
    HealthResponse,
    MissingTransferBasisResponse,
    ProcessJobRequest,
    ProcessJobResponse,
    UploadBatchResponse,
)
from .storage import (
    ArtifactRecord,
    DuplicateReportError,
    InvalidJobStateError,
    JobFileLimitError,
    JobRecord,
    JobStore,
    JobWorkspace,
    WebApiSettings,
)

LOGGER = logging.getLogger("kztax270.webapi")
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UNSAFE_FILENAME_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
UPLOAD_CHUNK_SIZE = 1024 * 1024
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JSON_MEDIA_TYPE = "application/json"
ZIP_MEDIA_TYPE = "application/zip"

AUTO_BROKERS = frozenset(FRONT_BROKER_FOLDERS.values())
MANUAL_BROKERS = frozenset({"freedom"})
UPLOAD_BROKERS = tuple(sorted(AUTO_BROKERS | MANUAL_BROKERS))

FrontPipelineFactory = Callable[[ProjectPaths], FrontPipeline]


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}


def create_app(
    settings: WebApiSettings | None = None,
    *,
    front_pipeline_factory: FrontPipelineFactory = FrontPipeline,
    job_store: JobStore | None = None,
) -> FastAPI:
    resolved_settings = settings or WebApiSettings.from_env()
    store = job_store or JobStore(
        resolved_settings.job_root,
        resolved_settings.pending_job_ttl_seconds,
        resolved_settings.job_ttl_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.cleanup_expired()
        yield

    application = FastAPI(title="QCM Tax 270 API", version="0.2.0", lifespan=lifespan)
    application.state.settings = resolved_settings
    application.state.job_store = store
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        detail = {"code": exc.code, "message": exc.message, **exc.extra}
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation_error",
                    "message": "Проверьте обязательные поля и формат запроса.",
                }
            },
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        LOGGER.error("Unexpected API error class=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "processing_error",
                    "message": "Не удалось обработать запрос.",
                }
            },
        )

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="qcm-tax-270")

    @application.get("/api/config", response_model=ConfigResponse)
    async def config() -> ConfigResponse:
        store.cleanup_expired()
        brokers = [
            BrokerConfigResponse(
                code=broker,
                display_name=BROKER_REPORT_SPECS[broker].display_name,
                upload_extensions=sorted(BROKER_REPORT_SPECS[broker].extensions),
                account_id_mode="manual" if broker in MANUAL_BROKERS else "auto",
            )
            for broker in UPLOAD_BROKERS
        ]
        return ConfigResponse(
            brokers=brokers,
            max_upload_bytes=resolved_settings.max_upload_bytes,
            max_upload_mb=resolved_settings.max_upload_mb,
            max_files=resolved_settings.max_files,
            max_job_files=resolved_settings.max_job_files,
            pending_job_ttl_seconds=resolved_settings.pending_job_ttl_seconds,
            job_ttl_seconds=resolved_settings.job_ttl_seconds,
        )

    @application.post("/api/jobs", response_model=CreateJobResponse)
    async def create_job() -> CreateJobResponse:
        record = store.create()
        return CreateJobResponse(job_id=record.job_id, status="collecting")

    @application.post("/api/jobs/{job_id}/reports", response_model=UploadBatchResponse)
    async def upload_reports(
        job_id: str,
        broker: str = Form(...),  # noqa: B008 - FastAPI parameter declaration
        files: list[UploadFile] = File(...),  # noqa: B008 - real multi-file Swagger control
        account_id: str | None = Form(None),  # noqa: B008 - FastAPI parameter declaration
    ) -> UploadBatchResponse:
        record = _job_or_error(store, job_id)
        _ensure_pending(record)
        broker = broker.strip().casefold()
        if broker not in UPLOAD_BROKERS:
            raise ApiError(422, "unsupported_broker", "Выбранный брокер не поддерживается.")
        if not files:
            raise ApiError(422, "validation_error", "Загрузите хотя бы один отчёт брокера.")
        if len(files) > resolved_settings.max_files:
            raise ApiError(
                413,
                "too_many_files",
                f"За один раз можно загрузить не более {resolved_settings.max_files} файлов.",
            )
        if len(record.uploads) + len(files) > resolved_settings.max_job_files:
            raise ApiError(
                413,
                "too_many_job_files",
                f"В одном задании можно хранить не более {resolved_settings.max_job_files} файлов.",
            )

        normalized_account_id = account_id.strip() if account_id and account_id.strip() else None
        if broker in MANUAL_BROKERS:
            if normalized_account_id is None:
                raise ApiError(422, "account_id_required", "Для Freedom Broker необходимо указать account_id.")
            _validate_account_id(normalized_account_id)
            broker_folder = f"freedom_{normalized_account_id}"
        else:
            if normalized_account_id is not None:
                raise ApiError(
                    422,
                    "account_id_not_allowed",
                    "Для выбранного брокера account_id определяется автоматически из отчёта.",
                )
            broker_folder = broker

        destination_dir = record.workspace.client_root / broker_folder
        destination_dir.mkdir(parents=True, exist_ok=True)
        saved: list[tuple[str, Path]] = []
        try:
            for upload in files:
                safe_name, suffix = _safe_upload_name(upload.filename)
                if suffix not in BROKER_REPORT_SPECS[broker].extensions:
                    raise ApiError(
                        415,
                        "unsupported_file_type",
                        "Формат файла не поддерживается для выбранного брокера.",
                    )
                destination = _available_destination(destination_dir, safe_name)
                digest = await _save_upload(upload, destination, resolved_settings.max_upload_bytes)
                saved.append((digest, destination))
            try:
                updated = store.add_uploads(
                    job_id,
                    saved,
                    max_job_files=resolved_settings.max_job_files,
                )
            except DuplicateReportError as exc:
                raise ApiError(409, "duplicate_report", "Этот отчёт уже загружен в задание.") from exc
            except JobFileLimitError as exc:
                raise ApiError(
                    413,
                    "too_many_job_files",
                    f"В одном задании можно хранить не более {resolved_settings.max_job_files} файлов.",
                ) from exc
            except InvalidJobStateError as exc:
                _raise_job_state_error(exc)
            LOGGER.info("Reports uploaded job_id=%s broker=%s count=%s", job_id, broker, len(saved))
            return UploadBatchResponse(
                job_id=job_id,
                status="collecting",
                accepted_files=len(saved),
                total_files=len(updated.uploads),
            )
        except Exception:
            _remove_paths(path for _digest, path in saved)
            raise
        finally:
            for upload in files:
                await upload.close()

    @application.post("/api/jobs/{job_id}/discover", response_model=DiscoverResponse)
    async def discover_accounts(job_id: str) -> DiscoverResponse:
        record = _job_or_error(store, job_id)
        _ensure_pending(record)
        if not record.uploads:
            raise ApiError(422, "validation_error", "Сначала загрузите брокерские отчёты.")
        pipeline = front_pipeline_factory(record.workspace.project_paths(resolved_settings.project_paths))
        try:
            accounts = await run_in_threadpool(
                pipeline.discover_accounts,
                record.workspace.internal_client_id,
            )
        except InvalidReportPeriodError as exc:
            raise _invalid_period_api_error(exc) from exc
        except ValueError as exc:
            LOGGER.info("Discovery validation failed job_id=%s error_class=%s", job_id, type(exc).__name__)
            raise ApiError(422, "validation_error", "Загруженные отчёты не прошли проверку.") from exc
        except Exception as exc:
            LOGGER.info("Report discovery failed job_id=%s error_class=%s", job_id, type(exc).__name__)
            raise ApiError(422, "report_parse_error", "Не удалось прочитать загруженные отчёты.") from exc
        try:
            store.mark_discovered(job_id)
        except InvalidJobStateError as exc:
            _raise_job_state_error(exc)
        return DiscoverResponse(
            job_id=job_id,
            status="awaiting_options",
            accounts=[
                DiscoveredAccountResponse(
                    broker=account.broker,
                    account_id=account.account_id,
                    report_count=len(account.report_paths),
                )
                for account in accounts
            ],
        )

    @application.post("/api/jobs/{job_id}/process", response_model=ProcessJobResponse)
    async def process_job(job_id: str, request: ProcessJobRequest) -> ProcessJobResponse:
        _job_or_error(store, job_id)
        try:
            record, previous_status = store.prepare_processing(job_id)
        except InvalidJobStateError as exc:
            _raise_job_state_error(exc)
        started_at = time.monotonic()
        pipeline = front_pipeline_factory(record.workspace.project_paths(resolved_settings.project_paths))
        try:
            result = await run_in_threadpool(
                pipeline.run,
                client_id=record.workspace.internal_client_id,
                tax_year=request.tax_year,
                taxpayer=request.taxpayer.model_dump(),
                joint_accounts=request.joint_accounts,
                acc_not_included_for_merged=request.acc_not_included_for_merged,
                form270_05=request.form270_05,
                allow_approximate_transfer_basis=request.allow_approximate_transfer_basis,
            )
        except InvalidReportPeriodError as exc:
            store.processing_failed(job_id, previous_status)
            raise _invalid_period_api_error(exc) from exc
        except ValueError as exc:
            store.processing_failed(job_id, previous_status)
            LOGGER.info("Processing validation failed job_id=%s error_class=%s", job_id, type(exc).__name__)
            raise ApiError(422, "validation_error", "Параметры расчёта или отчёты не прошли проверку.") from exc
        except Exception as exc:
            store.processing_failed(job_id, previous_status)
            LOGGER.error("Job processing failed job_id=%s error_class=%s", job_id, type(exc).__name__)
            raise ApiError(500, "processing_error", "Не удалось обработать брокерские отчёты.") from exc

        if not result.completed:
            store.needs_additional_reports(job_id)
            LOGGER.info("Job needs additional reports job_id=%s", job_id)
            return ProcessJobResponse(
                job_id=job_id,
                status="needs_additional_reports",
                tax_year=request.tax_year,
                missing_transfer_basis=_missing_basis_response(result),
                used_approximate_transfer_basis=result.used_approximate_transfer_basis,
            )

        try:
            artifacts = _build_artifacts(record.workspace, result, request.tax_year)
            store.complete(job_id, artifacts)
        except Exception as exc:
            store.processing_failed(job_id, previous_status)
            LOGGER.error("Artifact registration failed job_id=%s error_class=%s", job_id, type(exc).__name__)
            raise ApiError(500, "processing_error", "Не удалось подготовить результаты расчёта.") from exc
        LOGGER.info("Job completed job_id=%s elapsed_seconds=%.3f", job_id, time.monotonic() - started_at)
        return ProcessJobResponse(
            job_id=job_id,
            status="completed",
            tax_year=request.tax_year,
            missing_transfer_basis=_missing_basis_response(result),
            used_approximate_transfer_basis=result.used_approximate_transfer_basis,
            artifacts=[_artifact_response(job_id, artifact) for artifact in artifacts],
        )

    @application.get("/api/jobs/{job_id}/artifacts/{artifact_id}")
    async def download_artifact(job_id: str, artifact_id: str) -> FileResponse:
        record = _completed_job_or_error(store, job_id)
        artifact = record.artifacts.get(artifact_id)
        if artifact is None or not artifact.path.is_file():
            raise ApiError(404, "job_not_found", "Файл результата не найден.")
        return FileResponse(artifact.path, media_type=artifact.media_type, filename=artifact.filename)

    @application.get("/api/jobs/{job_id}/all")
    async def download_all(job_id: str) -> FileResponse:
        record = _completed_job_or_error(store, job_id)
        archive_path = record.workspace.output_dir / "all-artifacts.zip"
        await run_in_threadpool(_write_artifact_archive, record, archive_path)
        return FileResponse(archive_path, media_type=ZIP_MEDIA_TYPE, filename="qcm-tax-270-results.zip")

    @application.delete("/api/jobs/{job_id}", response_model=DeleteJobResponse)
    async def delete_job(job_id: str) -> DeleteJobResponse:
        store.delete(job_id)
        return DeleteJobResponse(job_id=job_id, status="deleted")

    _install_binary_upload_openapi_schema(application)
    return application


async def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> str:
    size = 0
    digest = hashlib.sha256()
    try:
        with destination.open("xb") as handle:
            while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(413, "file_too_large", "Размер файла превышает допустимый лимит.")
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _safe_upload_name(filename: str | None) -> tuple[str, str]:
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(basename).suffix.casefold()
    stem = basename[: -len(suffix)] if suffix else basename
    stem = UNSAFE_FILENAME_RE.sub("_", stem).strip(" .")
    if not stem:
        stem = "report"
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}{suffix}", suffix


def _available_destination(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    suffix = candidate.suffix
    stem = candidate.name[: -len(suffix)] if suffix else candidate.name
    index = 2
    while True:
        candidate = directory / f"{stem}__{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _validate_account_id(account_id: str) -> None:
    if not ACCOUNT_ID_RE.fullmatch(account_id):
        raise ApiError(422, "invalid_account_id", "Некорректный формат account_id.")


def _invalid_period_api_error(exc: InvalidReportPeriodError) -> ApiError:
    reports = [
        {
            "broker": report.broker,
            "account_id": report.account_id,
            "report_name": report.report_name,
            "period_end": report.period_end.isoformat() if report.period_end else None,
        }
        for report in exc.reports
    ]
    return ApiError(422, "invalid_report_period", str(exc), extra={"reports": reports})


def _missing_basis_response(result: FrontPipelineResult) -> list[MissingTransferBasisResponse]:
    return [
        MissingTransferBasisResponse(
            transfer_date=item.transfer_date,
            symbol=item.symbol,
            isin=item.isin,
            quantity=str(item.quantity),
            currency=item.currency,
            destination_broker=item.destination_broker,
            destination_account=item.destination_account,
            reason=item.reason,
        )
        for item in result.missing_transfer_basis
    ]


def _build_artifacts(
    workspace: JobWorkspace,
    result: FrontPipelineResult,
    tax_year: int,
) -> tuple[ArtifactRecord, ...]:
    artifacts: list[ArtifactRecord] = []
    joint_by_path = {path.resolve(): path for path in result.joint_workbook_paths}
    matched_joint_paths: set[Path] = set()
    for account, ordinary_path in zip(
        result.discovered_accounts,
        result.individual_workbook_paths,
        strict=True,
    ):
        _require_workspace_output(workspace, ordinary_path)
        artifacts.append(
            _artifact(
                kind="account_audit",
                path=ordinary_path,
                filename=ordinary_path.name,
                media_type=EXCEL_MEDIA_TYPE,
                broker=account.broker,
                account_id=account.account_id,
            )
        )
        expected_joint = joint_workbook_path(ordinary_path).resolve()
        joint_path = joint_by_path.get(expected_joint)
        if joint_path is not None:
            matched_joint_paths.add(expected_joint)
            _require_workspace_output(workspace, joint_path)
            artifacts.append(
                _artifact(
                    kind="joint_audit",
                    path=joint_path,
                    filename=joint_path.name,
                    media_type=EXCEL_MEDIA_TYPE,
                    broker=account.broker,
                    account_id=account.account_id,
                )
            )
    if matched_joint_paths != set(joint_by_path):
        raise ValueError("FrontPipeline returned an unassociated joint workbook")
    if result.merged_workbook_path is None:
        raise ValueError("Completed FrontPipeline result has no merged workbook")
    _require_workspace_output(workspace, result.merged_workbook_path)
    artifacts.append(
        _artifact(
            kind="merged_audit",
            path=result.merged_workbook_path,
            filename="merged_audit.xlsx",
            media_type=EXCEL_MEDIA_TYPE,
        )
    )
    for index, form_path in enumerate(result.form270_paths, start=1):
        _require_workspace_output(workspace, form_path)
        filename = f"270_{tax_year}_filled.json"
        if len(result.form270_paths) > 1:
            filename = f"270_{tax_year}_filled__{index}.json"
        artifacts.append(
            _artifact(
                kind="form270",
                path=form_path,
                filename=filename,
                media_type=JSON_MEDIA_TYPE,
            )
        )
    if not result.form270_paths:
        raise ValueError("Completed FrontPipeline result has no Form 270 output")
    return tuple(artifacts)


def _artifact(
    *,
    kind: Literal["account_audit", "joint_audit", "merged_audit", "form270"],
    path: Path,
    filename: str,
    media_type: str,
    broker: str | None = None,
    account_id: str | None = None,
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=uuid.uuid4().hex,
        kind=kind,
        path=path,
        filename=filename,
        media_type=media_type,
        broker=broker,
        account_id=account_id,
    )


def _artifact_response(job_id: str, artifact: ArtifactRecord) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.artifact_id,
        kind=artifact.kind,
        filename=artifact.filename,
        download_url=f"/api/jobs/{job_id}/artifacts/{artifact.artifact_id}",
        broker=artifact.broker,
        account_id=artifact.account_id,
    )


def _write_artifact_archive(record: JobRecord, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for artifact in record.artifacts.values():
            name = _unique_archive_name(artifact.filename, used_names)
            archive.write(artifact.path, arcname=name)
            used_names.add(name)


def _unique_archive_name(filename: str, used: set[str]) -> str:
    if filename not in used:
        return filename
    path = Path(filename)
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    index = 2
    while f"{stem}__{index}{suffix}" in used:
        index += 1
    return f"{stem}__{index}{suffix}"


def _require_workspace_output(workspace: JobWorkspace, path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace.root.resolve()) or not resolved.is_file():
        raise ValueError("Pipeline output is missing or outside the job directory")


def _job_or_error(store: JobStore, job_id: str) -> JobRecord:
    record = store.get(job_id)
    if record is not None:
        return record
    if store.was_expired(job_id):
        raise ApiError(410, "job_expired", "Срок хранения задания истёк.")
    raise ApiError(404, "job_not_found", "Задание не найдено.")


def _completed_job_or_error(store: JobStore, job_id: str) -> JobRecord:
    record = _job_or_error(store, job_id)
    if record.status != "completed":
        raise ApiError(409, "validation_error", "Расчёт задания ещё не завершён.")
    return record


def _ensure_pending(record: JobRecord) -> None:
    if record.status == "completed":
        raise ApiError(409, "job_completed", "Расчёт уже завершён; создайте новое задание.")
    if record.status == "processing":
        raise ApiError(409, "validation_error", "Задание уже обрабатывается.")


def _raise_job_state_error(exc: InvalidJobStateError) -> NoReturn:
    if str(exc) == "completed":
        raise ApiError(409, "job_completed", "Расчёт уже завершён; создайте новое задание.") from exc
    if str(exc) == "collecting":
        raise ApiError(409, "validation_error", "Сначала выполните обнаружение счетов.") from exc
    raise ApiError(409, "validation_error", "Задание сейчас нельзя изменить или обработать.") from exc


def _remove_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        Path(path).unlink(missing_ok=True)


def _install_binary_upload_openapi_schema(application: FastAPI) -> None:
    """Ensure Swagger renders the repeated ``files`` field as a file picker."""

    default_openapi = application.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = default_openapi()
        request_schema = schema["paths"]["/api/jobs/{job_id}/reports"]["post"]["requestBody"]["content"][
            "multipart/form-data"
        ]["schema"]
        if "$ref" in request_schema:
            component_name = request_schema["$ref"].rsplit("/", 1)[-1]
            request_schema = schema["components"]["schemas"][component_name]
        request_schema["properties"]["files"]["items"].setdefault("format", "binary")
        return schema

    application.openapi = custom_openapi  # type: ignore[method-assign]


app = create_app()
