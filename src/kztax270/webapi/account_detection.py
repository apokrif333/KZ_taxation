"""Web compatibility exports for shared broker report metadata."""

from __future__ import annotations

from kztax270.brokers.account_detection import (
    BROKER_REPORT_SPECS,
    AccountDetectionError,
    BrokerReportSpec,
    detect_account_id,
)

BrokerUploadSpec = BrokerReportSpec
BROKER_UPLOAD_SPECS = BROKER_REPORT_SPECS

__all__ = [
    "AccountDetectionError",
    "BROKER_UPLOAD_SPECS",
    "BrokerUploadSpec",
    "detect_account_id",
]
