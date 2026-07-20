"""Create a 50/50 ownership copy of a canonical audit workbook."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from kztax270.canonical.schema import CanonicalDataset
from kztax270.canonical.workbook_schema import CANONICAL_WORKBOOK_SHEETS
from kztax270.form270.json_builder import load_processed_workbook_tables

from .audit_workbook import ExcelAuditWorkbookWriter
from .merge_workbooks import aggregate_years_results, broker_account_from_workbook_path

JOINT_SHARE = Decimal("0.5")

# These columns represent the account owner's share.  Unit prices, FX rates,
# multipliers, dates, years and reconciliation tolerances deliberately remain
# unchanged.
OWNERSHIP_COLUMNS = frozenset(
    {
        "quantity",
        "proceeds",
        "value",
        "realized_pl",
        "gross_amount",
        "withholding_tax",
        "net_amount",
        "gross_amount_kzt",
        "withholding_tax_kzt",
        "net_amount_kzt",
        "tax",
        "tax_kzt",
        "amount",
        "commission",
        "amount_with_commission",
        "enter_quantity",
        "enter_amount",
        "enter_commission",
        "exit_quantity",
        "exit_amount",
        "exit_commission",
        "acquisition_cost_with_commission",
        "pnl_before_commission",
        "pnl_after_all_commissions",
        "pnl",
        "exit_amount_kzt",
        "acquisition_cost_with_commission_kzt",
        "pnl_before_commission_kzt",
        "pnl_after_all_commissions_kzt",
        "pnl_kzt",
        "amount_kzt",
        "ending_cash",
        "ending_cash_kzt",
        "only_profit",
        "only_profit_kzt",
        "withhold_kzt",
        "tax_kzt_withhold",
        "broker_value",
        "canonical_value",
        "difference",
    }
)


def create_joint_audit_workbook(input_path: Path, output_path: Path | None = None) -> Path:
    """Write a half-share audit workbook and return its path.

    Every ownership-dependent detail value is divided by two.  Annual result
    rows are aggregated again afterwards so pooled withholding and residual tax
    are recalculated from the scaled values.
    """

    source = Path(input_path)
    if not source.exists():
        raise FileNotFoundError(f"Audit workbook does not exist: {source}")
    if _is_joint_workbook_name(source):
        raise ValueError(f"Audit workbook is already a joint workbook: {source.name}")

    destination = Path(output_path) if output_path is not None else joint_workbook_path(source)
    if source.resolve() == destination.resolve():
        raise ValueError("Joint output workbook cannot overwrite its source workbook")

    tables = load_processed_workbook_tables(source)
    broker, account_id = broker_account_from_workbook_path(source)
    dataset = CanonicalDataset.empty(broker, account_id)
    for sheet in CANONICAL_WORKBOOK_SHEETS:
        records = tables.get(sheet.name, [])
        dataset.tables[sheet.name] = [
            _scale_record(record, sheet_name=sheet.name) for record in records
        ]

    dataset.tables["Years_Results"] = aggregate_years_results(
        dataset.tables.get("Years_Results", [])
    )
    return ExcelAuditWorkbookWriter().write(dataset, destination)


def joint_workbook_path(source: Path) -> Path:
    """Insert ``_joint`` immediately before the audit suffix."""

    path = Path(source)
    stem = path.stem
    if stem.endswith("_audit_fixed"):
        stem = f"{stem[:-len('_audit_fixed')]}_joint_audit_fixed"
    elif stem.endswith("_audit"):
        stem = f"{stem[:-len('_audit')]}_joint_audit"
    else:
        stem = f"{stem}_joint"
    return path.with_name(f"{stem}{path.suffix or '.xlsx'}")


def _scale_record(record: Mapping[str, Any], *, sheet_name: str) -> dict[str, Any]:
    scaled = dict(record)
    for column in OWNERSHIP_COLUMNS.intersection(record):
        value = record.get(column)
        if _missing(value):
            continue
        try:
            scaled[column] = _decimal_text(Decimal(str(value).replace(" ", "").replace(",", ".")) * JOINT_SHARE)
        except InvalidOperation as exc:
            raise ValueError(
                f"{sheet_name}.{column} contains a non-numeric ownership value: {value!r}"
            ) from exc
    return scaled


def _is_joint_workbook_name(path: Path) -> bool:
    stem = path.stem
    return stem.endswith("_joint_audit") or stem.endswith("_joint_audit_fixed") or stem.endswith("_joint")


def _missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(value != value)
    except Exception:
        return False


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
