"""Explicit request and response schemas for the multi-account web workflow."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class BrokerConfigResponse(BaseModel):
    code: str
    display_name: str
    upload_extensions: list[str]
    account_id_mode: Literal["auto", "manual"]


class ConfigResponse(BaseModel):
    brokers: list[BrokerConfigResponse]
    max_upload_bytes: int
    max_upload_mb: float
    max_files: int
    max_job_files: int
    pending_job_ttl_seconds: int
    job_ttl_seconds: int


class CreateJobResponse(BaseModel):
    job_id: str
    status: Literal["collecting"]


class UploadBatchResponse(BaseModel):
    job_id: str
    status: Literal["collecting"]
    accepted_files: int = Field(ge=1)
    total_files: int = Field(ge=1)
    reports: list["UploadedReportResponse"]


class UploadedReportResponse(BaseModel):
    report_id: str
    filename: str


class DeleteReportResponse(BaseModel):
    job_id: str
    status: Literal["collecting"]
    total_files: int = Field(ge=0)


class DiscoveredAccountResponse(BaseModel):
    broker: str
    account_id: str
    report_count: int = Field(ge=1)


class DiscoverResponse(BaseModel):
    job_id: str
    status: Literal["awaiting_options"]
    accounts: list[DiscoveredAccountResponse]


class TaxpayerRequest(BaseModel):
    fio1: str = Field(min_length=1)
    fio2: str = Field(min_length=1)
    fio3: str = ""
    iin: str = Field(min_length=1)


class ProcessJobRequest(BaseModel):
    tax_year: int = Field(ge=2000, le=2100)
    taxpayer: TaxpayerRequest
    joint_accounts: list[str] = Field(default_factory=list)
    acc_not_included_for_merged: list[str] = Field(default_factory=list)
    form270_05: bool = False
    allow_approximate_transfer_basis: bool = False


class MissingTransferBasisResponse(BaseModel):
    transfer_date: date | None
    symbol: str | None
    isin: str | None
    quantity: str
    currency: str | None
    destination_broker: str
    destination_account: str
    reason: str


class ArtifactResponse(BaseModel):
    id: str
    kind: Literal["account_audit", "joint_audit", "merged_audit", "form270"]
    filename: str
    download_url: str
    broker: str | None = None
    account_id: str | None = None


class ProcessJobResponse(BaseModel):
    job_id: str
    status: Literal["needs_additional_reports", "completed"]
    tax_year: int
    missing_transfer_basis: list[MissingTransferBasisResponse] = Field(default_factory=list)
    used_approximate_transfer_basis: bool = False
    artifacts: list[ArtifactResponse] = Field(default_factory=list)


class DeleteJobResponse(BaseModel):
    job_id: str
    status: Literal["deleted"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail
