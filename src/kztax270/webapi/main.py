"""Minimal production-oriented FastAPI application around AccountPipeline."""

from __future__ import annotations

import logging
import re
import time
import uuid
import zipfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from kztax270.config import AccountConfig, ProjectPaths
from kztax270.excel.joint_workbook import create_joint_audit_workbook
from kztax270.form270.json_builder import Form270JsonBuilder
from kztax270.pipeline import AccountPipeline, AccountPipelineResult

from .account_detection import (
    BROKER_UPLOAD_SPECS,
    AccountDetectionError,
    detect_account_id,
)
from .schemas import (
    BrokerConfigResponse,
    ConfigResponse,
    DeleteJobResponse,
    DownloadLinksResponse,
    HealthResponse,
    JobResponse,
    JobSummaryResponse,
    ReconciliationResponse,
)
from .storage import JobRecord, JobStore, JobWorkspace, WebApiSettings

LOGGER = logging.getLogger("kztax270.webapi")
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UPLOAD_CHUNK_SIZE = 1024 * 1024
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JSON_MEDIA_TYPE = "application/json"
ZIP_MEDIA_TYPE = "application/zip"

PipelineFactory = Callable[[ProjectPaths], AccountPipeline]


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def create_app(
    settings: WebApiSettings | None = None,
    *,
    pipeline_factory: PipelineFactory = AccountPipeline,
    job_store: JobStore | None = None,
) -> FastAPI:
    resolved_settings = settings or WebApiSettings.from_env()
    store = job_store or JobStore(resolved_settings.job_root, resolved_settings.job_ttl_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.cleanup_expired()
        yield

    application = FastAPI(title="QCM Tax 270 API", version="0.1.0", lifespan=lifespan)
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
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

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
                code=spec.code,
                display_name=spec.display_name,
                upload_extensions=sorted(spec.extensions),
                account_id_optional=spec.account_id_optional,
            )
            for spec in BROKER_UPLOAD_SPECS.values()
        ]
        return ConfigResponse(
            brokers=brokers,
            max_upload_bytes=resolved_settings.max_upload_bytes,
            max_upload_mb=resolved_settings.max_upload_mb,
            max_files=resolved_settings.max_files,
            job_ttl_seconds=resolved_settings.job_ttl_seconds,
        )

    @application.post("/api/jobs", response_model=JobResponse)
    async def create_job(
        broker: Annotated[str, Form()],
        tax_year: Annotated[int, Form(ge=2000, le=2100)],
        files: Annotated[list[UploadFile], File()],
        account_id: Annotated[str | None, Form()] = None,
        joint_account: Annotated[bool, Form()] = False,
    ) -> JobResponse:
        spec = BROKER_UPLOAD_SPECS.get(broker)
        if spec is None:
            raise ApiError(422, "unsupported_broker", "Выбранный брокер не поддерживается.")
        if not files:
            raise ApiError(422, "validation_error", "Загрузите хотя бы один отчёт брокера.")
        if len(files) > resolved_settings.max_files:
            raise ApiError(
                413,
                "too_many_files",
                f"Можно загрузить не более {resolved_settings.max_files} файлов.",
            )

        normalized_account_id = account_id.strip() if account_id else None
        if normalized_account_id:
            _validate_account_id(normalized_account_id)

        workspace = store.create_workspace()
        started_at = time.monotonic()
        saved_paths: list[Path] = []
        try:
            for upload in files:
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in spec.extensions:
                    raise ApiError(
                        415,
                        "unsupported_file_type",
                        "Формат файла не поддерживается для выбранного брокера.",
                    )
                destination = workspace.input_dir / f"{uuid.uuid4()}{suffix}"
                await _save_upload(upload, destination, resolved_settings.max_upload_bytes)
                saved_paths.append(destination)

            if normalized_account_id is None:
                try:
                    normalized_account_id = await run_in_threadpool(detect_account_id, broker, saved_paths)
                except AccountDetectionError as exc:
                    raise ApiError(
                        422,
                        "account_id_mismatch",
                        "Загруженные отчёты относятся к разным брокерским счетам.",
                    ) from exc
                except Exception as exc:
                    raise ApiError(
                        422,
                        "report_parse_error",
                        "Не удалось прочитать брокерский счёт из загруженного отчёта.",
                    ) from exc
                if normalized_account_id is None:
                    raise ApiError(
                        422,
                        "account_id_required",
                        "Для выбранного брокера необходимо указать account_id.",
                    )
                _validate_account_id(normalized_account_id)

            account = AccountConfig(broker=broker, account_id=normalized_account_id)
            project_paths = workspace.project_paths(resolved_settings.project_paths)
            try:
                result = await run_in_threadpool(
                    _run_pipeline,
                    pipeline_factory,
                    project_paths,
                    account,
                    saved_paths,
                    tax_year,
                    joint_account,
                )
            except Exception as exc:
                LOGGER.error(
                    "Job processing failed job_id=%s broker=%s error_class=%s",
                    workspace.job_id,
                    broker,
                    type(exc).__name__,
                )
                raise ApiError(
                    500,
                    "processing_error",
                    "Не удалось обработать брокерские отчёты.",
                ) from exc

            audit_path, form_path, form_media_type = _prepare_downloads(workspace, result)
            store.complete(
                workspace,
                audit_path=audit_path,
                form270_path=form_path,
                form270_media_type=form_media_type,
            )
            response = _job_response(workspace.job_id, broker, normalized_account_id, tax_year, result, saved_paths)
            LOGGER.info(
                "Job completed job_id=%s broker=%s elapsed_seconds=%.3f",
                workspace.job_id,
                broker,
                time.monotonic() - started_at,
            )
            return response
        except ApiError:
            store.discard(workspace)
            raise
        except Exception as exc:
            store.discard(workspace)
            LOGGER.error(
                "Job failed job_id=%s broker=%s error_class=%s",
                workspace.job_id,
                broker,
                type(exc).__name__,
            )
            raise ApiError(500, "processing_error", "Не удалось обработать запрос.") from exc
        finally:
            for upload in files:
                await upload.close()

    @application.get("/api/jobs/{job_id}/audit")
    async def download_audit(job_id: str) -> FileResponse:
        record = _job_or_404(store, job_id)
        return FileResponse(
            record.audit_path,
            media_type=EXCEL_MEDIA_TYPE,
            filename="qcm-tax-270-audit.xlsx",
        )

    @application.get("/api/jobs/{job_id}/form270")
    async def download_form270(job_id: str) -> FileResponse:
        record = _job_or_404(store, job_id)
        filename = (
            "qcm-tax-270-form270.zip" if record.form270_media_type == ZIP_MEDIA_TYPE else "qcm-tax-270-form270.json"
        )
        return FileResponse(record.form270_path, media_type=record.form270_media_type, filename=filename)

    @application.delete("/api/jobs/{job_id}", response_model=DeleteJobResponse)
    async def delete_job(job_id: str) -> DeleteJobResponse:
        store.delete(job_id)
        return DeleteJobResponse(job_id=job_id, status="deleted")

    return application


async def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> None:
    size = 0
    with destination.open("xb") as handle:
        while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise ApiError(413, "file_too_large", "Размер загруженного файла превышает допустимый лимит.")
            handle.write(chunk)


def _validate_account_id(account_id: str) -> None:
    if not ACCOUNT_ID_RE.fullmatch(account_id):
        raise ApiError(422, "invalid_account_id", "Некорректный формат account_id.")


def _run_pipeline(
    pipeline_factory: PipelineFactory,
    paths: ProjectPaths,
    account: AccountConfig,
    report_paths: list[Path],
    tax_year: int,
    joint_account: bool,
) -> AccountPipelineResult:
    pipeline = pipeline_factory(paths)
    result = pipeline.run_reports(
        account,
        report_paths,
        tax_year=tax_year,
        taxpayer=None,
        write_excel=True,
        write_json=not joint_account,
    )
    if not joint_account:
        return result

    if result.workbook_path is None:
        raise ValueError("Audit workbook was not generated")
    joint_workbook_path = create_joint_audit_workbook(result.workbook_path)
    builder = Form270JsonBuilder(paths.form270_template)
    joint_draft = builder.build_processed_workbook_draft(
        joint_workbook_path,
        tax_year=tax_year,
        taxpayer=None,
        broker=account.broker,
        account_id=account.account_id,
    )
    joint_form_path = paths.output_data / f"270_{tax_year}_{account.broker}_{account.account_id}_joint.json"
    builder.save(joint_draft, joint_form_path)
    result.workbook_path = joint_workbook_path
    result.form_paths = {"joint": joint_form_path}
    return result


def _prepare_downloads(
    workspace: JobWorkspace,
    result: AccountPipelineResult,
) -> tuple[Path, Path, str]:
    if result.workbook_path is None:
        raise ValueError("Audit workbook was not generated")
    _require_workspace_output(workspace, result.workbook_path)
    form_paths = list(result.form_paths.values())
    if not form_paths:
        raise ValueError("Form 270 JSON was not generated")
    for path in form_paths:
        _require_workspace_output(workspace, path)
    if len(form_paths) == 1:
        return result.workbook_path, form_paths[0], JSON_MEDIA_TYPE

    archive_path = workspace.output_dir / "form270.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(form_paths, start=1):
            archive.write(path, arcname=f"form270-{index}.json")
    return result.workbook_path, archive_path, ZIP_MEDIA_TYPE


def _require_workspace_output(workspace: JobWorkspace, path: Path) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(workspace.root.resolve()) or not resolved.is_file():
        raise ValueError("Pipeline output is missing or outside the job directory")


def _job_response(
    job_id: str,
    broker: str,
    account_id: str,
    tax_year: int,
    result: AccountPipelineResult,
    report_paths: list[Path],
) -> JobResponse:
    replacements = _path_replacements(report_paths)
    warnings = [_redact_paths(warning, replacements) for warning in result.dataset.warnings]
    reconciliation_rows: list[ReconciliationResponse] = []
    for raw_row in result.dataset.tables.get("Reconciliation", []):
        row: dict[str, Any] = dict(raw_row)
        for key in ("source", "details"):
            if row.get(key) is not None:
                row[key] = _redact_paths(str(row[key]), replacements)
        reconciliation_rows.append(ReconciliationResponse.model_validate(row))
    return JobResponse(
        job_id=job_id,
        status="completed",
        broker=broker,
        account_id=account_id,
        tax_year=tax_year,
        summary=JobSummaryResponse(
            operations=len(result.dataset.tables.get("Trades", [])),
            instruments=len(result.dataset.tables.get("Instruments", [])),
            warnings=len(warnings),
            reconciliation_errors=result.reconciliation_error_count,
        ),
        warnings=warnings,
        reconciliation=reconciliation_rows,
        downloads=DownloadLinksResponse(
            audit=f"/api/jobs/{job_id}/audit",
            form270=f"/api/jobs/{job_id}/form270",
        ),
    )


def _path_replacements(paths: list[Path]) -> tuple[str, ...]:
    values: set[str] = set()
    for path in paths:
        values.add(str(path))
        values.add(str(path.resolve()))
    return tuple(sorted(values, key=len, reverse=True))


def _redact_paths(value: str, replacements: tuple[str, ...]) -> str:
    result = value
    for path in replacements:
        result = result.replace(path, "[uploaded report]")
    return result


def _job_or_404(store: JobStore, job_id: str) -> JobRecord:
    record = store.get(job_id)
    if record is None:
        raise ApiError(404, "job_not_found", "Задание не найдено или срок хранения истёк.")
    return record


app = create_app()
