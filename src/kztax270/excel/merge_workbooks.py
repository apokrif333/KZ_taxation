"""Merge account audit workbooks into one canonical workbook."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
TAX_RATE = Decimal("0.10")
MONEY_QUANTUM = Decimal("0.01")
NON_PREFERENTIAL_TRADE_FLAGS = frozenset({"non-preferential", "offshore"})
KZ_COUNTRY_CODES = frozenset({"kz", "kaz", "kazakhstan", "казахстан"})


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
    _recalculate_tax_after_merge(result)
    return result


def _recalculate_tax_after_merge(rows: Sequence[dict[str, Any]]) -> None:
    """Rebuild derived tax fields from the merged annual bases.

    Account workbooks contain taxes calculated before the accounts are pooled.
    Adding those taxes preserves tax on a profitable account even when another
    account has an offsetting loss.  The merged workbook must instead expose
    the same bases that application 270.00 consumes.
    """

    trade_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        table = row.get("table")
        if table == "Yearly Trades":
            row["tax_kzt"] = "0.00"
            if _is_preferential_income(row):
                continue
            flag = str(row.get("flag") or "").strip().casefold()
            if flag in NON_PREFERENTIAL_TRADE_FLAGS:
                key = (row.get("year"), _trade_country_bucket(row))
                trade_groups.setdefault(key, []).append(row)
                continue
        row["tax_kzt"] = _money_text(_row_tax_before_withholding(row))

    for group_rows in trade_groups.values():
        bases = [max(_decimal(row.get("pnl_kzt")), Decimal("0")) for row in group_rows]
        pooled_base = max(
            sum((_decimal(row.get("pnl_kzt")) for row in group_rows), Decimal("0")),
            Decimal("0"),
        )
        _allocate_amount(group_rows, bases, _money(pooled_base * TAX_RATE), field="tax_kzt")

    _recalculate_withholding_after_merge(rows)


def _row_tax_before_withholding(row: Mapping[str, Any]) -> Decimal:
    table = str(row.get("table") or "")
    if table in {"Yearly Bonds Redemption", "Yearly FX Trades", "Yearly Coupons"}:
        return Decimal("0")
    if table in {"Yearly Trades", "Yearly Dividends"} and _is_preferential_income(row):
        return Decimal("0")
    if table in {"Yearly Derivatives", "Yearly Interest"}:
        base = _decimal(row.get("only_profit_kzt"))
    elif table == "Yearly Dividends":
        base = _decimal(row.get("amount_kzt"))
    elif not _missing(row.get("pnl_kzt")):
        base = _decimal(row.get("pnl_kzt"))
    elif not _missing(row.get("amount_kzt")):
        base = _decimal(row.get("amount_kzt"))
    else:
        base = Decimal("0")
    return _money(max(base, Decimal("0")) * TAX_RATE)


def _trade_country_bucket(row: Mapping[str, Any]) -> str:
    country = str(row.get("country") or "").strip().casefold()
    return "kz" if country in KZ_COUNTRY_CODES else "foreign"


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
        if _is_preferential_income(row):
            row["tax_kzt_withhold"] = "0.00"
            continue
        key = (row.get("table"), row.get("year"), row.get("country"))
        groups.setdefault(key, []).append(row)

    for group_rows in groups.values():
        total_tax = sum((max(_decimal(row.get("tax_kzt")), Decimal("0")) for row in group_rows), Decimal("0"))
        foreign_withholding = max(
            -sum((_decimal(row.get("withhold_kzt")) for row in group_rows), Decimal("0")),
            Decimal("0"),
        )
        tax_after_withholding = _money(max(total_tax - foreign_withholding, Decimal("0")))
        _allocate_tax_after_withholding(group_rows, total_tax, tax_after_withholding)


def _allocate_tax_after_withholding(
    rows: Sequence[dict[str, Any]], total_tax: Decimal, tax_after_withholding: Decimal
) -> None:
    """Distribute the pooled residual tax across displayed rows without changing its total."""

    if total_tax <= 0:
        for row in rows:
            row["tax_kzt_withhold"] = "0.00"
        return

    taxes = [max(_decimal(row.get("tax_kzt")), Decimal("0")) for row in rows]
    _allocate_amount(rows, taxes, tax_after_withholding, field="tax_kzt_withhold")


def _allocate_amount(
    rows: Sequence[dict[str, Any]],
    weights: Sequence[Decimal],
    total: Decimal,
    *,
    field: str,
) -> None:
    positive_indexes = [index for index, weight in enumerate(weights) if weight > 0]
    positive_total = sum((weights[index] for index in positive_indexes), Decimal("0"))
    remaining = _money(total)
    for row in rows:
        row[field] = "0.00"
    for offset, index in enumerate(positive_indexes):
        value = (
            remaining
            if offset == len(positive_indexes) - 1
            else _money(total * weights[index] / positive_total)
        )
        remaining -= value
        rows[index][field] = _money_text(value)


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


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _money_text(value: Decimal) -> str:
    return format(_money(value), "f")


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
