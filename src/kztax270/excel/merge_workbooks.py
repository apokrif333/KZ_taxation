"""Merge account audit workbooks into one canonical workbook."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from kztax270.canonical.schema import CanonicalDataset
from kztax270.canonical.workbook_schema import CANONICAL_WORKBOOK_SHEETS
from kztax270.form270.json_builder import load_processed_workbook_tables

from .audit_workbook import ExcelAuditWorkbookWriter


YEAR_RESULT_DIMENSIONS = ("table", "year", "flag", "country", "tax_exchange", "currency")
YEAR_RESULT_VALUES = (
    "pnl",
    "pnl_kzt",
    "amount",
    "amount_kzt",
    "only_profit",
    "only_profit_kzt",
    "withhold_kzt",
    "tax_kzt",
    "tax_kzt_withhold",
)
WITHHOLDING_POOL_TABLES = frozenset({"Yearly Trades", "Yearly Dividends", "Yearly Coupons"})


def merge_audit_workbooks(input_paths: Sequence[Path], output_path: Path) -> Path:
    """Concatenate canonical sheets and aggregate annual result intersections."""

    paths = tuple(Path(path).resolve() for path in input_paths)
    if len(paths) < 2:
        raise ValueError("At least two audit workbooks are required for merging")
    if len(set(paths)) != len(paths):
        raise ValueError("The merge workbook list contains duplicate paths")
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Audit workbook does not exist: {missing[0]}")
    if output_path.resolve() in paths:
        raise ValueError("Merged output workbook cannot also be an input workbook")

    merged = CanonicalDataset.empty("merged", output_path.stem)
    for path in paths:
        broker, account_id = broker_account_from_workbook_path(path)
        tables = load_processed_workbook_tables(path)
        for sheet in CANONICAL_WORKBOOK_SHEETS:
            records = tables.get(sheet.name, [])
            if sheet.name == "Years_Results":
                merged.tables.setdefault(sheet.name, []).extend(dict(record) for record in records)
                continue
            if sheet.name == "CashBalances":
                merged.tables.setdefault(sheet.name, []).extend(
                    {
                        **record,
                        "broker": record.get("broker") or broker,
                        "account_id": record.get("account_id") or account_id,
                    }
                    for record in records
                )
                continue
            merged.tables.setdefault(sheet.name, []).extend(dict(record) for record in records)

    merged.tables["Years_Results"] = aggregate_years_results(merged.tables.get("Years_Results", []))
    return ExcelAuditWorkbookWriter().write(merged, output_path)


def aggregate_years_results(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sum numeric annual-result fields sharing the same dimensions."""

    groups: dict[tuple[Any, ...], dict[str, Decimal]] = {}
    present_values: dict[tuple[Any, ...], set[str]] = {}
    for record in records:
        dimensions: list[Any] = []
        for field in YEAR_RESULT_DIMENSIONS:
            if field == "tax_exchange":
                value = record.get("tax_exchange") or record.get("exchange")
            else:
                value = record.get(field)
            dimensions.append(_dimension_value(value, field=field))
        key = tuple(dimensions)
        values = groups.setdefault(key, {field: Decimal("0") for field in YEAR_RESULT_VALUES})
        present = present_values.setdefault(key, set())
        for field in YEAR_RESULT_VALUES:
            if not _missing(record.get(field)):
                values[field] += _decimal(record.get(field))
                present.add(field)

    result: list[dict[str, Any]] = []
    for key in sorted(groups, key=_year_result_sort_key):
        row = dict(zip(YEAR_RESULT_DIMENSIONS, key, strict=True))
        for field in YEAR_RESULT_VALUES:
            if field in present_values[key]:
                row[field] = _decimal_text(groups[key][field])
        result.append(row)
    _recalculate_withholding_after_merge(result)
    return result


def _recalculate_withholding_after_merge(rows: Sequence[dict[str, Any]]) -> None:
    """Pool foreign withholding by income type, year, and country after a merge.

    Source workbooks calculate ``tax_kzt_withhold`` independently.  Once several
    accounts are merged, withholding from one broker can cover Kazakhstan tax
    calculated by another broker for the same income type/year/country.
    """

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("table") not in WITHHOLDING_POOL_TABLES:
            continue
        if row.get("table") in {"Yearly Dividends", "Yearly Coupons"} and _is_preferential_income(row):
            row["tax_kzt_withhold"] = "0"
            continue
        key = (row.get("table"), row.get("year"), row.get("country"))
        groups.setdefault(key, []).append(row)

    for group_rows in groups.values():
        total_tax = sum((max(_decimal(row.get("tax_kzt")), Decimal("0")) for row in group_rows), Decimal("0"))
        foreign_withholding = max(
            -sum((_decimal(row.get("withhold_kzt")) for row in group_rows), Decimal("0")),
            Decimal("0"),
        )
        tax_after_withholding = max(total_tax - foreign_withholding, Decimal("0"))
        _allocate_tax_after_withholding(group_rows, total_tax, tax_after_withholding)


def _allocate_tax_after_withholding(
    rows: Sequence[dict[str, Any]], total_tax: Decimal, tax_after_withholding: Decimal
) -> None:
    """Distribute the pooled residual tax across displayed rows without changing its total."""

    if total_tax <= 0:
        for row in rows:
            row["tax_kzt_withhold"] = "0"
        return

    remaining = tax_after_withholding
    taxable_rows = [row for row in rows if max(_decimal(row.get("tax_kzt")), Decimal("0")) > 0]
    for index, row in enumerate(taxable_rows):
        tax = max(_decimal(row.get("tax_kzt")), Decimal("0"))
        value = remaining if index == len(taxable_rows) - 1 else tax_after_withholding * tax / total_tax
        remaining -= value
        row["tax_kzt_withhold"] = _decimal_text(value)
    for row in rows:
        if row not in taxable_rows:
            row["tax_kzt_withhold"] = "0"


def _is_preferential_income(row: Mapping[str, Any]) -> bool:
    flag = str(row.get("flag") or "").strip().casefold()
    return flag == "issuer_kz" or flag.startswith("preferential")


def broker_account_from_workbook_path(path: Path) -> tuple[str, str]:
    """Infer source broker and account from the standard audit filename."""

    name = path.stem
    for suffix in ("_joint_audit_fixed", "_joint_audit", "_audit_fixed", "_audit"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if "_" not in name:
        return "", name
    broker, account_id = name.split("_", 1)
    if broker == "freedom" and account_id.startswith("bank_"):
        return "freedom_bank", account_id[len("bank_") :]
    if broker == "freedom" and account_id.startswith("broker_"):
        return "freedom_broker", account_id[len("broker_") :]
    return broker, account_id


def _dimension_value(value: Any, *, field: str) -> Any:
    if _missing(value):
        return None
    if field == "year":
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError):
            return str(value).strip()
    return str(value).strip()


def _decimal(value: Any) -> Decimal:
    if _missing(value):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Years_Results contains a non-numeric value: {value!r}") from exc


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    try:
        return bool(value != value)
    except Exception:
        return False


def _year_result_sort_key(key: tuple[Any, ...]) -> tuple[Any, ...]:
    table, year, flag, country, tax_exchange, currency = key
    return (
        str(table or ""),
        -1 if year is None else int(year),
        str(flag or ""),
        str(country or ""),
        str(tax_exchange or ""),
        str(currency or ""),
    )
