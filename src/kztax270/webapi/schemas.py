"""Explicit request-independent response schemas for the web API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class BrokerConfigResponse(BaseModel):
    code: str
    display_name: str
    upload_extensions: list[str]
    account_id_optional: bool


class ConfigResponse(BaseModel):
    brokers: list[BrokerConfigResponse]
    max_upload_bytes: int
    max_upload_mb: float
    max_files: int
    job_ttl_seconds: int


class JobSummaryResponse(BaseModel):
    operations: int = Field(ge=0)
    instruments: int = Field(ge=0)
    warnings: int = Field(ge=0)
    reconciliation_errors: int = Field(ge=0)


class ReconciliationResponse(BaseModel):
    metric: str
    severity: str
    broker_value: str
    canonical_value: str
    difference: str
    tolerance: str
    currency: str | None = None
    instrument_key: str | None = None
    year: int | None = None
    source: str | None = None
    details: str | None = None


class DownloadLinksResponse(BaseModel):
    audit: str
    form270: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    broker: str
    account_id: str
    tax_year: int
    summary: JobSummaryResponse
    warnings: list[str]
    reconciliation: list[ReconciliationResponse]
    downloads: DownloadLinksResponse


class DeleteJobResponse(BaseModel):
    job_id: str
    status: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail
