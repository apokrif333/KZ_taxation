"""Native Interactive Brokers CSV parser."""

from __future__ import annotations

import csv
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from kztax270.canonical.schema import AccountMetadata, CanonicalDataset, RawReportTotals
from kztax270.form270.json_builder import DEFAULT_BROKER_BANK_INFO
from kztax270.reconciliation.models import ReconciliationMetric
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reference.kase_aix import (
    KaseAixDividendProvider,
    PREFERENTIAL_AIX,
    PREFERENTIAL_KASE,
)
from kztax270.reference.securities import AixInstrumentProvider, OffshoreJurisdictionProvider
from kztax270.transfers import TransferInFifoLot, TransferInFifoResolver, TransferInRequest

from .base import BrokerReport, ParseResult
from .discovery import DiscoveryRule, discover_raw_reports

IB_SECTION_ACCOUNT = "Account Information"
IB_SECTION_CA = "Corporate Actions"
IB_SECTION_CASH = "Cash Report"
IB_SECTION_DEPOSITS = "Deposits & Withdrawals"
IB_SECTION_DIVIDENDS = "Dividends"
IB_SECTION_FI = "Financial Instrument Information"
IB_SECTION_INTEREST = "Interest"
IB_SECTION_MTM = "Mark-to-Market Performance Summary"
IB_SECTION_NAV = "Change in NAV"
IB_SECTION_POSITIONS = "Open Positions"
IB_SECTION_RU = "Realized & Unrealized Performance Summary"
IB_SECTION_STATEMENT = "Statement"
IB_SECTION_TAX = "Withholding Tax"
IB_SECTION_TRADES = "Trades"

ISIN_RE = re.compile(r"(?<!\w)([A-Z]{2}[A-Z0-9]{10})(?!\w)")
SYMBOL_ISIN_RE = re.compile(r"^([^()]+)\(([^)]+)\)")

FLAG_PREFERENTIAL = "preferential"
FLAG_PREFERENTIAL_AIX = PREFERENTIAL_AIX
FLAG_PREFERENTIAL_KASE = PREFERENTIAL_KASE
FLAG_NON_PREFERENTIAL = "non-preferential"
FLAG_OFFSHORE = "offshore"

EXCHANGE_OUTOFKZ = "outofKZ"
EXCHANGE_AIX = "AIX"
EXCHANGE_KASE = "KASE"
US_LISTING_EXCHANGES = {
    "AMEX",
    "ARCA",
    "BATS",
    "CBOE",
    "IEX",
    "ISE",
    "NASDAQ",
    "NYSE",
    "NYSEARCA",
    "PINK",
}


@dataclass(slots=True)
class ParsedIbReport:
    path: Path
    account_id: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    base_currency: str | None = None
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    totals: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class FifoOpenLot:
    asset_type: str
    symbol: str
    isin: str | None
    currency: str
    country: str | None
    exchange: str | None
    date_time: datetime | None
    raw_quantity: Decimal
    raw_amount: Decimal
    raw_commission: Decimal
    price: Decimal
    calculation_price: Decimal
    multiplier: Decimal
    quantity: Decimal
    broker_quantity: Decimal
    commission_per_unit: Decimal
    trade_id: str | None
    opening_lot_status: str = "matched"


@dataclass(frozen=True, slots=True)
class CorporateActionIdentityChange:
    action_dt: datetime
    old_symbol: str | None
    old_isin: str | None
    new_symbol: str | None
    new_isin: str | None
    ratio: Decimal
    description: str | None


class InteractiveBrokersParser:
    broker_code = "ib"

    def __init__(
        self,
        fx_provider: AnnualFxRateProvider | None = None,
        transfer_in_resolver: TransferInFifoResolver | None = None,
    ) -> None:
        self.fx_provider = fx_provider or AnnualFxRateProvider({})
        self.transfer_in_resolver = transfer_in_resolver

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        return discover_raw_reports(raw_root, DiscoveryRule(broker="ib", account_id=account_id))

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        parsed_reports = [parse_ib_csv_report(report.path) for report in reports]
        dataset = build_canonical_dataset(
            parsed_reports,
            account_id,
            self.fx_provider,
            transfer_in_resolver=self.transfer_in_resolver,
        )
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(
            broker=self.broker_code,
            account_id=account_id,
            reports=reports,
            dataset=dataset,
            raw_totals=dataset.raw_totals,
        )


def parse_ib_csv_report(path: Path) -> ParsedIbReport:
    parsed = ParsedIbReport(path=path)
    current_headers: dict[str, list[str]] = {}

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            section = row[0].strip()
            row_type = row[1].strip() if len(row) > 1 else ""
            if row_type == "Header":
                current_headers[section] = row
                continue
            if row_type not in {"Data", "SubTotal", "Total"}:
                if section == "Total P/L for Statement Period":
                    parsed.fields["Total P/L for Statement Period"] = _last_value(row)
                continue

            header = current_headers.get(section)
            record = _row_to_record(header, row)
            record["source_report"] = str(path)
            if row_type == "Data":
                parsed.rows[section].append(record)
            else:
                parsed.totals[section].append(record)

            if section == IB_SECTION_STATEMENT and record.get("Field Name") == "Period":
                parsed.period_start, parsed.period_end = _parse_period(str(record.get("Field Value") or ""))
            elif section == IB_SECTION_ACCOUNT:
                if record.get("Field Name") == "Account":
                    parsed.account_id = str(record.get("Field Value") or "")
                elif record.get("Field Name") == "Base Currency":
                    parsed.base_currency = str(record.get("Field Value") or "")
            elif section == IB_SECTION_NAV and record.get("Field Name"):
                parsed.fields[str(record.get("Field Name"))] = str(record.get("Field Value") or "")
    return parsed


def build_canonical_dataset(
    reports: Sequence[ParsedIbReport],
    account_id: str,
    fx_provider: AnnualFxRateProvider,
    *,
    transfer_in_resolver: TransferInFifoResolver | None = None,
) -> CanonicalDataset:
    base_currency = next((report.base_currency for report in reports if report.base_currency), "USD") or "USD"
    dataset = CanonicalDataset(metadata=AccountMetadata(broker="ib", account_id=account_id, base_currency=base_currency))

    instruments = _build_instruments(reports, account_id)
    instrument_lookup = _instrument_lookup(instruments)
    symbol_history = _instrument_symbol_history(instruments)
    dataset.tables["Instruments"] = instruments
    corporate_actions = [*_build_corporate_actions(reports), *_build_inferred_symbol_change_actions(instruments)]
    identity_changes = _build_corporate_action_identity_changes(corporate_actions)
    dataset.tables["CorporateActions"] = corporate_actions
    dataset.tables["Dividends"] = _apply_corporate_action_identity_changes_to_records(
        _build_dividends(reports, fx_provider, dataset.warnings, instrument_lookup),
        identity_changes,
        instrument_lookup,
        date_fields=("pay_date", "date"),
    )
    transfers, transfer_totals_by_currency = _build_transfers(reports, instrument_lookup)
    transfers = _apply_corporate_action_identity_changes_to_records(
        transfers,
        identity_changes,
        instrument_lookup,
        date_fields=("date",),
    )
    raw_internal_trades = _apply_corporate_action_identity_changes_to_records(
        _build_trades(reports, instrument_lookup),
        identity_changes,
        instrument_lookup,
        date_fields=("date_time",),
    )
    synthetic_corporate_action_trades = _build_synthetic_corporate_action_trades(corporate_actions, instrument_lookup)
    internal_trades = _sort_trades_by_datetime([*raw_internal_trades, *synthetic_corporate_action_trades])
    _apply_broker_country_to_forex_trades(internal_trades, "ib")
    dataset.tables["Trades"] = _canonical_trade_rows(internal_trades)
    dataset.tables["_BrokerTradeRealizedPL"] = _build_broker_trade_realized_pl(internal_trades)
    fifo_input_trades = [
        trade
        for trade in _apply_corporate_action_split_adjustments_to_fifo_trades(internal_trades, corporate_actions)
        if not _is_fx_trade(trade)
    ]
    fifo_rows, fifo_positions, transfer_rows = _build_fifo_and_positions(
        fifo_input_trades,
        transfers=transfers,
        initial_lots=_apply_corporate_action_identity_changes_to_initial_lots(
            _build_initial_fifo_lots(reports, instrument_lookup, raw_internal_trades, dataset.warnings),
            identity_changes,
            instrument_lookup,
        ),
        max_year=max((year for year in (_year_for_report(report) for report in reports) if year), default=None),
        fx_provider=fx_provider,
        warnings=dataset.warnings,
        symbol_history=symbol_history,
        transfer_in_resolver=transfer_in_resolver,
    )
    fifo_rows.extend(_build_fx_fifo_rows(internal_trades, fx_provider, dataset.warnings))
    dataset.tables["Fifo"] = fifo_rows
    dataset.tables["Unprocessed"] = _build_unprocessed_rows(dataset.tables["Trades"], fifo_rows)
    dataset.tables["Positions"] = fifo_positions
    dataset.tables["Transfers"] = _canonical_transfer_rows(transfer_rows)
    interest, coupons = _build_interest_and_coupons(reports, instrument_lookup, fx_provider, dataset.warnings)
    dataset.tables["Interest"] = interest
    dataset.tables["Coupons"] = coupons
    dataset.tables["CashBalances"] = _build_cash_balances(reports, fx_provider, dataset.warnings)
    dataset.tables["_BrokerRealizedPL"] = _build_broker_realized_pl(reports, fx_provider, dataset.warnings)
    years_results = _build_years_results(dataset)
    dataset.tables["Years_Results"] = years_results

    _populate_raw_totals(dataset.raw_totals, reports, transfer_totals_by_currency, instrument_lookup)
    coupon_total = sum((_decimal(record.get("gross_amount")) for record in dataset.tables["Coupons"]), Decimal("0"))
    dataset.raw_totals.scalar_totals[ReconciliationMetric.TOTAL_COUPONS.value] = coupon_total
    dataset.raw_totals.scalar_totals[ReconciliationMetric.TOTAL_INTEREST.value] = (
        dataset.raw_totals.scalar_totals.get(ReconciliationMetric.TOTAL_INTEREST.value, Decimal("0")) - coupon_total
    )
    return dataset


def _row_to_record(header: Sequence[str] | None, row: Sequence[str]) -> dict[str, Any]:
    if not header:
        return {f"col_{idx}": value for idx, value in enumerate(row)}
    fields = list(header[2:])
    values = list(row[2:])
    record: dict[str, Any] = {"_section": row[0].strip(), "_row_type": row[1].strip() if len(row) > 1 else ""}
    for idx, value in enumerate(values):
        name = fields[idx] if idx < len(fields) else f"extra_{idx}"
        name = name.strip() or f"unnamed_{idx}"
        if name in record:
            name = f"{name}_{idx}"
        record[name] = value.strip() if isinstance(value, str) else value
    return record


def _last_value(row: Sequence[str]) -> str:
    for value in reversed(row):
        if value not in (None, ""):
            return str(value)
    return ""


def _parse_period(value: str) -> tuple[date | None, date | None]:
    if " - " not in value:
        return None, None
    start_text, end_text = value.split(" - ", 1)
    return _parse_month_date(start_text), _parse_month_date(end_text)


def _parse_month_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date()
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d, %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed_date = _parse_date(text)
    return datetime.combine(parsed_date, datetime.min.time()) if parsed_date else None


def _decimal(value: Any) -> Decimal:
    if value in (None, "", "--"):
        return Decimal("0")
    return Decimal(str(value).replace(",", ""))


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _price_text(value: Decimal) -> str:
    return _decimal_text(value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))


def _money_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _multiplier_text(value: Decimal) -> str:
    normalized = _normalize_multiplier(value)
    if normalized.as_tuple().exponent < -6:
        normalized = normalized.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return _decimal_text(normalized)


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _date_to_iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()


def _year_for_report(report: ParsedIbReport) -> int | None:
    return report.period_end.year if report.period_end else None


def _annual_rate(
    fx_provider: AnnualFxRateProvider,
    year: int | None,
    currency: str | None,
    warnings: list[str],
) -> Decimal | None:
    if year is None or not currency:
        return None
    rate = fx_provider.rate(year, currency)
    if rate is None:
        warning = f"Missing annual NBK FX rate for {currency}/{year}; KZT fields left empty."
        if warning not in warnings:
            warnings.append(warning)
    return rate


def _amount_kzt(amount: Decimal, rate: Decimal | None) -> str | None:
    return str(amount * rate) if rate is not None else None


def _extract_symbol_isin(description: str | None) -> tuple[str | None, str | None, str | None]:
    if not description:
        return None, None, None
    match = SYMBOL_ISIN_RE.match(description)
    if match and _looks_security_identifier(match.group(2)):
        symbol = match.group(1).strip()
        isin = match.group(2).strip()
    else:
        symbol = _leading_dividend_symbol(description)
        isin_match = ISIN_RE.search(description)
        isin = isin_match.group(1) if isin_match else None
    dividend_type_match = re.search(r"\(([^)]+)\)\s*$", description)
    dividend_type = dividend_type_match.group(1) if dividend_type_match else None
    return symbol, isin, dividend_type


def _leading_dividend_symbol(description: str) -> str | None:
    match = re.match(r"\s*([A-Z0-9._/-]+)\s+(?:Cash\s+Dividend|Dividend)\b", description, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _looks_security_identifier(value: str | None) -> bool:
    text = _string_or_none(value)
    if not text:
        return False
    return bool(ISIN_RE.fullmatch(text) or re.fullmatch(r"[A-Z0-9]{6,12}", text))


def _country_from_instrument(asset_type: str | None, security_id: str | None, listing_exchange: str | None) -> str | None:
    if security_id and ISIN_RE.fullmatch(security_id):
        country = security_id[:2]
        return "BE" if country == "XS" else country
    if asset_type and "Option" in asset_type:
        return "US"
    exchange = _string_or_none(listing_exchange)
    if exchange and exchange.upper() in US_LISTING_EXCHANGES:
        return "US"
    return None


def _build_instruments(reports: Sequence[ParsedIbReport], account_id: str) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    instruments: list[dict[str, Any]] = []
    raw_rows: list[tuple[ParsedIbReport, dict[str, Any], int | None]] = []
    for report in reports:
        year = _year_for_report(report)
        for row in report.rows.get(IB_SECTION_FI, []):
            raw_rows.append((report, row, year))

    country_by_cusip: dict[str, str] = {}
    for _, row, _ in raw_rows:
        security_id = _string_or_none(row.get("Security ID"))
        if security_id and ISIN_RE.fullmatch(security_id):
            country_by_cusip[security_id[2:11]] = security_id[:2]

    for report, row, year in raw_rows:
        asset_type = _string_or_none(row.get("Asset Category"))
        symbol = _string_or_none(row.get("Symbol"))
        description = _string_or_none(row.get("Description"))
        security_id = _string_or_none(row.get("Security ID"))
        listing_exchange = _string_or_none(row.get("Listing Exch"))
        country_hint = _country_from_instrument(asset_type, security_id, listing_exchange)
        isin = _normalize_security_id_to_isin(security_id, country_by_cusip, country_hint=country_hint)
        country = _country_from_instrument(asset_type, isin or security_id, listing_exchange) or country_hint
        cusip = _string_or_none(row.get("CUSIP")) or (_cusip_from_isin(isin) if isin else None)
        key = (symbol, description, row.get("Conid"), security_id, year)
        if key in seen:
            continue
        seen.add(key)
        instruments.append(
            {
                "symbol": symbol,
                "description": description,
                "conid": _string_or_none(row.get("Conid")),
                "security_id": security_id,
                "underlying": _string_or_none(row.get("Underlying")),
                "listing_exchange": listing_exchange,
                "multiplier": str(_decimal(row.get("Multiplier") or "1")),
                "type": _string_or_none(row.get("Type")),
                "code": _string_or_none(row.get("Code")),
                "year": year,
                "expiry": _date_to_iso(_parse_date(row.get("Expiry"))),
                "delivery_month": _string_or_none(row.get("Delivery Month")),
                "strike": str(_decimal(row.get("Strike"))) if row.get("Strike") else None,
                "issuer": _string_or_none(row.get("Issuer")),
                "maturity": _date_to_iso(_parse_date(row.get("Maturity"))),
                "cusip": cusip,
                "country": country,
                "isin": isin,
                "figi": None,
                "issuer_country": country,
                "offshore_flag": None,
                "issuer_outside_kz_flag": None if country is None else country != "KZ",
                "preferential_tax_flag": None,
                "source_broker": "ib",
                "source_account": account_id,
                "source_report": str(report.path),
                "as_of_date": _date_to_iso(report.period_end),
                "asset_type": asset_type,
            }
        )
    return instruments


def _normalize_security_id_to_isin(
    security_id: str | None,
    country_by_cusip: Mapping[str, str],
    *,
    country_hint: str | None = None,
) -> str | None:
    if not security_id:
        return None
    security_id = security_id.strip()
    if ISIN_RE.fullmatch(security_id):
        return security_id
    if len(security_id) == 9 and re.fullmatch(r"[A-Z0-9]{9}", security_id):
        country = country_by_cusip.get(security_id) or _string_or_none(country_hint)
        if country and re.fullmatch(r"[A-Z]{2}", country):
            return _add_isin_check_digit(country + security_id)
    if len(security_id) == 11 and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}", security_id):
        return _add_isin_check_digit(security_id)
    return None


def _add_isin_check_digit(isin_without_check_digit: str) -> str:
    expanded = ""
    for char in isin_without_check_digit.upper():
        if char.isdigit():
            expanded += char
        else:
            expanded += str(ord(char) - 55)
    digits = [int(ch) for ch in expanded[::-1]]
    total = 0
    for idx, digit in enumerate(digits, start=1):
        value = digit * 2 if idx % 2 == 1 else digit
        total += value // 10 + value % 10
    return isin_without_check_digit.upper() + str((10 - total % 10) % 10)


def _instrument_lookup(instruments: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int | None], dict[str, Any]]:
    lookup: dict[tuple[str, int | None], dict[str, Any]] = {}
    for row in instruments:
        year = int(row["year"]) if row.get("year") not in (None, "") else None
        symbol_aliases = _symbol_aliases(_string_or_none(row.get("symbol")))
        isin = _string_or_none(row.get("isin"))
        cusip = _string_or_none(row.get("cusip")) or (_cusip_from_isin(isin) if isin else None)
        for key_value in (*symbol_aliases, row.get("description"), row.get("security_id"), isin, cusip):
            if key_value:
                lookup[(str(key_value), year)] = dict(row)
                lookup.setdefault((str(key_value), None), dict(row))
    return lookup


def _cusip_from_isin(isin: str | None) -> str | None:
    if isin and ISIN_RE.fullmatch(isin):
        return isin[2:11]
    return None


def _instrument_identity_key_from_values(
    *,
    isin: str | None = None,
    conid: str | None = None,
    symbol: str | None = None,
) -> str | None:
    normalized_isin = _string_or_none(isin)
    if normalized_isin and ISIN_RE.fullmatch(normalized_isin):
        return f"ISIN:{normalized_isin}"
    normalized_conid = _string_or_none(conid)
    if normalized_conid:
        return f"CONID:{normalized_conid}"
    normalized_symbol = _string_or_none(symbol)
    if normalized_symbol:
        return f"SYMBOL:{normalized_symbol}"
    return None


def _instrument_identity_key(row: Mapping[str, Any]) -> str | None:
    return _instrument_identity_key_from_values(
        isin=_string_or_none(row.get("isin") or row.get("security_id")),
        conid=_string_or_none(row.get("conid")),
        symbol=_string_or_none(row.get("symbol")),
    )


def _instrument_symbol_history(instruments: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str | None, int | None, str | None]] = set()
    for row in instruments:
        identity_key = _instrument_identity_key(row)
        symbol = _string_or_none(row.get("symbol"))
        if not identity_key or not symbol:
            continue
        year = int(row["year"]) if row.get("year") not in (None, "") else None
        source_report = _string_or_none(row.get("source_report"))
        key = (identity_key, symbol, year, source_report)
        if key in seen:
            continue
        seen.add(key)
        history[identity_key].append(
            {
                "symbol": symbol,
                "year": year,
                "as_of_date": _string_or_none(row.get("as_of_date")),
                "description": _string_or_none(row.get("description")),
                "isin": _string_or_none(row.get("isin")),
                "conid": _string_or_none(row.get("conid")),
                "source_report": source_report,
            }
        )
    for rows in history.values():
        rows.sort(key=lambda item: (item.get("year") or 9999, item.get("as_of_date") or "", item.get("symbol") or ""))
    return history


def _instrument_symbol_for_year(
    symbol_history: Mapping[str, Sequence[Mapping[str, Any]]],
    identity_key: str | None,
    year: int | None,
) -> str | None:
    if not identity_key or year is None:
        return None
    rows = list(symbol_history.get(identity_key, ()))
    if not rows:
        return None
    eligible = [row for row in rows if row.get("year") not in (None, "") and int(row["year"]) <= year]
    if eligible:
        return _string_or_none(eligible[-1].get("symbol"))
    return _string_or_none(rows[0].get("symbol"))


def _build_inferred_symbol_change_actions(instruments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for identity_key, history_rows in _instrument_symbol_history(instruments).items():
        previous_symbol: str | None = None
        previous_row: Mapping[str, Any] | None = None
        for row in history_rows:
            symbol = _string_or_none(row.get("symbol"))
            if not symbol:
                continue
            if previous_symbol is not None and symbol != previous_symbol:
                isin = _string_or_none(row.get("isin") or (previous_row or {}).get("isin"))
                conid = _string_or_none(row.get("conid") or (previous_row or {}).get("conid"))
                description = _string_or_none(row.get("description") or (previous_row or {}).get("description"))
                source_report = _string_or_none(row.get("source_report"))
                identity_description = isin or conid or identity_key
                rows.append(
                    {
                        "date": _string_or_none(row.get("as_of_date")),
                        "date_time": None,
                        "report_date": _string_or_none(row.get("as_of_date")),
                        "asset_type": None,
                        "symbol": symbol,
                        "isin": isin,
                        "action_type": "symbol_change",
                        "description": (
                            f"Inferred symbol change for {description or identity_description}: "
                            f"{previous_symbol} -> {symbol}. Stable identity: {identity_key}."
                        ),
                        "quantity": "0",
                        "proceeds": "0",
                        "value": "0",
                        "currency": None,
                        "realized_pl": "0",
                        "year": int(row["year"]) if row.get("year") not in (None, "") else None,
                        "exit_price": "0",
                        "country": (isin[:2].replace("XS", "BE") if isin else None),
                        "source_report": (
                            f"inferred:financial_instrument_information:{source_report}"
                            if source_report
                            else "inferred:financial_instrument_information"
                        ),
                        "_identity_key": identity_key,
                        "_previous_symbol": previous_symbol,
                        "_new_symbol": symbol,
                        "_conid": conid,
                    }
                )
            previous_symbol = symbol
            previous_row = row
    return rows


def _symbol_aliases(symbol: str | None) -> tuple[str, ...]:
    if not symbol:
        return ()
    aliases = [symbol]
    if "," in symbol:
        aliases.extend(part.strip() for part in symbol.split(",") if part.strip())
    return tuple(dict.fromkeys(aliases))


def _lookup_instrument(
    lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    symbol: str | None,
    year: int | None,
) -> dict[str, Any] | None:
    if not symbol:
        return None
    return lookup.get((symbol, year)) or lookup.get((symbol, None))


def _build_corporate_actions(reports: Sequence[ParsedIbReport]) -> list[dict[str, Any]]:
    raw_rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(IB_SECTION_CA, []):
            asset_category = _string_or_none(row.get("Asset Category"))
            description = _string_or_none(row.get("Description"))
            if not description or asset_category == "Total":
                continue
            symbol, extracted_isin, _ = _extract_symbol_isin(description)
            isin_match = ISIN_RE.search(description or "")
            isin = extracted_isin or (isin_match.group(1) if isin_match else None)
            target_symbol, target_isin = _corporate_action_target_symbol_isin(description)
            action_date = _parse_datetime(row.get("Date/Time")) or _parse_datetime(row.get("Date_Time"))
            quantity = _decimal(row.get("Quantity"))
            proceeds = _decimal(row.get("Proceeds"))
            raw_rows.append(
                {
                    "date": _date_to_iso(action_date),
                    "date_time": action_date.isoformat(sep=" ") if action_date else None,
                    "report_date": _date_to_iso(_parse_date(row.get("Report Date"))),
                    "asset_type": asset_category,
                    "symbol": symbol,
                    "isin": isin,
                    "action_type": _infer_corporate_action_type(description),
                    "description": description,
                    "quantity": str(quantity),
                    "proceeds": str(proceeds),
                    "value": str(_decimal(row.get("Value"))),
                    "currency": _string_or_none(row.get("Currency")),
                    "realized_pl": str(_decimal(row.get("Realized P/L") or row.get("Realized_P_L"))),
                    "year": action_date.year if action_date else _year_for_report(report),
                    "exit_price": str(abs(proceeds / quantity)) if quantity else "0",
                    "country": (isin[:2].replace("XS", "BE") if isin else None),
                    "source_report": str(report.path),
                    "_source_symbol": symbol,
                    "_source_isin": isin,
                    "_target_symbol": target_symbol,
                    "_target_isin": target_isin,
                    "_ratio": str(_split_ratio(description) or Decimal("1")),
                }
            )

    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        grouped[(row.get("date_time"), row.get("description"))].append(row)

    rows: list[dict[str, Any]] = []
    for group_rows in grouped.values():
        quantity_sum = sum((_decimal(row.get("quantity")) for row in group_rows), Decimal("0"))
        proceeds_sum = sum((_decimal(row.get("proceeds")) for row in group_rows), Decimal("0"))
        realized_sum = sum((_decimal(row.get("realized_pl")) for row in group_rows), Decimal("0"))
        if len(group_rows) > 1 and quantity_sum == 0 and proceeds_sum == 0 and realized_sum == 0:
            continue
        rows.extend(group_rows)
    return rows


def _corporate_action_target_symbol_isin(description: str | None) -> tuple[str | None, str | None]:
    if not description:
        return None, None
    isin_matches = ISIN_RE.findall(description)
    target_isin = isin_matches[-1] if len(isin_matches) >= 2 else None
    target_symbol: str | None = None
    trailing_match = re.search(r"\(([^()]*)\)\s*$", description)
    if trailing_match:
        first_token = trailing_match.group(1).split(",", 1)[0].strip()
        target_symbol = first_token or None
    return target_symbol, target_isin


def _infer_corporate_action_type(description: str | None) -> str | None:
    if not description:
        return None
    lowered = description.lower()
    for token in ("spinoff", "split", "merger", "merged", "maturity", "full call", "redemption", "buyback"):
        if token in lowered:
            return token.replace(" ", "_")
    return "other"


def _build_corporate_action_identity_changes(
    corporate_actions: Sequence[Mapping[str, Any]],
) -> list[CorporateActionIdentityChange]:
    changes: list[CorporateActionIdentityChange] = []
    for action in corporate_actions:
        action_type = _string_or_none(action.get("action_type"))
        if action_type not in {"merger", "merged"}:
            continue
        action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
        old_isin = _string_or_none(action.get("_source_isin") or action.get("isin"))
        old_symbol = _string_or_none(action.get("_source_symbol") or action.get("symbol"))
        new_isin = _string_or_none(action.get("_target_isin"))
        new_symbol = _string_or_none(action.get("_target_symbol"))
        if not action_dt or not old_isin or not new_isin or old_isin == new_isin:
            continue
        ratio = _decimal(action.get("_ratio") or "1") or Decimal("1")
        changes.append(
            CorporateActionIdentityChange(
                action_dt=action_dt,
                old_symbol=old_symbol,
                old_isin=old_isin,
                new_symbol=new_symbol,
                new_isin=new_isin,
                ratio=ratio,
                description=_string_or_none(action.get("description")),
            )
        )
    changes.sort(key=lambda item: item.action_dt)
    return changes


def _apply_corporate_action_identity_changes_to_records(
    records: Sequence[Mapping[str, Any]],
    changes: Sequence[CorporateActionIdentityChange],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    *,
    date_fields: Sequence[str],
) -> list[dict[str, Any]]:
    if not changes:
        return [dict(record) for record in records]
    normalized: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        row_dt = _first_record_datetime(row, date_fields)
        if row_dt is None:
            normalized.append(row)
            continue
        for change in changes:
            if row_dt >= change.action_dt:
                continue
            if not _record_matches_identity_change(row, change):
                continue
            _apply_identity_change_to_record(row, change, instrument_lookup, row_dt.year)
        normalized.append(row)
    return normalized


def _first_record_datetime(record: Mapping[str, Any], date_fields: Sequence[str]) -> datetime | None:
    for field_name in date_fields:
        value = record.get(field_name)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
        parsed_date = _parse_date(value)
        if parsed_date is not None:
            return datetime.combine(parsed_date, datetime.min.time())
    return None


def _record_matches_identity_change(record: Mapping[str, Any], change: CorporateActionIdentityChange) -> bool:
    record_isin = _string_or_none(record.get("isin"))
    record_symbol = _string_or_none(record.get("symbol"))
    if change.old_isin and record_isin == change.old_isin:
        return True
    return bool(change.old_symbol and record_symbol == change.old_symbol)


def _apply_identity_change_to_record(
    row: dict[str, Any],
    change: CorporateActionIdentityChange,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    year: int | None,
) -> None:
    instrument = (
        _lookup_instrument(instrument_lookup, change.new_isin, year)
        or _lookup_instrument(instrument_lookup, change.new_symbol, year)
        or _lookup_instrument(instrument_lookup, change.new_isin, None)
        or _lookup_instrument(instrument_lookup, change.new_symbol, None)
        or {}
    )
    new_symbol = _string_or_none(instrument.get("symbol")) or change.new_symbol
    new_isin = _string_or_none(instrument.get("isin")) or change.new_isin
    conid = _string_or_none(instrument.get("conid") or row.get("_conid"))
    row["symbol"] = new_symbol
    row["isin"] = new_isin
    row["country"] = instrument.get("country") or (new_isin[:2].replace("XS", "BE") if new_isin else row.get("country"))
    if "exchange" in row:
        row["exchange"] = instrument.get("listing_exchange") or row.get("exchange")
    if "_conid" in row:
        row["_conid"] = conid
    if "_instrument_identity_key" in row:
        row["_instrument_identity_key"] = _instrument_identity_key_from_values(isin=new_isin, conid=conid, symbol=new_symbol)
    if change.ratio != 0 and change.ratio != 1:
        if row.get("calculation_quantity") not in (None, ""):
            row["calculation_quantity"] = str(_decimal(row.get("calculation_quantity")) / change.ratio)
        if row.get("calculation_price") not in (None, ""):
            row["calculation_price"] = str(_decimal(row.get("calculation_price")) * change.ratio)
    row["corporate_action_adjustment"] = change.description


def _build_dividends(
    reports: Sequence[ParsedIbReport],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    tax_by_key: dict[tuple[str | None, str | None, str | None], Decimal] = defaultdict(Decimal)
    tax_meta_by_key: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
    for report in reports:
        for row in report.rows.get(IB_SECTION_TAX, []):
            if _is_total_amount_row(row):
                continue
            pay_date = _parse_date(row.get("Date"))
            year = pay_date.year if pay_date else _year_for_report(report)
            symbol, security_id, dividend_type = _extract_symbol_isin(_string_or_none(row.get("Description")))
            symbol, isin = _resolve_dividend_symbol_isin(symbol, security_id, year, instrument_lookup)
            key = (_date_to_iso(pay_date), isin, _string_or_none(row.get("Currency")))
            tax_by_key[key] += _decimal(row.get("Amount"))
            tax_meta_by_key.setdefault(
                key,
                {
                    "date": pay_date,
                    "symbol": symbol,
                    "isin": isin,
                    "currency": _string_or_none(row.get("Currency")),
                    "dividend_type": dividend_type,
                    "description": _string_or_none(row.get("Description")),
                    "source_report": str(report.path),
                },
            )

    dividends: list[dict[str, Any]] = []
    dividend_inputs: list[tuple[ParsedIbReport, date | None, str | None, str | None, str | None, str | None, Decimal]] = []
    dividend_group_gross: dict[tuple[str | None, str | None, str | None], Decimal] = defaultdict(Decimal)
    for report in reports:
        for row in report.rows.get(IB_SECTION_DIVIDENDS, []):
            if _is_total_amount_row(row):
                continue
            pay_date = _parse_date(row.get("Date"))
            year = pay_date.year if pay_date else _year_for_report(report)
            symbol, security_id, dividend_type = _extract_symbol_isin(_string_or_none(row.get("Description")))
            symbol, isin = _resolve_dividend_symbol_isin(symbol, security_id, year, instrument_lookup)
            currency = _string_or_none(row.get("Currency"))
            gross = _decimal(row.get("Amount"))
            key = (_date_to_iso(pay_date), isin, currency)
            dividend_group_gross[key] += gross
            dividend_inputs.append((report, pay_date, symbol, isin, dividend_type, currency, gross))

    consumed_tax_keys: set[tuple[str | None, str | None, str | None]] = set()
    for report, pay_date, symbol, isin, dividend_type, currency, gross in dividend_inputs:
        key = (_date_to_iso(pay_date), isin, currency)
        group_gross = dividend_group_gross.get(key, Decimal("0"))
        withholding = tax_by_key.get(key, Decimal("0")) * (gross / group_gross) if group_gross else Decimal("0")
        if key in tax_by_key:
            consumed_tax_keys.add(key)
        dividends.append(
            _dividend_record(
                report=report,
                pay_date=pay_date,
                symbol=symbol,
                isin=isin,
                dividend_type=dividend_type,
                currency=currency,
                gross=gross,
                withholding=withholding,
                fx_provider=fx_provider,
                warnings=warnings,
            )
        )

    for key, withholding in tax_by_key.items():
        if key in consumed_tax_keys:
            continue
        meta = tax_meta_by_key[key]
        dividends.append(
            _dividend_record(
                report=None,
                pay_date=meta["date"],
                symbol=meta["symbol"],
                isin=meta["isin"],
                dividend_type=meta["dividend_type"],
                currency=meta["currency"],
                gross=Decimal("0"),
                withholding=withholding,
                fx_provider=fx_provider,
                warnings=warnings,
                source_report=meta["source_report"],
                adjustment_description=meta["description"],
            )
        )
    return dividends


def _resolve_dividend_symbol_isin(
    symbol: str | None,
    security_id: str | None,
    year: int | None,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> tuple[str | None, str | None]:
    security_id = _string_or_none(security_id)
    symbol = _string_or_none(symbol)
    instrument = _lookup_instrument(instrument_lookup, security_id, year) or _lookup_instrument(
        instrument_lookup, symbol, year
    )
    if instrument:
        return _string_or_none(instrument.get("symbol")) or symbol, _string_or_none(instrument.get("isin"))
    if security_id and ISIN_RE.fullmatch(security_id):
        return symbol, security_id
    return symbol, None


def _dividend_record(
    *,
    report: ParsedIbReport | None,
    pay_date: date | None,
    symbol: str | None,
    isin: str | None,
    dividend_type: str | None,
    currency: str | None,
    gross: Decimal,
    withholding: Decimal,
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
    source_report: str | None = None,
    adjustment_description: str | None = None,
) -> dict[str, Any]:
    net = gross + withholding
    year = pay_date.year if pay_date else _year_for_report(report) if report else None
    rate = _annual_rate(fx_provider, year, currency, warnings)
    country = isin[:2].replace("XS", "BE") if isin and ISIN_RE.fullmatch(isin) else None
    tax_usd = gross * Decimal("0.10")
    return {
        "date": _date_to_iso(pay_date),
        "pay_date": _date_to_iso(pay_date),
        "symbol": symbol,
        "isin": isin,
        "country": country,
        "currency": currency,
        "gross_amount": str(gross),
        "withholding_tax": str(withholding),
        "net_amount": str(net),
        "kzt_rate": str(rate) if rate is not None else None,
        "gross_amount_kzt": _amount_kzt(gross, rate),
        "tax": str(tax_usd),
        "tax_kzt": _amount_kzt(tax_usd, rate),
        "offshore_flag": None,
        "kase_aix_preferential_flag": None,
        "dividend_type": dividend_type,
        "source_report": source_report or (str(report.path) if report else None),
        "adjustment_description": adjustment_description,
    }


def _build_transfers(
    reports: Sequence[ParsedIbReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    rows: list[dict[str, Any]] = []
    security_rows: list[dict[str, Any]] = []
    totals_by_currency: dict[str, Decimal] = defaultdict(Decimal)
    for report in reports:
        for row in report.rows.get(IB_SECTION_DEPOSITS, []):
            currency = _string_or_none(row.get("Currency"))
            if not currency or currency.startswith("Total"):
                continue
            amount = _decimal(row.get("Amount"))
            transfer_date = _parse_date(row.get("Settle Date"))
            totals_by_currency[currency] += amount
            rows.append(
                {
                    "date": _date_to_iso(transfer_date),
                    "transfer_type": "cash",
                    "direction": "in" if amount >= 0 else "out",
                    "asset_type": "cash",
                    "symbol": None,
                    "isin": None,
                    "currency": currency,
                    "quantity": None,
                    "price": None,
                    "amount": str(amount),
                    "broker_comment": _string_or_none(row.get("Description")),
                    "counterparty": None,
                    "source_report": str(report.path),
                }
            )
        for idx, row in enumerate(report.rows.get("Transfers", []), start=1):
            asset_type = _string_or_none(row.get("Asset Category"))
            if not asset_type or asset_type.startswith("Total"):
                continue
            symbol = _normalize_position_symbol(_string_or_none(row.get("Symbol")))
            if not symbol:
                continue
            transfer_date = _parse_date(row.get("Date"))
            year = transfer_date.year if transfer_date else _year_for_report(report)
            instrument = _lookup_instrument(instrument_lookup, symbol, year) or {}
            qty = _decimal(row.get("Qty") or row.get("Quantity"))
            currency = _string_or_none(row.get("Currency"))
            direction = str(row.get("Direction") or "").lower() or None
            isin = _string_or_none(instrument.get("isin") or row.get("Security ID"))
            conid = _string_or_none(instrument.get("conid"))
            multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
            identity_key = _instrument_identity_key_from_values(isin=isin, conid=conid, symbol=symbol)
            price, amount, cost_basis_status = _incoming_transfer_cost_basis(direction=direction)
            security_rows.append(
                {
                    "date": _date_to_iso(transfer_date),
                    "transfer_type": "security",
                    "direction": direction,
                    "asset_type": asset_type,
                    "symbol": symbol,
                    "isin": isin,
                    "currency": currency,
                    "country": instrument.get("country"),
                    "quantity": _decimal_text(abs(qty)) if direction in {"in", "out"} else str(qty),
                    "price": _price_text(price) if price is not None else None,
                    "amount": None,
                    "broker_comment": _string_or_none(row.get("Description")),
                    "counterparty": _transfer_counterparty(row),
                    "source_report": str(report.path),
                    "_transfer_id": f"{report.path.name}:transfer:{idx}",
                    "_raw_quantity": str(qty),
                    "_instrument_identity_key": identity_key,
                    "_conid": conid,
                    "_multiplier": _multiplier_text(multiplier),
                    "_cost_basis_amount": _decimal_text(amount) if amount is not None else None,
                    "_transfer_cost_basis_status": cost_basis_status,
                }
            )
    rows.extend(_drop_zero_sum_security_transfers(security_rows))
    return rows, totals_by_currency


def _transfer_counterparty(row: Mapping[str, Any]) -> str | None:
    account = _string_or_none(row.get("Xfer Account"))
    company = _string_or_none(row.get("Xfer Company"))
    if account and account != "--":
        return account
    if company and company != "--":
        return company
    return account or company


def _incoming_transfer_cost_basis(
    *,
    direction: str | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    if direction != "in":
        return None, None, None
    return None, None, "pending_transfer_out_fifo_cost_basis"


def _drop_zero_sum_security_transfers(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            _string_or_none(row.get("date")),
            _string_or_none(row.get("asset_type")),
            _string_or_none(row.get("symbol")),
            _string_or_none(row.get("isin")),
            _string_or_none(row.get("currency")),
            _string_or_none(row.get("counterparty")),
        )
        raw_quantity = _decimal(row.get("_raw_quantity") or row.get("quantity"))
        group = grouped.setdefault(key, {"rows": [], "quantity": Decimal("0"), "ids": []})
        group["rows"].append(dict(row))
        group["quantity"] += raw_quantity
        if row.get("_transfer_id"):
            group["ids"].append(row.get("_transfer_id"))

    result: list[dict[str, Any]] = []
    for group in grouped.values():
        net_quantity = group["quantity"]
        if net_quantity == 0:
            continue
        source_rows = list(group["rows"])
        row = _representative_transfer_row(source_rows, net_quantity)
        declared_direction = str(row.get("direction") or "").lower()
        if declared_direction == "out" and net_quantity > 0:
            continue
        if declared_direction == "in" and net_quantity < 0:
            continue
        row["direction"] = "in" if net_quantity > 0 else "out"
        row["quantity"] = _decimal_text(abs(net_quantity))
        row["_raw_quantity"] = str(net_quantity)
        if group["ids"]:
            row["_transfer_id"] = ";".join(str(value) for value in group["ids"])
        result.append(row)
    return result


def _representative_transfer_row(rows: Sequence[Mapping[str, Any]], net_quantity: Decimal) -> dict[str, Any]:
    if net_quantity > 0:
        candidates = [row for row in rows if _decimal(row.get("_raw_quantity") or row.get("quantity")) > 0]
    else:
        candidates = [row for row in rows if _decimal(row.get("_raw_quantity") or row.get("quantity")) < 0]
    source = candidates[-1] if candidates else rows[-1]
    return dict(source)


def _canonical_transfer_rows(transfers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    columns = (
        "date",
        "transfer_type",
        "direction",
        "asset_type",
        "symbol",
        "isin",
        "currency",
        "quantity",
        "price",
        "enter_date",
        "amount",
        "broker_comment",
        "counterparty",
        "source_report",
    )
    return [{column: transfer.get(column) for column in columns} for transfer in transfers]


def _trade_id(report: ParsedIbReport, idx: int) -> str:
    return f"{report.path.name}:{idx}"


def _build_trades(reports: Sequence[ParsedIbReport], instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        for idx, row in enumerate(report.rows.get(IB_SECTION_TRADES, []), start=1):
            if row.get("DataDiscriminator") and row.get("DataDiscriminator") != "Order":
                continue
            symbol = _strip_yield_suffix(_string_or_none(row.get("Symbol")))
            trade_dt = _parse_datetime(row.get("Date/Time"))
            year = trade_dt.year if trade_dt else _year_for_report(report)
            instrument = _lookup_instrument(instrument_lookup, symbol, year) or {}
            quantity = _decimal(row.get("Quantity"))
            proceeds = _decimal(row.get("Proceeds"))
            instrument_multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
            price = _decimal(row.get("T. Price"))
            multiplier = _effective_transaction_multiplier(instrument_multiplier, quantity, price, proceeds)
            amount_from_price = abs(quantity * price * multiplier)
            gross = abs(proceeds) if proceeds else amount_from_price
            commission = abs(_decimal(row.get("Comm/Fee") or row.get("Comm in USD")))
            asset_type = _string_or_none(row.get("Asset Category")) or instrument.get("asset_type")
            isin = instrument.get("isin")
            conid = _string_or_none(instrument.get("conid"))
            country = instrument.get("country") or _country_from_instrument(asset_type, _string_or_none(isin), instrument.get("listing_exchange"))
            broker_realized_pl = _broker_trade_pnl(row, asset_type)
            rows.append(
                {
                    "date_time": trade_dt.isoformat(sep=" ") if trade_dt else None,
                    "trade_id": _trade_id(report, idx),
                    "trade_type": "trade",
                    "symbol": symbol,
                    "isin": isin,
                    "asset_type": asset_type,
                    "quantity": str(quantity),
                    "calculation_quantity": str(quantity),
                    "calculation_price": str(price),
                    "multiplier": _multiplier_text(multiplier),
                    "_calculation_multiplier": str(multiplier),
                    "price": str(price),
                    "amount": str(gross),
                    "commission": str(commission),
                    "amount_with_commission": str(gross + commission),
                    "currency": _string_or_none(row.get("Currency")),
                    "exchange": instrument.get("listing_exchange"),
                    "country": country,
                    "source_report": str(report.path),
                    "_instrument_identity_key": _instrument_identity_key_from_values(isin=_string_or_none(isin), conid=conid, symbol=symbol),
                    "_conid": conid,
                    "_broker_realized_pl": str(broker_realized_pl) if broker_realized_pl is not None else None,
                    "_broker_basis": str(_decimal(row.get("Basis"))) if row.get("Basis") not in (None, "") else None,
                    "_broker_code": _string_or_none(row.get("Code")),
                }
            )
    return rows


def _broker_trade_pnl(row: Mapping[str, Any], asset_type: str | None) -> Decimal | None:
    if str(asset_type or "").lower() == "forex":
        for field_name in ("MTM in USD", "MTM P/L"):
            if row.get(field_name) not in (None, ""):
                return _decimal(row.get(field_name))
        return None
    if row.get("Realized P/L") not in (None, ""):
        return _decimal(row.get("Realized P/L"))
    return None


def _canonical_trade_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    columns = (
        "date_time",
        "trade_id",
        "trade_type",
        "symbol",
        "isin",
        "asset_type",
        "quantity",
        "price",
        "multiplier",
        "amount",
        "commission",
        "amount_with_commission",
        "currency",
        "exchange",
        "country",
        "source_report",
    )
    return [{column: trade.get(column) for column in columns} for trade in trades]


def _effective_transaction_multiplier(
    instrument_multiplier: Decimal,
    quantity: Decimal,
    price: Decimal,
    proceeds: Decimal,
) -> Decimal:
    if quantity and price and proceeds:
        broker_amount = abs(proceeds)
        instrument_amount = abs(quantity * price * (instrument_multiplier or Decimal("1")))
        if _amounts_close(instrument_amount, broker_amount):
            return _normalize_multiplier(instrument_multiplier)
        computed = broker_amount / (abs(quantity) * abs(price))
        normalized = _normalize_multiplier(computed)
        if _amounts_close(abs(quantity * price * normalized), broker_amount):
            return normalized
        return computed
    return _normalize_multiplier(instrument_multiplier or Decimal("1"))


def _amounts_close(left: Decimal, right: Decimal) -> bool:
    tolerance = max(Decimal("0.01"), abs(right) * Decimal("0.000001"))
    return abs(left - right) <= tolerance


def _normalize_multiplier(value: Decimal) -> Decimal:
    if value == 0:
        return Decimal("0")
    common_values = (
        Decimal("0.0001"),
        Decimal("0.001"),
        Decimal("0.01"),
        Decimal("0.1"),
        Decimal("1"),
        Decimal("10"),
        Decimal("100"),
        Decimal("1000"),
    )
    for common in common_values:
        tolerance = max(common * Decimal("0.000001"), Decimal("0.00000001"))
        if abs(value - common) <= tolerance:
            return common
    nearest_integer = value.to_integral_value(rounding=ROUND_HALF_UP)
    if nearest_integer and abs(value - nearest_integer) <= max(abs(nearest_integer) * Decimal("0.000001"), Decimal("0.00000001")):
        return nearest_integer
    return value


def _build_broker_trade_realized_pl(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        if _is_fx_trade(trade) or _string_or_none(trade.get("_synthetic_source")) == "corporate_action":
            continue
        realized_pl = trade.get("_broker_realized_pl")
        if realized_pl in (None, ""):
            continue
        rows.append(
            {
                "trade_id": trade.get("trade_id"),
                "date_time": trade.get("date_time"),
                "symbol": trade.get("symbol"),
                "currency": trade.get("currency"),
                "realized_pl": realized_pl,
                "source_report": trade.get("source_report"),
            }
        )
    return rows


def _strip_yield_suffix(symbol: str | None) -> str | None:
    if not symbol:
        return None
    return re.sub(r"\s+\d+(?:\.\d+)?%$", "", symbol).strip()


def _sort_trades_by_datetime(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(trade) for trade in trades),
        key=lambda trade: (
            _parse_datetime(trade.get("date_time")) or datetime.max,
            str(trade.get("trade_id") or ""),
        ),
    )


def _apply_corporate_action_split_adjustments_to_fifo_trades(
    trades: Sequence[Mapping[str, Any]],
    corporate_actions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    fifo_trades = [dict(trade) for trade in trades]
    for action in corporate_actions:
        action_type = action.get("action_type")
        action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
        isin = _string_or_none(action.get("isin"))
        if not action_type or not action_dt or not isin:
            continue
        if action_type == "split":
            ratio = _split_ratio(_string_or_none(action.get("description")))
            if ratio is None or ratio == 0:
                continue
            for trade in fifo_trades:
                trade_dt = _parse_datetime(trade.get("date_time"))
                if trade_dt is None or trade_dt >= action_dt:
                    continue
                if _string_or_none(trade.get("isin")) != isin:
                    continue
                quantity = _decimal(trade.get("calculation_quantity") or trade.get("quantity"))
                price = _decimal(trade.get("calculation_price") or trade.get("price"))
                trade["calculation_quantity"] = str(quantity / ratio)
                trade["calculation_price"] = str(price * ratio)
                trade["corporate_action_adjustment"] = action.get("description")
    return fifo_trades


def _build_synthetic_corporate_action_trades(
    corporate_actions: Sequence[Mapping[str, Any]],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for action in corporate_actions:
        if _is_spinoff_corporate_action(action):
            trade = _synthetic_spinoff_trade(action, instrument_lookup)
        elif _is_synthetic_exit_corporate_action(action):
            trade = _synthetic_corporate_action_trade(action, instrument_lookup)
        else:
            trade = None
        if trade is not None:
            trades.append(trade)
    return trades


def _synthetic_spinoff_trade(
    action: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> dict[str, Any] | None:
    action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
    target_isin = _string_or_none(action.get("_target_isin"))
    if action_dt is None or not target_isin:
        return None
    quantity = _decimal(action.get("quantity"))
    if quantity <= 0:
        return None
    year = action_dt.year
    instrument = _lookup_instrument(instrument_lookup, target_isin, year) or _lookup_instrument(instrument_lookup, target_isin, None) or {}
    symbol = instrument.get("symbol") or action.get("_target_symbol") or target_isin
    instrument_multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
    source_report = _string_or_none(action.get("source_report"))
    conid = _string_or_none(instrument.get("conid"))
    return {
        "date_time": action_dt.isoformat(sep=" "),
        "trade_id": f"CA:{source_report}:{action_dt.isoformat()}:{target_isin}:spinoff",
        "trade_type": "corporate_action:spinoff",
        "symbol": symbol,
        "isin": target_isin,
        "asset_type": action.get("asset_type") or instrument.get("asset_type"),
        "quantity": str(quantity),
        "calculation_quantity": str(quantity),
        "calculation_price": "0",
        "price": "0",
        "multiplier": _multiplier_text(instrument_multiplier),
        "_calculation_multiplier": str(instrument_multiplier),
        "amount": "0",
        "commission": "0",
        "amount_with_commission": "0",
        "currency": action.get("currency"),
        "exchange": instrument.get("listing_exchange"),
        "country": instrument.get("country") or target_isin[:2].replace("XS", "BE"),
        "_broker_realized_pl": "0",
        "_corporate_action_type": "spinoff",
        "_synthetic_source": "corporate_action",
        "_instrument_identity_key": _instrument_identity_key_from_values(isin=target_isin, conid=conid, symbol=_string_or_none(symbol)),
        "_conid": conid,
        "source_report": f"corporate_action:{source_report}" if source_report else "corporate_action",
        "corporate_action_adjustment": action.get("description"),
    }


def _synthetic_corporate_action_trade(
    action: Mapping[str, Any],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> dict[str, Any] | None:
    action_dt = _parse_datetime(action.get("date_time") or action.get("date"))
    isin = _string_or_none(action.get("isin"))
    if action_dt is None or not isin:
        return None
    quantity = _decimal(action.get("quantity"))
    proceeds = _decimal(action.get("proceeds"))
    if quantity >= 0 or proceeds == 0:
        return None
    year = action_dt.year
    instrument = _lookup_instrument(instrument_lookup, isin, year) or _lookup_instrument(instrument_lookup, isin, None) or {}
    symbol = instrument.get("symbol") or action.get("symbol") or isin
    price = abs(proceeds / quantity) if quantity else Decimal("0")
    instrument_multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
    multiplier = _effective_transaction_multiplier(instrument_multiplier, quantity, price, proceeds)
    source_report = _string_or_none(action.get("source_report"))
    action_type = _string_or_none(action.get("action_type")) or "corporate_action"
    conid = _string_or_none(instrument.get("conid"))
    return {
        "date_time": action_dt.isoformat(sep=" "),
        "trade_id": f"CA:{source_report}:{action_dt.isoformat()}:{isin}",
        "trade_type": f"corporate_action:{action_type}",
        "symbol": symbol,
        "isin": isin,
        "asset_type": action.get("asset_type") or _asset_type_from_action(action),
        "quantity": str(quantity),
        "calculation_quantity": str(quantity),
        "calculation_price": str(price),
        "price": str(price),
        "multiplier": _multiplier_text(multiplier),
        "_calculation_multiplier": str(multiplier),
        "amount": str(abs(proceeds)),
        "commission": "0",
        "amount_with_commission": str(abs(proceeds)),
        "currency": action.get("currency"),
        "exchange": instrument.get("listing_exchange"),
        "country": action.get("country") or instrument.get("country"),
        "_broker_realized_pl": action.get("realized_pl"),
        "_corporate_action_type": action_type,
        "_synthetic_source": "corporate_action",
        "_instrument_identity_key": _instrument_identity_key_from_values(isin=isin, conid=conid, symbol=_string_or_none(symbol)),
        "_conid": conid,
        "source_report": f"corporate_action:{source_report}" if source_report else "corporate_action",
        "corporate_action_adjustment": action.get("description"),
    }


def _is_spinoff_corporate_action(action: Mapping[str, Any]) -> bool:
    return action.get("action_type") == "spinoff"


def _is_synthetic_exit_corporate_action(action: Mapping[str, Any]) -> bool:
    action_type = action.get("action_type")
    if _is_identity_change_merger_action(action):
        return False
    if action_type not in {"maturity", "full_call", "redemption", "merged", "merger", "buyback"}:
        return False
    return _decimal(action.get("quantity")) < 0 and _decimal(action.get("proceeds")) != 0


def _is_identity_change_merger_action(action: Mapping[str, Any]) -> bool:
    if action.get("action_type") not in {"merger", "merged"}:
        return False
    description = str(action.get("description") or "").lower()
    if "cash and stock merger" in description:
        return True
    source_isin = _string_or_none(action.get("_source_isin") or action.get("isin"))
    target_isin = _string_or_none(action.get("_target_isin"))
    return bool(source_isin and target_isin and source_isin != target_isin)


def _split_ratio(description: str | None) -> Decimal | None:
    if not description:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s+for\s+(\d+(?:\.\d+)?)", description)
    if not match:
        return None
    from_qty = Decimal(match.group(1))
    to_qty = Decimal(match.group(2))
    return to_qty / from_qty


def _asset_type_from_action(action: Mapping[str, Any]) -> str | None:
    description = str(action.get("description") or "")
    if "Treasury Bill" in description:
        return "Treasury Bills"
    if "Bond" in description or "Full Call" in description:
        return "Bonds"
    return None


def _build_initial_fifo_lots(
    reports: Sequence[ParsedIbReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    warnings: list[str],
) -> list[tuple[tuple[str, str | None, str], str, FifoOpenLot]]:
    earliest_reports = _earliest_reports(reports)
    currency_lookup = _instrument_currency_lookup(reports, trades)
    lots: list[tuple[tuple[str, str | None, str], str, FifoOpenLot]] = []
    for report in earliest_reports:
        year = _year_for_report(report)
        prior_lot_dt = _report_start_datetime(report)
        for row in report.rows.get(IB_SECTION_MTM, []):
            asset_type = _string_or_none(row.get("Asset Category"))
            symbol = _strip_yield_suffix(_string_or_none(row.get("Symbol")))
            prior_quantity = _decimal(row.get("Prior Quantity"))
            if not symbol or not asset_type or asset_type.startswith("Total") or prior_quantity == 0:
                continue
            instrument = _lookup_instrument(instrument_lookup, symbol, year) or _lookup_instrument(instrument_lookup, symbol, None) or {}
            isin = _string_or_none(instrument.get("isin"))
            conid = _string_or_none(instrument.get("conid"))
            currency = _lookup_initial_lot_currency(currency_lookup, symbol, isin)
            if not currency:
                warning = f"Cannot seed prior FIFO lot for {symbol}; currency is not available in discovered reports."
                if warning not in warnings:
                    warnings.append(warning)
                continue
            multiplier = _decimal(instrument.get("multiplier") or "1") or Decimal("1")
            prior_price = _decimal(row.get("Prior Price"))
            quantity = abs(prior_quantity)
            side = "long" if prior_quantity > 0 else "short"
            country = instrument.get("country") or _country_from_instrument(asset_type, isin, instrument.get("listing_exchange"))
            lot = FifoOpenLot(
                asset_type=asset_type,
                symbol=symbol,
                isin=isin,
                currency=currency,
                country=_string_or_none(country),
                exchange=_string_or_none(instrument.get("listing_exchange")),
                date_time=prior_lot_dt,
                raw_quantity=prior_quantity,
                raw_amount=abs(quantity * prior_price * multiplier),
                raw_commission=Decimal("0"),
                price=prior_price,
                calculation_price=prior_price,
                multiplier=multiplier,
                quantity=quantity,
                broker_quantity=quantity,
                commission_per_unit=Decimal("0"),
                trade_id=None,
                opening_lot_status="missing_opening_lot",
            )
            instrument_key = _instrument_identity_key_from_values(isin=isin, conid=conid, symbol=symbol)
            if instrument_key:
                lots.append(((instrument_key, isin, currency), side, lot))
    return lots


def _earliest_reports(reports: Sequence[ParsedIbReport]) -> list[ParsedIbReport]:
    dated = [
        (report.period_start or report.period_end, report)
        for report in reports
        if report.period_start is not None or report.period_end is not None
    ]
    if not dated:
        return list(reports[:1])
    earliest = min(report_date for report_date, _ in dated)
    return [report for report_date, report in dated if report_date == earliest]


def _report_start_datetime(report: ParsedIbReport) -> datetime | None:
    report_date = report.period_start or report.period_end
    if report_date is None:
        return None
    return datetime(report_date.year, report_date.month, report_date.day)


def _instrument_currency_lookup(
    reports: Sequence[ParsedIbReport],
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for trade in trades:
        currency = _string_or_none(trade.get("currency"))
        if not currency:
            continue
        for key in (_string_or_none(trade.get("symbol")), _string_or_none(trade.get("isin"))):
            if key:
                lookup.setdefault(key, currency)
    for report in reports:
        for row in report.rows.get(IB_SECTION_POSITIONS, []):
            currency = _string_or_none(row.get("Currency"))
            symbol = _normalize_position_symbol(_string_or_none(row.get("Symbol")))
            if currency and symbol:
                lookup.setdefault(symbol, currency)
    return lookup


def _lookup_initial_lot_currency(currency_lookup: Mapping[str, str], symbol: str, isin: str | None) -> str | None:
    return currency_lookup.get(symbol) or (currency_lookup.get(isin) if isin else None)


def _apply_corporate_action_identity_changes_to_initial_lots(
    lots: Sequence[tuple[tuple[str, str | None, str], str, FifoOpenLot]],
    changes: Sequence[CorporateActionIdentityChange],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> list[tuple[tuple[str, str | None, str], str, FifoOpenLot]]:
    if not changes:
        return list(lots)
    normalized: list[tuple[tuple[str, str | None, str], str, FifoOpenLot]] = []
    for _, side, lot in lots:
        for change in changes:
            if lot.date_time is not None and lot.date_time >= change.action_dt:
                continue
            if not _lot_matches_identity_change(lot, change):
                continue
            _apply_identity_change_to_lot(lot, change, instrument_lookup, change.action_dt.year)
        instrument_key = _instrument_identity_key_from_values(isin=lot.isin, conid=None, symbol=lot.symbol)
        if instrument_key:
            normalized.append(((instrument_key, lot.isin, lot.currency), side, lot))
    return normalized


def _lot_matches_identity_change(lot: FifoOpenLot, change: CorporateActionIdentityChange) -> bool:
    if change.old_isin and lot.isin == change.old_isin:
        return True
    return bool(change.old_symbol and lot.symbol == change.old_symbol)


def _apply_identity_change_to_lot(
    lot: FifoOpenLot,
    change: CorporateActionIdentityChange,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    year: int | None,
) -> None:
    instrument = (
        _lookup_instrument(instrument_lookup, change.new_isin, year)
        or _lookup_instrument(instrument_lookup, change.new_symbol, year)
        or _lookup_instrument(instrument_lookup, change.new_isin, None)
        or _lookup_instrument(instrument_lookup, change.new_symbol, None)
        or {}
    )
    new_symbol = _string_or_none(instrument.get("symbol")) or change.new_symbol
    new_isin = _string_or_none(instrument.get("isin")) or change.new_isin
    lot.symbol = new_symbol or lot.symbol
    lot.isin = new_isin or lot.isin
    lot.country = _string_or_none(instrument.get("country")) or (new_isin[:2].replace("XS", "BE") if new_isin else lot.country)
    if change.ratio != 0 and change.ratio != 1:
        lot.quantity = lot.quantity / change.ratio
        lot.broker_quantity = lot.quantity
        lot.calculation_price = lot.calculation_price * change.ratio
        lot.price = lot.price * change.ratio


def _build_fifo_and_positions(
    trades: Sequence[Mapping[str, Any]],
    transfers: Sequence[Mapping[str, Any]],
    initial_lots: Sequence[tuple[tuple[str, str | None, str], str, FifoOpenLot]],
    max_year: int | None,
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
    symbol_history: Mapping[str, Sequence[Mapping[str, Any]]],
    transfer_in_resolver: TransferInFifoResolver | None = None,
    broker_cost_basis_method: str = "fifo",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    fifo_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    transfer_rows: list[dict[str, Any]] = [
        dict(transfer)
        for transfer in transfers
        if not _is_outgoing_security_transfer(transfer) and not _is_incoming_security_transfer(transfer)
    ]
    inventory: dict[tuple[str, str | None, str], dict[str, deque[FifoOpenLot]]] = defaultdict(lambda: {"long": deque(), "short": deque()})
    grouped_events: dict[tuple[str, str | None, str], list[tuple[datetime, int, str, Mapping[str, Any]]]] = defaultdict(list)
    for key, side, lot in initial_lots:
        inventory[key][side].append(lot)
    for trade in trades:
        isin = _string_or_none(trade.get("isin"))
        instrument_key = _string_or_none(trade.get("_instrument_identity_key")) or _instrument_identity_key_from_values(
            isin=isin,
            conid=_string_or_none(trade.get("_conid")),
            symbol=_string_or_none(trade.get("symbol")),
        )
        trade_dt = _parse_datetime(trade.get("date_time"))
        if trade_dt is None or not instrument_key:
            continue
        grouped_events[(instrument_key, isin, str(trade.get("currency") or ""))].append((trade_dt, 0, "trade", trade))

    for transfer in transfers:
        is_outgoing_transfer = _is_outgoing_security_transfer(transfer)
        is_incoming_transfer = _is_incoming_security_transfer(transfer)
        if not (is_outgoing_transfer or is_incoming_transfer):
            continue
        transfer_dt = _transfer_datetime(transfer, end_of_day=is_outgoing_transfer)
        if is_incoming_transfer and (transfer.get("_converted_identity_key") or transfer.get("_converted_isin") or transfer.get("_converted_symbol")):
            isin = _string_or_none(transfer.get("_converted_isin") or transfer.get("isin"))
            instrument_key = _string_or_none(transfer.get("_converted_identity_key")) or _instrument_identity_key_from_values(
                isin=isin,
                conid=_string_or_none(transfer.get("_conid")),
                symbol=_string_or_none(transfer.get("_converted_symbol") or transfer.get("symbol")),
            )
        else:
            isin = _string_or_none(transfer.get("isin"))
            instrument_key = _string_or_none(transfer.get("_instrument_identity_key")) or _instrument_identity_key_from_values(
                isin=isin,
                conid=_string_or_none(transfer.get("_conid")),
                symbol=_string_or_none(transfer.get("symbol")),
            )
        currency = str(transfer.get("currency") or "")
        if transfer_dt is None or not instrument_key:
            transfer_rows.append(dict(transfer))
            continue
        event_type = "transfer_out" if is_outgoing_transfer else "transfer_in"
        event_order = 1 if event_type == "transfer_out" else -1
        grouped_events[(instrument_key, isin, currency)].append((transfer_dt, event_order, event_type, transfer))

    for key, key_events in grouped_events.items():
        books = inventory[key]
        current_year: int | None = None
        sorted_events = sorted(key_events, key=lambda item: (item[0], item[1]))
        for event_dt, _, event_type, event in sorted_events:
            if current_year is not None and event_dt.year > current_year:
                for snapshot_year in range(current_year, event_dt.year):
                    _append_position_snapshots(position_rows, books, snapshot_year, fx_provider, warnings, key[0], symbol_history)
            current_year = event_dt.year
            if event_type == "transfer_out":
                transfer_rows.extend(_consume_outgoing_transfer(books, event, event_dt, warnings))
                continue
            if event_type == "transfer_in":
                transfer_rows_for_event, transfer_lots = _consume_incoming_transfer(
                    event,
                    event_dt,
                    transfer_in_resolver,
                    warnings,
                )
                transfer_rows.extend(transfer_rows_for_event)
                for side, lot in transfer_lots:
                    books[side].append(lot)
                continue
            trade = event
            trade_dt = event_dt
            if trade.get("_event_type") == "split":
                _apply_split_to_open_lots(books, _decimal(trade.get("_split_ratio")))
                continue
            quantity = _decimal(trade.get("calculation_quantity") or trade.get("quantity"))
            if quantity == 0:
                continue
            broker_quantity = abs(_decimal(trade.get("quantity")))
            broker_quantity_per_calculation_unit = broker_quantity / abs(quantity) if quantity else Decimal("1")
            price = _decimal(trade.get("price"))
            calculation_price = _decimal(trade.get("calculation_price") or trade.get("price"))
            multiplier = _decimal(trade.get("_calculation_multiplier") or trade.get("multiplier") or "1")
            commission = _decimal(trade.get("commission"))
            commission_per_unit = commission / abs(quantity) if quantity else Decimal("0")

            if quantity > 0:
                remaining = quantity
                matched_any = False
                while remaining > 0 and books["short"]:
                    opening = books["short"][0]
                    matched = min(remaining, opening.quantity)
                    opening_broker_matched = _matched_broker_quantity(opening, matched)
                    exit_broker_matched = matched * broker_quantity_per_calculation_unit
                    if broker_cost_basis_method == "average":
                        _infer_average_cost_unknown_open_lots(
                            books["short"],
                            trade,
                            matched,
                            exit_broker_matched,
                            calculation_price,
                            multiplier,
                            commission_per_unit,
                            "short",
                        )
                    fifo_rows.append(
                        _close_fifo_lot(
                            opening,
                            trade,
                            matched,
                            opening_broker_matched,
                            exit_broker_matched,
                            price,
                            calculation_price,
                            multiplier,
                            commission_per_unit,
                            trade_dt,
                            "short",
                            fx_provider,
                            warnings,
                        )
                    )
                    opening.quantity -= matched
                    opening.broker_quantity -= opening_broker_matched
                    remaining -= matched
                    matched_any = True
                    if opening.quantity == 0:
                        books["short"].popleft()
                if remaining > 0:
                    if _should_close_unknown_prior_lot(trade, matched_any):
                        fifo_rows.append(
                            _close_unknown_opening_lot(
                                trade,
                                remaining,
                                remaining * broker_quantity_per_calculation_unit,
                                price,
                                calculation_price,
                                multiplier,
                                commission_per_unit,
                                trade_dt,
                                "short",
                                fx_provider,
                                warnings,
                            )
                        )
                    else:
                        books["long"].append(
                            _open_lot(
                                trade,
                                remaining,
                                remaining * broker_quantity_per_calculation_unit,
                                price,
                                calculation_price,
                                multiplier,
                                commission_per_unit,
                                trade_dt,
                            )
                        )
            else:
                remaining = abs(quantity)
                matched_any = False
                while remaining > 0 and books["long"]:
                    opening = books["long"][0]
                    matched = min(remaining, opening.quantity)
                    opening_broker_matched = _matched_broker_quantity(opening, matched)
                    exit_broker_matched = matched * broker_quantity_per_calculation_unit
                    if broker_cost_basis_method == "average":
                        _infer_average_cost_unknown_open_lots(
                            books["long"],
                            trade,
                            matched,
                            exit_broker_matched,
                            calculation_price,
                            multiplier,
                            commission_per_unit,
                            "long",
                        )
                    fifo_rows.append(
                        _close_fifo_lot(
                            opening,
                            trade,
                            matched,
                            opening_broker_matched,
                            exit_broker_matched,
                            price,
                            calculation_price,
                            multiplier,
                            commission_per_unit,
                            trade_dt,
                            "long",
                            fx_provider,
                            warnings,
                        )
                    )
                    opening.quantity -= matched
                    opening.broker_quantity -= opening_broker_matched
                    remaining -= matched
                    matched_any = True
                    if opening.quantity == 0:
                        books["long"].popleft()
                if remaining > 0:
                    if _should_close_unknown_prior_lot(trade, matched_any):
                        fifo_rows.append(
                            _close_unknown_opening_lot(
                                trade,
                                remaining,
                                remaining * broker_quantity_per_calculation_unit,
                                price,
                                calculation_price,
                                multiplier,
                                commission_per_unit,
                                trade_dt,
                                "long",
                                fx_provider,
                                warnings,
                            )
                        )
                    else:
                        books["short"].append(
                            _open_lot(
                                trade,
                                remaining,
                                remaining * broker_quantity_per_calculation_unit,
                                price,
                                calculation_price,
                                multiplier,
                                commission_per_unit,
                                trade_dt,
                            )
                        )
        if current_year is not None and max_year is not None:
            for snapshot_year in range(current_year, max_year + 1):
                _append_position_snapshots(position_rows, books, snapshot_year, fx_provider, warnings, key[0], symbol_history)
    return fifo_rows, position_rows, sorted(transfer_rows, key=_transfer_sort_key)


def _is_outgoing_security_transfer(transfer: Mapping[str, Any]) -> bool:
    return (
        transfer.get("transfer_type") == "security"
        and str(transfer.get("direction") or "").lower() == "out"
        and _decimal(transfer.get("_raw_quantity") or transfer.get("quantity")) < 0
    )


def _is_incoming_security_transfer(transfer: Mapping[str, Any]) -> bool:
    return (
        transfer.get("transfer_type") == "security"
        and str(transfer.get("direction") or "").lower() == "in"
        and _decimal(transfer.get("_raw_quantity") or transfer.get("quantity")) != 0
    )


def _transfer_datetime(transfer: Mapping[str, Any], *, end_of_day: bool = True) -> datetime | None:
    transfer_date = _parse_date(transfer.get("date"))
    if transfer_date is None:
        return None
    if end_of_day:
        return datetime(transfer_date.year, transfer_date.month, transfer_date.day, 23, 59, 59)
    return datetime(transfer_date.year, transfer_date.month, transfer_date.day, 0, 0, 0)


def _consume_incoming_transfer(
    transfer: Mapping[str, Any],
    transfer_dt: datetime,
    transfer_in_resolver: TransferInFifoResolver | None,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[tuple[str, FifoOpenLot]]]:
    request = _transfer_in_request(transfer, transfer_dt)
    resolved_lots = list(transfer_in_resolver(request) or []) if transfer_in_resolver is not None else []
    expected_quantity = abs(_decimal(transfer.get("_raw_quantity") or transfer.get("quantity")))
    resolved_quantity = sum((lot.quantity for lot in resolved_lots), Decimal("0"))
    if resolved_lots and abs(resolved_quantity - expected_quantity) <= Decimal("0.0001"):
        rows: list[dict[str, Any]] = []
        lots: list[tuple[str, FifoOpenLot]] = []
        for lot in resolved_lots:
            rows.append(_incoming_transfer_allocation_row(transfer, lot))
            opened_lot = _open_incoming_transfer_lot(transfer, transfer_dt, lot)
            if opened_lot is not None:
                lots.append(opened_lot)
        return rows, lots
    if resolved_lots:
        warning = (
            "Resolved incoming transfer FIFO quantity does not match broker transfer "
            f"for {transfer.get('symbol')} {transfer.get('isin')} on {transfer.get('date')}: "
            f"expected={expected_quantity}, resolved={resolved_quantity}."
        )
        if warning not in warnings:
            warnings.append(warning)
    request_text = request.prompt()
    if request_text not in warnings:
        warnings.append(request_text)
    pending_lot = _open_incoming_transfer_lot(transfer, transfer_dt, None)
    return [dict(transfer)], [pending_lot] if pending_lot is not None else []


def _transfer_in_request(transfer: Mapping[str, Any], transfer_dt: datetime) -> TransferInRequest:
    return TransferInRequest(
        transfer_date=transfer_dt.date(),
        symbol=_string_or_none(transfer.get("symbol")),
        isin=_string_or_none(transfer.get("isin")),
        quantity=abs(_decimal(transfer.get("_raw_quantity") or transfer.get("quantity"))),
        currency=_string_or_none(transfer.get("currency")),
        asset_type=_string_or_none(transfer.get("asset_type")),
        source_report=_string_or_none(transfer.get("source_report")),
        counterparty=_string_or_none(transfer.get("counterparty")),
    )


def _incoming_transfer_allocation_row(
    transfer: Mapping[str, Any],
    lot: TransferInFifoLot,
) -> dict[str, Any]:
    row = dict(transfer)
    row["quantity"] = _decimal_text(lot.quantity)
    row["price"] = _price_text(lot.price)
    row["enter_date"] = lot.enter_date.isoformat(sep=" ") if lot.enter_date else None
    row["amount"] = None
    if lot.source_file:
        source = _string_or_none(row.get("source_report"))
        row["source_report"] = f"{source}; fifo_source:{lot.source_file}" if source else f"fifo_source:{lot.source_file}"
    row["_transfer_cost_basis_status"] = "transfer_in_fifo_source"
    row["_fifo_source_file"] = lot.source_file
    return row


def _open_incoming_transfer_lot(
    transfer: Mapping[str, Any],
    transfer_dt: datetime,
    source_lot: TransferInFifoLot | None = None,
) -> tuple[str, FifoOpenLot] | None:
    raw_quantity = _decimal(transfer.get("_raw_quantity") or transfer.get("quantity"))
    if raw_quantity == 0:
        return None
    quantity = source_lot.quantity if source_lot is not None else abs(raw_quantity)
    multiplier = _decimal(transfer.get("_multiplier") or "1") or Decimal("1")
    price = source_lot.price if source_lot is not None else _decimal(transfer.get("price"))
    converted_ratio = _decimal(transfer.get("_converted_ratio") or "1") or Decimal("1")
    if transfer.get("_converted_isin") or transfer.get("_converted_symbol"):
        quantity = quantity / converted_ratio
        price = price * converted_ratio
    side = "long" if raw_quantity > 0 else "short"
    opening_lot_status = "transfer_in_fifo_source" if source_lot is not None else (
        _string_or_none(transfer.get("_transfer_cost_basis_status")) or "transfer_in"
    )
    base_trade_id = _string_or_none(transfer.get("_transfer_id"))
    trade_id_suffix = f":fifo_source:{source_lot.source_row}" if source_lot and source_lot.source_row is not None else ""
    enter_dt = source_lot.enter_date if source_lot is not None else None
    lot = FifoOpenLot(
        asset_type=str(transfer.get("asset_type") or ""),
        symbol=str(transfer.get("_converted_symbol") or transfer.get("symbol") or ""),
        isin=_string_or_none(transfer.get("_converted_isin") or transfer.get("isin")),
        currency=str(transfer.get("currency") or ""),
        country=_string_or_none(transfer.get("_converted_country") or transfer.get("country")),
        exchange=_string_or_none(transfer.get("_converted_exchange") or transfer.get("exchange")),
        date_time=enter_dt,
        raw_quantity=raw_quantity,
        raw_amount=abs(price * quantity * multiplier),
        raw_commission=Decimal("0"),
        price=price,
        calculation_price=price,
        multiplier=multiplier,
        quantity=quantity,
        broker_quantity=quantity,
        commission_per_unit=Decimal("0"),
        trade_id=f"{base_trade_id}{trade_id_suffix}" if base_trade_id else None,
        opening_lot_status=opening_lot_status,
    )
    return side, lot


def _consume_outgoing_transfer(
    books: Mapping[str, deque[FifoOpenLot]],
    transfer: Mapping[str, Any],
    transfer_dt: datetime,
    warnings: list[str],
) -> list[dict[str, Any]]:
    remaining = abs(_decimal(transfer.get("_raw_quantity") or transfer.get("quantity")))
    rows: list[dict[str, Any]] = []
    while remaining > 0 and books["long"]:
        lot = books["long"][0]
        matched = min(remaining, lot.quantity)
        matched_broker_quantity = _matched_broker_quantity(lot, matched)
        rows.append(_transfer_allocation_row(transfer, lot, matched))
        lot.quantity -= matched
        lot.broker_quantity -= matched_broker_quantity
        remaining -= matched
        if lot.quantity == 0:
            books["long"].popleft()

    if remaining > 0:
        warning = (
            "Outgoing transfer has no sufficient FIFO opening lots "
            f"for {transfer.get('symbol')} on {transfer_dt.date().isoformat()}; unmatched quantity={remaining}."
        )
        if warning not in warnings:
            warnings.append(warning)
        row = dict(transfer)
        row["quantity"] = _decimal_text(remaining)
        row["price"] = None
        rows.append(row)
    return rows


def _transfer_allocation_row(
    transfer: Mapping[str, Any],
    lot: FifoOpenLot,
    quantity: Decimal,
) -> dict[str, Any]:
    row = dict(transfer)
    row["quantity"] = _decimal_text(quantity)
    row["price"] = _price_text(_transfer_fifo_price(lot, quantity))
    row["enter_date"] = lot.date_time.isoformat(sep=" ") if lot.date_time else None
    return row


def _transfer_fifo_price(lot: FifoOpenLot, quantity: Decimal) -> Decimal:
    multiplier = lot.multiplier or Decimal("1")
    denominator = quantity * multiplier
    if denominator == 0:
        return lot.calculation_price
    entry_gross = abs(lot.calculation_price * quantity * multiplier)
    entry_commission = abs(lot.commission_per_unit * quantity)
    return (entry_gross + entry_commission) / denominator


def _transfer_sort_key(transfer: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(transfer.get("date") or ""),
        str(transfer.get("symbol") or ""),
        str(transfer.get("direction") or ""),
        str(transfer.get("quantity") or ""),
    )


def _apply_split_to_open_lots(books: Mapping[str, deque[FifoOpenLot]], ratio: Decimal) -> None:
    if ratio == 0:
        return
    for book_name in ("long", "short"):
        for lot in books[book_name]:
            lot.quantity = lot.quantity / ratio
            lot.calculation_price = lot.calculation_price * ratio


_UNPROCESSED_LOT_STATUSES = {
    "missing_opening_lot",
    "broker_pl_inferred_transfer_in",
    "broker_average_inferred_transfer_in",
}

_UNPROCESSED_DETAILS: dict[str, str] = {
    "missing_opening_lot": "Closing trade has broker realized P/L but no opening lot in discovered raw reports.",
    "broker_pl_inferred_transfer_in": (
        "Opening lot originated from a starting balance or unresolved transfer-in; "
        "cost basis was inferred from broker realized P/L because no raw purchase record exists."
    ),
    "broker_average_inferred_transfer_in": (
        "Opening lot originated from a starting balance or unresolved transfer-in; "
        "cost basis was inferred from broker average-cost P/L and then applied to tax FIFO."
    ),
}


def _build_unprocessed_rows(
    trades: Sequence[Mapping[str, Any]],
    fifo_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    trades_by_id = {_string_or_none(trade.get("trade_id")): trade for trade in trades}
    rows: list[dict[str, Any]] = []
    for fifo_row in fifo_rows:
        lot_status = _string_or_none(fifo_row.get("_opening_lot_status"))
        if lot_status not in _UNPROCESSED_LOT_STATUSES:
            continue
        trade_id = _string_or_none(fifo_row.get("source_trade_id"))
        trade = trades_by_id.get(trade_id, {})
        details = _UNPROCESSED_DETAILS.get(lot_status or "", "Opening lot source is unknown.")
        rows.append(
            {
                "severity": "error",
                "reason": lot_status,
                "details": details,
                "source_sheet": "Trades",
                "source_report": _string_or_none(trade.get("source_report") or fifo_row.get("source_report")),
                "trade_id": trade_id,
                "date_time": _string_or_none(trade.get("date_time") or fifo_row.get("exit_date")),
                "symbol": _string_or_none(trade.get("symbol") or fifo_row.get("symbol")),
                "isin": _string_or_none(trade.get("isin") or fifo_row.get("isin")),
                "asset_type": _string_or_none(trade.get("asset_type") or fifo_row.get("asset_type")),
                "currency": _string_or_none(trade.get("currency") or fifo_row.get("currency")),
                "quantity": _string_or_none(trade.get("quantity") or fifo_row.get("exit_quantity")),
                "price": _string_or_none(trade.get("price") or fifo_row.get("exit_price")),
                "amount": _string_or_none(trade.get("amount") or fifo_row.get("exit_amount")),
                "commission": _string_or_none(trade.get("commission") or fifo_row.get("exit_commission")),
            }
        )
    return rows


def _build_fx_fifo_rows(
    trades: Sequence[Mapping[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        if not _is_fx_trade(trade):
            continue
        trade_dt = _parse_datetime(trade.get("date_time"))
        if trade_dt is None:
            continue
        pnl = _decimal(trade.get("_broker_realized_pl"))
        commission = _decimal(trade.get("commission"))
        pnl_after_commission = pnl - commission
        rate = _annual_rate(fx_provider, trade_dt.year, _string_or_none(trade.get("currency")), warnings)
        quantity = abs(_decimal(trade.get("quantity")))
        exit_amount = _decimal(trade.get("amount"))
        rows.append(
            {
                "asset_type": _string_or_none(trade.get("asset_type")),
                "symbol": _string_or_none(trade.get("symbol")),
                "isin": None,
                "currency": _string_or_none(trade.get("currency")),
                "country": _string_or_none(trade.get("country")),
                "position_type": "fx",
                "_opening_lot_status": "matched",
                "enter_date": None,
                "enter_quantity": None,
                "enter_price": None,
                "enter_multiplier": None,
                "enter_amount": None,
                "enter_commission": None,
                "exit_date": trade_dt.isoformat(sep=" "),
                "exit_quantity": _decimal_text(quantity),
                "exit_price": _decimal_text(_decimal(trade.get("price"))),
                "exit_multiplier": _multiplier_text(_decimal(trade.get("multiplier") or "1")),
                "exit_amount": _decimal_text(exit_amount),
                "exit_commission": _decimal_text(commission),
                "acquisition_cost_with_commission": None,
                "pnl_before_commission": str(pnl),
                "pnl_after_all_commissions": str(pnl_after_commission),
                "pnl": str(pnl_after_commission),
                "kzt_rate": str(rate) if rate is not None else None,
                "exit_amount_kzt": _amount_kzt(exit_amount, rate),
                "acquisition_cost_with_commission_kzt": None,
                "pnl_before_commission_kzt": _amount_kzt(pnl, rate),
                "pnl_after_all_commissions_kzt": _amount_kzt(pnl_after_commission, rate),
                "pnl_kzt": _amount_kzt(pnl_after_commission, rate),
                "source_trade_id": _string_or_none(trade.get("trade_id")),
                "entry_trade_id": None,
            }
        )
    return rows


def _open_lot(
    trade: Mapping[str, Any],
    quantity: Decimal,
    broker_quantity: Decimal,
    price: Decimal,
    calculation_price: Decimal,
    multiplier: Decimal,
    commission_per_unit: Decimal,
    trade_dt: datetime,
) -> FifoOpenLot:
    return FifoOpenLot(
        asset_type=str(trade.get("asset_type") or ""),
        symbol=str(trade.get("symbol") or ""),
        isin=_string_or_none(trade.get("isin")),
        currency=str(trade.get("currency") or ""),
        country=_string_or_none(trade.get("country")),
        exchange=_string_or_none(trade.get("exchange")),
        date_time=trade_dt,
        raw_quantity=_decimal(trade.get("quantity")),
        raw_amount=_decimal(trade.get("amount")),
        raw_commission=_decimal(trade.get("commission")),
        price=price,
        calculation_price=calculation_price,
        multiplier=multiplier,
        quantity=quantity,
        broker_quantity=broker_quantity,
        commission_per_unit=commission_per_unit,
        trade_id=_string_or_none(trade.get("trade_id")),
    )


def _matched_broker_quantity(opening: FifoOpenLot, matched_quantity: Decimal) -> Decimal:
    if opening.quantity == 0:
        return matched_quantity
    return matched_quantity * opening.broker_quantity / opening.quantity


def _broker_realized_pl(trade: Mapping[str, Any]) -> Decimal:
    return _decimal(trade.get("_broker_realized_pl"))


def _broker_realized_pl_includes_commissions(trade: Mapping[str, Any]) -> bool:
    return trade.get("_broker_realized_pl_includes_commissions") is not False


def _opens_position(trade: Mapping[str, Any]) -> bool:
    code = _string_or_none(trade.get("_broker_code"))
    if not code:
        return False
    return "O" in {part.strip().upper() for part in code.split(";") if part.strip()}


def _closes_position(trade: Mapping[str, Any]) -> bool:
    code = _string_or_none(trade.get("_broker_code"))
    if not code:
        return _broker_realized_pl(trade) != 0
    return "C" in {part.strip().upper() for part in code.split(";") if part.strip()}


def _should_close_unknown_prior_lot(trade: Mapping[str, Any], matched_any: bool) -> bool:
    if _broker_realized_pl(trade) == 0:
        return False
    if _opens_position(trade):
        return False
    return matched_any or _closes_position(trade)


def _append_position_snapshots(
    rows: list[dict[str, Any]],
    books: Mapping[str, deque[FifoOpenLot]],
    snapshot_year: int,
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
    identity_key: str | None = None,
    symbol_history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> None:
    snapshot_date = date(snapshot_year, 12, 31)
    display_symbol = _instrument_symbol_for_year(symbol_history or {}, identity_key, snapshot_year)
    for position_type, sign in (("long", Decimal("1")), ("short", Decimal("-1"))):
        for lot in books[position_type]:
            if abs(lot.quantity) <= Decimal("0.00000001"):
                continue
            quantity = lot.quantity * sign
            has_pending_transfer_cost_basis = lot.opening_lot_status == "pending_transfer_out_fifo_cost_basis"
            amount = None if has_pending_transfer_cost_basis else lot.calculation_price * quantity * lot.multiplier
            entry_commission_remaining = None if has_pending_transfer_cost_basis else abs(lot.commission_per_unit * lot.quantity)
            acquisition_cost = (
                None
                if has_pending_transfer_cost_basis
                else abs(lot.calculation_price * lot.quantity * lot.multiplier) + entry_commission_remaining
            )
            rate = _annual_rate(fx_provider, snapshot_year, lot.currency, warnings)
            rows.append(
                {
                    "year": snapshot_year,
                    "date": snapshot_date.isoformat(),
                    "asset_type": lot.asset_type,
                    "symbol": display_symbol or lot.symbol,
                    "isin": lot.isin,
                    "currency": lot.currency,
                    "country": lot.country,
                    "quantity": str(quantity),
                    "price": None if has_pending_transfer_cost_basis else _decimal_text(lot.calculation_price),
                    "multiplier": _multiplier_text(lot.multiplier),
                    "amount": None if amount is None else str(amount),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "amount_kzt": None if amount is None else _amount_kzt(amount, rate),
                    "position_type": position_type,
                    "enter_date": lot.date_time.isoformat(sep=" ") if lot.date_time else None,
                    "entry_trade_id": lot.trade_id,
                    "entry_commission_remaining": None if entry_commission_remaining is None else str(entry_commission_remaining),
                    "acquisition_cost_with_commission": None if acquisition_cost is None else str(acquisition_cost),
                    "valuation_basis": "pending_transfer_out_fifo_cost_basis" if has_pending_transfer_cost_basis else "fifo_lot_cost",
                }
            )


def _infer_average_cost_unknown_open_lots(
    lots: deque[FifoOpenLot],
    exit_trade: Mapping[str, Any],
    quantity: Decimal,
    exit_broker_quantity: Decimal,
    exit_calculation_price: Decimal,
    exit_multiplier: Decimal,
    exit_commission_per_unit: Decimal,
    position_type: str,
) -> None:
    if not lots or _broker_realized_pl(exit_trade) == 0:
        return
    unknown_lots = [
        lot
        for lot in lots
        if lot.quantity > 0 and lot.opening_lot_status == "pending_transfer_out_fifo_cost_basis"
    ]
    if not unknown_lots:
        return
    known_lots = [
        lot
        for lot in lots
        if lot.quantity > 0 and lot.opening_lot_status != "pending_transfer_out_fifo_cost_basis"
    ]
    if not known_lots:
        return

    implied_sold_cost = _broker_implied_acquisition_cost(
        exit_trade,
        quantity,
        exit_broker_quantity,
        exit_calculation_price,
        exit_multiplier,
        exit_commission_per_unit,
        position_type,
    )
    broker_average_price = _implied_enter_price(implied_sold_cost, quantity, exit_multiplier)
    pool_denominator = sum((lot.quantity * (lot.multiplier or Decimal("1")) for lot in lots if lot.quantity > 0), Decimal("0"))
    unknown_denominator = sum((lot.quantity * (lot.multiplier or Decimal("1")) for lot in unknown_lots), Decimal("0"))
    if pool_denominator == 0 or unknown_denominator == 0:
        return
    known_cost = sum(
        (lot.calculation_price * lot.quantity * (lot.multiplier or Decimal("1")) for lot in known_lots),
        Decimal("0"),
    )
    unknown_cost = broker_average_price * pool_denominator - known_cost
    unknown_price = unknown_cost / unknown_denominator
    if unknown_price <= 0:
        return

    for lot in unknown_lots:
        multiplier = lot.multiplier or Decimal("1")
        lot.price = unknown_price
        lot.calculation_price = unknown_price
        lot.raw_amount = abs(unknown_price * lot.quantity * multiplier)
        lot.opening_lot_status = "broker_average_inferred_transfer_in"


def _broker_implied_acquisition_cost(
    exit_trade: Mapping[str, Any],
    quantity: Decimal,
    exit_broker_quantity: Decimal,
    exit_calculation_price: Decimal,
    exit_multiplier: Decimal,
    exit_commission_per_unit: Decimal,
    position_type: str,
) -> Decimal:
    broker_pnl = _allocated_broker_realized_pl(exit_trade, exit_broker_quantity)
    broker_pnl_includes_commissions = _broker_realized_pl_includes_commissions(exit_trade)
    broker_basis = _allocated_broker_basis(exit_trade, exit_broker_quantity)
    exit_gross = exit_calculation_price * quantity * exit_multiplier
    exit_commission = abs(exit_commission_per_unit * quantity)
    if broker_basis is not None and broker_basis != 0:
        return broker_basis
    if position_type == "short":
        if broker_pnl_includes_commissions:
            return exit_gross + exit_commission + broker_pnl
        return exit_gross + broker_pnl
    if broker_pnl_includes_commissions:
        return exit_gross - exit_commission - broker_pnl
    return exit_gross - broker_pnl


def _close_unknown_opening_lot(
    exit_trade: Mapping[str, Any],
    quantity: Decimal,
    exit_broker_quantity: Decimal,
    exit_price: Decimal,
    exit_calculation_price: Decimal,
    exit_multiplier: Decimal,
    exit_commission_per_unit: Decimal,
    exit_dt: datetime,
    position_type: str,
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
    *,
    enter_dt: datetime | None = None,
    opening_status: str = "missing_opening_lot",
    entry_trade_id: str | None = None,
) -> dict[str, Any]:
    broker_pnl = _allocated_broker_realized_pl(exit_trade, exit_broker_quantity)
    broker_pnl_includes_commissions = _broker_realized_pl_includes_commissions(exit_trade)
    exit_gross = exit_calculation_price * quantity * exit_multiplier
    exit_commission = abs(exit_commission_per_unit * quantity)
    implied_acquisition_cost = _broker_implied_acquisition_cost(
        exit_trade,
        quantity,
        exit_broker_quantity,
        exit_calculation_price,
        exit_multiplier,
        exit_commission_per_unit,
        position_type,
    )
    broker_pnl_after_all_commissions = broker_pnl if broker_pnl_includes_commissions else broker_pnl - exit_commission
    implied_enter_price = _implied_enter_price(implied_acquisition_cost, quantity, exit_multiplier)
    tax_pnl = broker_pnl_after_all_commissions + exit_commission
    rate = _annual_rate(fx_provider, exit_dt.year, _string_or_none(exit_trade.get("currency")), warnings)
    if opening_status == "missing_opening_lot":
        warning = (
            "Missing opening lot for closing trade "
            f"{exit_trade.get('trade_id')} ({exit_trade.get('symbol')}); broker realized P/L used for FIFO audit row."
        )
    else:
        warning = (
            "Transfer-in cost basis is unresolved for closing trade "
            f"{exit_trade.get('trade_id')} ({exit_trade.get('symbol')}); broker realized P/L used for FIFO audit row."
        )
    if warning not in warnings:
        warnings.append(warning)
    inferred_enter_dt = enter_dt or _parse_datetime(exit_trade.get("_missing_opening_enter_date"))
    return {
        "asset_type": _string_or_none(exit_trade.get("asset_type")),
        "symbol": _string_or_none(exit_trade.get("symbol")),
        "isin": _string_or_none(exit_trade.get("isin")),
        "currency": _string_or_none(exit_trade.get("currency")),
        "country": _string_or_none(exit_trade.get("country")),
        "exchange": _string_or_none(exit_trade.get("exchange")),
        "position_type": position_type,
        "_opening_lot_status": opening_status,
        "enter_date": inferred_enter_dt.isoformat(sep=" ") if inferred_enter_dt else None,
        "enter_quantity": _decimal_text(quantity),
        "enter_price": _price_text(implied_enter_price),
        "enter_multiplier": _multiplier_text(exit_multiplier),
        "enter_amount": str(implied_acquisition_cost),
        "enter_commission": None,
        "exit_date": exit_dt.isoformat(sep=" "),
        "exit_quantity": _decimal_text(quantity),
        "exit_price": _decimal_text(exit_calculation_price),
        "exit_multiplier": _multiplier_text(exit_multiplier),
        "exit_amount": str(exit_gross),
        "exit_commission": str(exit_commission),
        "acquisition_cost_with_commission": str(implied_acquisition_cost),
        "pnl_before_commission": None,
        "pnl_after_all_commissions": str(broker_pnl_after_all_commissions),
        "pnl": str(tax_pnl),
        "kzt_rate": str(rate) if rate is not None else None,
        "exit_amount_kzt": _amount_kzt(exit_gross, rate),
        "acquisition_cost_with_commission_kzt": _amount_kzt(implied_acquisition_cost, rate),
        "pnl_before_commission_kzt": None,
        "pnl_after_all_commissions_kzt": _amount_kzt(broker_pnl_after_all_commissions, rate),
        "pnl_kzt": _amount_kzt(tax_pnl, rate),
        "source_trade_id": _string_or_none(exit_trade.get("trade_id")),
        "entry_trade_id": entry_trade_id,
        "corporate_action_type": _string_or_none(exit_trade.get("_corporate_action_type")),
    }


def _implied_enter_price(acquisition_cost: Decimal, quantity: Decimal, multiplier: Decimal) -> Decimal:
    denominator = quantity * (multiplier or Decimal("1"))
    if denominator == 0:
        return Decimal("0")
    return acquisition_cost / denominator


def _allocated_broker_realized_pl(exit_trade: Mapping[str, Any], exit_broker_quantity: Decimal) -> Decimal:
    broker_pnl = _broker_realized_pl(exit_trade)
    total_broker_quantity = abs(_decimal(exit_trade.get("quantity")))
    if total_broker_quantity == 0 or exit_broker_quantity == 0:
        return broker_pnl
    if exit_broker_quantity == total_broker_quantity:
        return broker_pnl
    return broker_pnl * abs(exit_broker_quantity) / total_broker_quantity


def _allocated_broker_basis(exit_trade: Mapping[str, Any], exit_broker_quantity: Decimal) -> Decimal | None:
    if exit_trade.get("_broker_basis") in (None, ""):
        return None
    broker_basis = abs(_decimal(exit_trade.get("_broker_basis")))
    total_broker_quantity = abs(_decimal(exit_trade.get("quantity")))
    if total_broker_quantity == 0 or exit_broker_quantity == 0:
        return broker_basis
    if exit_broker_quantity == total_broker_quantity:
        return broker_basis
    return broker_basis * abs(exit_broker_quantity) / total_broker_quantity


def _close_fifo_lot(
    opening: FifoOpenLot,
    exit_trade: Mapping[str, Any],
    quantity: Decimal,
    entry_broker_quantity: Decimal,
    exit_broker_quantity: Decimal,
    exit_price: Decimal,
    exit_calculation_price: Decimal,
    exit_multiplier: Decimal,
    exit_commission_per_unit: Decimal,
    exit_dt: datetime,
    position_type: str,
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> dict[str, Any]:
    if opening.opening_lot_status == "missing_opening_lot":
        return _close_unknown_opening_lot(
            exit_trade,
            quantity,
            exit_broker_quantity,
            exit_price,
            exit_calculation_price,
            exit_multiplier,
            exit_commission_per_unit,
            exit_dt,
            position_type,
            fx_provider,
            warnings,
            enter_dt=opening.date_time,
            entry_trade_id=opening.trade_id,
        )
    if opening.opening_lot_status == "pending_transfer_out_fifo_cost_basis" and _broker_realized_pl(exit_trade) != 0:
        return _close_unknown_opening_lot(
            exit_trade,
            quantity,
            exit_broker_quantity,
            exit_price,
            exit_calculation_price,
            exit_multiplier,
            exit_commission_per_unit,
            exit_dt,
            position_type,
            fx_provider,
            warnings,
            enter_dt=opening.date_time,
            opening_status="broker_pl_inferred_transfer_in",
            entry_trade_id=opening.trade_id,
        )
    entry_gross = opening.calculation_price * quantity * opening.multiplier
    exit_gross = exit_calculation_price * quantity * exit_multiplier
    entry_commission = abs(opening.commission_per_unit * quantity)
    exit_commission = abs(exit_commission_per_unit * quantity)
    allocated_commission = entry_commission + exit_commission
    is_derivative = _is_derivative_record(
        {
            "asset_type": opening.asset_type,
            "symbol": _string_or_none(exit_trade.get("symbol")) or opening.symbol,
        }
    )
    if position_type == "long":
        pnl_before_commission = exit_gross - entry_gross
        acquisition_cost_with_commission = entry_gross if is_derivative else entry_gross + entry_commission
        pnl = exit_gross - acquisition_cost_with_commission
    else:
        pnl_before_commission = entry_gross - exit_gross
        acquisition_cost_with_commission = entry_gross - entry_commission
        pnl = pnl_before_commission - entry_commission
    pnl_after_all_commissions = pnl_before_commission - allocated_commission
    rate = _annual_rate(fx_provider, exit_dt.year, opening.currency, warnings)
    display_symbol = _string_or_none(exit_trade.get("symbol")) or opening.symbol
    opening_lot_status = (
        opening.opening_lot_status
        if opening.opening_lot_status == "broker_average_inferred_transfer_in"
        else "matched"
    )
    return {
        "asset_type": opening.asset_type,
        "symbol": display_symbol,
        "isin": opening.isin,
        "currency": opening.currency,
        "country": opening.country,
        "exchange": _string_or_none(exit_trade.get("exchange")) or opening.exchange,
        "position_type": position_type,
        "_opening_lot_status": opening_lot_status,
        "enter_date": opening.date_time.isoformat(sep=" ") if opening.date_time else None,
        "enter_quantity": _decimal_text(quantity),
        "enter_price": _decimal_text(opening.calculation_price),
        "enter_multiplier": _multiplier_text(opening.multiplier),
        "enter_amount": _decimal_text(entry_gross),
        "enter_commission": str(entry_commission),
        "exit_date": exit_dt.isoformat(sep=" "),
        "exit_quantity": _decimal_text(quantity),
        "exit_price": _decimal_text(exit_calculation_price),
        "exit_multiplier": _multiplier_text(exit_multiplier),
        "exit_amount": str(exit_gross),
        "exit_commission": str(exit_commission),
        "acquisition_cost_with_commission": str(acquisition_cost_with_commission),
        "pnl_before_commission": str(pnl_before_commission),
        "pnl_after_all_commissions": str(pnl_after_all_commissions),
        "pnl": str(pnl),
        "kzt_rate": str(rate) if rate is not None else None,
        "exit_amount_kzt": _amount_kzt(exit_gross, rate),
        "acquisition_cost_with_commission_kzt": _amount_kzt(acquisition_cost_with_commission, rate),
        "pnl_before_commission_kzt": _amount_kzt(pnl_before_commission, rate),
        "pnl_after_all_commissions_kzt": _amount_kzt(pnl_after_all_commissions, rate),
        "pnl_kzt": _amount_kzt(pnl, rate),
        "source_trade_id": _string_or_none(exit_trade.get("trade_id")),
        "entry_trade_id": opening.trade_id,
        "corporate_action_type": _string_or_none(exit_trade.get("_corporate_action_type")),
    }


def _build_positions(
    reports: Sequence[ParsedIbReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        year = _year_for_report(report)
        for row in report.rows.get(IB_SECTION_POSITIONS, []):
            if row.get("DataDiscriminator") and row.get("DataDiscriminator") != "Summary":
                continue
            symbol = _string_or_none(row.get("Symbol"))
            currency = _string_or_none(row.get("Currency"))
            instrument = _lookup_instrument(instrument_lookup, symbol, year) or {}
            quantity = _decimal(row.get("Quantity"))
            price = _decimal(row.get("Close Price"))
            value = _decimal(row.get("Value"))
            rate = _annual_rate(fx_provider, year, currency, warnings)
            rows.append(
                {
                    "year": year,
                    "date": _date_to_iso(report.period_end),
                    "asset_type": _string_or_none(row.get("Asset Category")) or instrument.get("asset_type"),
                    "symbol": symbol,
                    "isin": instrument.get("isin"),
                    "currency": currency,
                    "country": instrument.get("country"),
                    "exchange": instrument.get("listing_exchange"),
                    "quantity": str(quantity),
                    "price": str(price),
                    "amount": str(value),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "amount_kzt": _amount_kzt(value, rate),
                    "source_report": str(report.path),
                }
            )
    return rows


def _build_interest_and_coupons(
    reports: Sequence[ParsedIbReport],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    interest_rows: list[dict[str, Any]] = []
    coupon_rows: list[dict[str, Any]] = []
    for report in reports:
        for row in report.rows.get(IB_SECTION_INTEREST, []):
            if _is_total_amount_row(row):
                continue
            event_date = _parse_date(row.get("Date"))
            year = event_date.year if event_date else _year_for_report(report)
            currency = _string_or_none(row.get("Currency"))
            amount = _decimal(row.get("Amount"))
            description = _string_or_none(row.get("Description"))
            symbol, isin = _infer_interest_symbol_isin(description, year, instrument_lookup)
            rate = _annual_rate(fx_provider, year, currency, warnings)
            base_record = {
                "date": _date_to_iso(event_date),
                "description": description,
                "currency": currency,
                "gross_amount": str(amount),
                "withholding_tax": "0",
                "net_amount": str(amount),
                "kzt_rate": str(rate) if rate is not None else None,
                "gross_amount_kzt": _amount_kzt(amount, rate),
                "withholding_tax_kzt": _amount_kzt(Decimal("0"), rate),
                "net_amount_kzt": _amount_kzt(amount, rate),
                "source_report": str(report.path),
            }
            if isin:
                coupon_rows.append(
                    {
                        "date": base_record["date"],
                        "symbol": symbol,
                        "isin": isin,
                        "country": isin[:2].replace("XS", "BE") if len(isin) >= 2 else None,
                        "currency": currency,
                        "gross_amount": str(amount),
                        "withholding_tax": "0",
                        "net_amount": str(amount),
                        "kzt_rate": base_record["kzt_rate"],
                        "gross_amount_kzt": base_record["gross_amount_kzt"],
                        "withholding_tax_kzt": base_record["withholding_tax_kzt"],
                        "net_amount_kzt": base_record["net_amount_kzt"],
                        "is_revert": _is_explicit_income_revert(description),
                        "offshore_flag": None,
                        "source_report": str(report.path),
                        "description": description,
                    }
                )
            else:
                interest_rows.append(base_record)
    return interest_rows, coupon_rows


def _infer_interest_symbol_isin(
    description: str | None,
    year: int | None,
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> tuple[str | None, str | None]:
    if not description:
        return None, None
    isin_match = ISIN_RE.search(description)
    if isin_match:
        return None, isin_match.group(1)
    for (candidate, candidate_year), instrument in instrument_lookup.items():
        if candidate_year not in {year, None}:
            continue
        if candidate and len(candidate) > 3 and candidate in description:
            isin = instrument.get("isin")
            if isin and ISIN_RE.fullmatch(str(isin)):
                return str(instrument.get("symbol") or candidate), str(isin)
    return None, None


def _is_explicit_income_revert(description: str | None) -> bool:
    """Recognize a reversal only when the raw broker text says it is one."""

    text = str(description or "").strip().casefold()
    return any(marker in text for marker in ("reverted", "reversal", "reversed", "storno", "сторно", "отмена"))


def _build_cash_balances(
    reports: Sequence[ParsedIbReport],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        year = _year_for_report(report)
        for row in report.rows.get(IB_SECTION_CASH, []):
            if row.get("Currency Summary") != "Ending Cash":
                continue
            currency = _string_or_none(row.get("Currency"))
            if not currency or currency == "Base Currency Summary":
                continue
            ending_cash = _decimal(row.get("Total"))
            rate = _annual_rate(fx_provider, year, currency, warnings)
            rows.append(
                {
                    "year": year,
                    "date": _date_to_iso(report.period_end),
                    "currency": currency,
                    "ending_cash": str(ending_cash),
                    "ending_cash_kzt": _amount_kzt(ending_cash, rate),
                    "source_report": str(report.path),
                }
            )
    return rows


def _apply_broker_country_to_forex_trades(trades: Sequence[Mapping[str, Any]], broker_code: str) -> None:
    country = _broker_registration_country(broker_code)
    if not country:
        return
    for trade in trades:
        if _is_broker_country_forex_trade(trade) and isinstance(trade, dict):
            trade["country"] = country


def _broker_registration_country(broker_code: str) -> str | None:
    normalized = str(broker_code or "").strip().lower()
    normalized = {
        "freedom_broker": "freedom",
        "freedom_bank": "freedom",
    }.get(normalized, normalized)
    info = DEFAULT_BROKER_BANK_INFO.get(normalized)
    return _string_or_none(info.get("country")) if info else None


def _is_broker_country_forex_trade(record: Mapping[str, Any]) -> bool:
    asset_type = str(record.get("asset_type") or record.get("Asset_Type") or "").strip().lower()
    symbol = str(record.get("symbol") or record.get("Symbol") or "").strip().upper()
    return asset_type in {"forex", "fx spot", "fx_spot", "fx-spot", "currency"} or "FOREX" in asset_type or ".FX" in symbol


def _build_years_results(
    dataset: CanonicalDataset,
    *,
    aix_provider: AixInstrumentProvider | None = None,
    dividend_provider: KaseAixDividendProvider | None = None,
    offshore_provider: OffshoreJurisdictionProvider | None = None,
) -> list[dict[str, Any]]:
    aix_provider = aix_provider or AixInstrumentProvider.from_xlsx()
    dividend_provider = dividend_provider or KaseAixDividendProvider.from_xlsx()
    offshore_provider = offshore_provider or OffshoreJurisdictionProvider.from_xlsx()
    instrument_flags = _instrument_tax_flags(dataset)
    rows: list[dict[str, Any]] = []
    pnl_groups: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
        lambda: {
            "pnl": Decimal("0"),
            "pnl_kzt": Decimal("0"),
            "only_profit": Decimal("0"),
            "only_profit_kzt": Decimal("0"),
            "withhold_kzt": Decimal("0"),
            "foreign_tax_credit_kzt": Decimal("0"),
            "taxable_proceeds_kzt": Decimal("0"),
        }
    )
    amount_groups: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
        lambda: {
            "amount": Decimal("0"),
            "amount_kzt": Decimal("0"),
            "only_profit": Decimal("0"),
            "only_profit_kzt": Decimal("0"),
            "withhold_kzt": Decimal("0"),
            "foreign_tax_credit_kzt": Decimal("0"),
        }
    )

    for record in dataset.tables.get("Fifo", []):
        year = _record_year(record, "exit_date")
        flags = _record_tax_flags(
            record,
            instrument_flags,
            aix_provider=aix_provider,
            offshore_provider=offshore_provider,
        )
        if _is_derivative_record(record):
            table_name = "Yearly Derivatives"
        elif _is_fx_trade(record):
            flags = _issuer_outside_kz_flags()
            table_name = "Yearly FX Trades"
        else:
            pnl_kzt = _decimal(record.get("pnl_kzt"))
            table_name = (
                "Yearly Bonds Redemption"
                if _is_bond_redemption_fifo_row(record) and pnl_kzt > 0
                else "Yearly Trades"
            )
        key = _years_result_key(table_name, year, record, flags)
        values = pnl_groups[key]
        pnl = _decimal(record.get("pnl"))
        pnl_kzt = _decimal(record.get("pnl_kzt"))
        tax_pnl = _derivative_tax_pnl(record)
        tax_pnl_kzt = _derivative_tax_pnl_kzt(record)
        values["pnl"] += pnl
        values["pnl_kzt"] += pnl_kzt
        if table_name == "Yearly Derivatives" and tax_pnl_kzt > 0:
            values["only_profit"] += tax_pnl
            values["only_profit_kzt"] += tax_pnl_kzt
        if table_name == "Yearly Trades" and _tax_source_flag(flags) == FLAG_OFFSHORE:
            values["taxable_proceeds_kzt"] += _taxable_exit_proceeds_kzt(record)

    for record in dataset.tables.get("_TradeWithholdingTax", []):
        year = _record_year(record, "date")
        flags = _record_tax_flags(
            record,
            instrument_flags,
            aix_provider=aix_provider,
            offshore_provider=offshore_provider,
        )
        if flags.get("issuer_outside_kz_flag") is None:
            flags = _issuer_outside_kz_flags()
        key = _years_result_key("Yearly Trades", year, record, flags)
        values = pnl_groups[key]
        withholding_kzt = _decimal(record.get("withholding_tax_kzt"))
        values["withhold_kzt"] += withholding_kzt
        values["foreign_tax_credit_kzt"] += withholding_kzt

    for key, values in _build_dividend_year_groups(
        dataset.tables.get("Dividends", []),
        instrument_flags,
        aix_provider=aix_provider,
        dividend_provider=dividend_provider,
        offshore_provider=offshore_provider,
    ).items():
        amount_groups[key]["amount"] += values["amount"]
        amount_groups[key]["amount_kzt"] += values["amount_kzt"]
        amount_groups[key]["withhold_kzt"] += values["withhold_kzt"]
        amount_groups[key]["foreign_tax_credit_kzt"] += values["foreign_tax_credit_kzt"]

    for sheet_name, table_name in (("Interest", "Yearly Interest"), ("Coupons", "Yearly Coupons")):
        for record in dataset.tables.get(sheet_name, []):
            year = _record_year(record, "date")
            flags = (
                _issuer_outside_kz_flags()
                if sheet_name == "Interest"
                else _record_tax_flags(
                    record,
                    instrument_flags,
                    aix_provider=aix_provider,
                    offshore_provider=offshore_provider,
                )
            )
            amount = _decimal(record.get("gross_amount"))
            amount_kzt = _decimal(record.get("gross_amount_kzt"))
            if sheet_name == "Interest" and _is_swap_interest_record(record):
                key = _years_result_key("Yearly Derivatives", year, record, flags)
                values = pnl_groups[key]
                values["pnl"] += amount
                values["pnl_kzt"] += amount_kzt
                if amount_kzt > 0:
                    values["only_profit"] += amount
                    values["only_profit_kzt"] += amount_kzt
                continue
            key = _years_result_key(table_name, year, record, flags)
            values = amount_groups[key]
            values["amount"] += amount
            values["amount_kzt"] += amount_kzt
            if sheet_name == "Interest" and amount > 0:
                values["only_profit"] += amount
                values["only_profit_kzt"] += amount_kzt
            elif sheet_name == "Coupons" and not _is_kz_coupon(record, flags) and (
                (amount > 0 and not bool(record.get("is_revert")))
                or (amount < 0 and bool(record.get("is_revert")))
            ):
                # Negative accrued coupon interest (NKD) remains in Amount, but
                # does not reduce remuneration.  An explicit broker reversal
                # does reduce the positive-coupon tax base.  Coupons from KZ
                # securities are informative Amount values only and therefore
                # never enter OnlyProfit or form 270.00.
                values["only_profit"] += amount
                values["only_profit_kzt"] += amount_kzt
            withholding_kzt = _decimal(record.get("withholding_tax_kzt"))
            values["withhold_kzt"] += withholding_kzt
            values["foreign_tax_credit_kzt"] += withholding_kzt

    for key in sorted(pnl_groups, key=_years_result_sort_key):
        table_name, year, flag, country, tax_exchange, currency = key
        values = pnl_groups[key]
        pnl = values["pnl"]
        pnl_kzt = values["pnl_kzt"]
        only_profit = values["only_profit"]
        only_profit_kzt = values["only_profit_kzt"]
        withhold_kzt = values["withhold_kzt"]
        foreign_tax_credit_kzt = values["foreign_tax_credit_kzt"]
        display_pnl_kzt = values["taxable_proceeds_kzt"] if table_name == "Yearly Trades" and flag == FLAG_OFFSHORE else pnl_kzt
        taxable_pnl_kzt = max(display_pnl_kzt, Decimal("0"))
        exempt = table_name in {"Yearly Bonds Redemption", "Yearly FX Trades"} or (
            table_name == "Yearly Trades" and flag == FLAG_PREFERENTIAL
        )
        if exempt:
            tax_kzt = Decimal("0")
        elif table_name == "Yearly Trades" and flag == FLAG_OFFSHORE:
            tax_kzt = values["taxable_proceeds_kzt"] * Decimal("0.10")
        elif table_name == "Yearly Derivatives":
            tax_kzt = max(only_profit_kzt, Decimal("0")) * Decimal("0.10")
        else:
            tax_kzt = taxable_pnl_kzt * Decimal("0.10")
        tax_kzt_withhold = Decimal("0") if exempt else max(tax_kzt + foreign_tax_credit_kzt, Decimal("0"))
        rows.append(
            {
                "table": table_name,
                "year": year,
                "flag": flag,
                "country": country,
                "tax_exchange": tax_exchange,
                "currency": currency,
                "pnl": _money_text(pnl),
                "pnl_kzt": _money_text(display_pnl_kzt),
                "only_profit": _money_text(only_profit),
                "only_profit_kzt": _money_text(only_profit_kzt),
                "withhold_kzt": _money_text(withhold_kzt),
                "tax_kzt": _money_text(tax_kzt),
                "tax_kzt_withhold": _money_text(tax_kzt_withhold),
            }
        )

    for key in sorted(amount_groups, key=_years_result_sort_key):
        table_name, year, flag, country, tax_exchange, currency = key
        values = amount_groups[key]
        amount = values["amount"]
        amount_kzt = values["amount_kzt"]
        only_profit = values["only_profit"]
        only_profit_kzt = values["only_profit_kzt"]
        withhold_kzt = values["withhold_kzt"]
        foreign_tax_credit_kzt = values["foreign_tax_credit_kzt"]
        if table_name == "Yearly Interest":
            tax_kzt = max(only_profit_kzt, Decimal("0")) * Decimal("0.10")
            rows.append(
                {
                    "table": table_name,
                    "year": year,
                    "flag": flag,
                    "country": country,
                    "tax_exchange": tax_exchange,
                    "currency": currency,
                    "amount": _money_text(amount),
                    "amount_kzt": _money_text(amount_kzt),
                    "only_profit": _money_text(only_profit),
                    "only_profit_kzt": _money_text(only_profit_kzt),
                    "tax_kzt": _money_text(tax_kzt),
                }
            )
            continue
        is_preferential_unreported_income = table_name == "Yearly Dividends" and flag == FLAG_PREFERENTIAL
        is_exchange_preferential_dividend = table_name == "Yearly Dividends" and flag in {
            FLAG_PREFERENTIAL_AIX,
            FLAG_PREFERENTIAL_KASE,
        }
        is_coupon = table_name == "Yearly Coupons"
        displayed_amount_kzt = Decimal("0") if is_preferential_unreported_income else amount_kzt
        displayed_withhold_kzt = Decimal("0") if is_preferential_unreported_income else withhold_kzt
        taxable_amount_kzt = max(only_profit_kzt if is_coupon else amount_kzt, Decimal("0"))
        if is_coupon:
            # All bond coupon remuneration is deducted in application 01.E.1
            # (article 341), so it must not produce a Kazakhstan tax or a
            # foreign-tax credit in the annual result block.
            tax_kzt = Decimal("0")
            tax_kzt_withhold = Decimal("0")
        else:
            tax_kzt = (
                Decimal("0")
                if is_preferential_unreported_income or is_exchange_preferential_dividend
                else taxable_amount_kzt * Decimal("0.10")
            )
            tax_kzt_withhold = (
                Decimal("0")
                if taxable_amount_kzt <= 0 or is_preferential_unreported_income or is_exchange_preferential_dividend
                else max(tax_kzt + foreign_tax_credit_kzt, Decimal("0"))
            )
        row = {
            "table": table_name,
            "year": year,
            "flag": flag,
            "country": country,
            "tax_exchange": tax_exchange,
            "currency": currency,
            "amount": _money_text(amount),
            "amount_kzt": _money_text(displayed_amount_kzt),
            "withhold_kzt": _money_text(displayed_withhold_kzt),
            "tax_kzt": _money_text(tax_kzt),
            "tax_kzt_withhold": _money_text(tax_kzt_withhold),
        }
        if table_name == "Yearly Coupons":
            row["only_profit"] = _money_text(only_profit)
            row["only_profit_kzt"] = _money_text(only_profit_kzt)
        rows.append(row)
    return rows


def _is_bond_redemption_fifo_row(record: Mapping[str, Any]) -> bool:
    if not str(record.get("source_trade_id") or "").startswith("CA:"):
        return False
    corporate_action_type = _string_or_none(record.get("corporate_action_type"))
    asset_type = str(record.get("asset_type") or "").lower()
    if corporate_action_type in {"maturity", "full_call"}:
        return True
    return corporate_action_type == "redemption" and "bond" in asset_type


def _is_derivative_record(record: Mapping[str, Any]) -> bool:
    asset_type = str(record.get("asset_type") or "").lower()
    symbol = str(record.get("symbol") or "").upper()
    description = str(record.get("description") or "").lower()
    financing_kind = str(record.get("financing_kind") or "").lower()
    if financing_kind == "swap":
        return True
    if ".SWAP" in symbol or description.startswith("swap reward"):
        return True
    return any(
        token in asset_type
        for token in (
            "option",
            "future",
            "futures",
            "fx_spot",
            "fx spot",
            "swap",
            "derivative",
            "\u043e\u043f\u0446\u0438\u043e\u043d",
            "\u0444\u044c\u044e\u0447\u0435\u0440",
            "\u0441\u0432\u043e\u043f",
        )
    )


def _derivative_tax_pnl(record: Mapping[str, Any]) -> Decimal:
    if record.get("pnl_before_commission") not in (None, ""):
        return _decimal(record.get("pnl_before_commission"))
    return _decimal(record.get("pnl"))


def _derivative_tax_pnl_kzt(record: Mapping[str, Any]) -> Decimal:
    if record.get("pnl_before_commission_kzt") not in (None, ""):
        return _decimal(record.get("pnl_before_commission_kzt"))
    if record.get("pnl_before_commission") not in (None, ""):
        rate = _decimal(record.get("kzt_rate"))
        if rate != 0:
            return _decimal(record.get("pnl_before_commission")) * rate
    return _decimal(record.get("pnl_kzt"))


def _taxable_exit_proceeds_kzt(record: Mapping[str, Any]) -> Decimal:
    if record.get("exit_amount_kzt") not in (None, ""):
        return abs(_decimal(record.get("exit_amount_kzt")))
    exit_amount = abs(_decimal(record.get("exit_amount")))
    rate = _decimal(record.get("kzt_rate"))
    if exit_amount == 0 or rate == 0:
        return Decimal("0")
    return exit_amount * rate


def _is_swap_interest_record(record: Mapping[str, Any]) -> bool:
    if str(record.get("financing_kind") or "").lower() == "swap":
        return True
    description = str(record.get("description") or "").lower()
    return description.startswith("swap reward")


def _is_kz_coupon(record: Mapping[str, Any], flags: Mapping[str, Any]) -> bool:
    if flags.get("issuer_outside_kz_flag") is False:
        return True
    issuer_country = _string_or_none(
        record.get("issuer_country")
        or record.get("country")
        or flags.get("issuer_country")
    )
    if str(issuer_country or "").strip().upper() == "KZ":
        return True
    isin = _string_or_none(record.get("isin") or flags.get("isin"))
    return str(isin or "").strip().upper().startswith("KZ")


def _build_dividend_year_groups(
    records: Sequence[Mapping[str, Any]],
    instrument_flags: Mapping[str, Mapping[str, Any]],
    *,
    aix_provider: AixInstrumentProvider,
    dividend_provider: KaseAixDividendProvider,
    offshore_provider: OffshoreJurisdictionProvider,
) -> dict[tuple[Any, ...], dict[str, Decimal]]:
    instrument_groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        year = _record_year(record, "pay_date", "date")
        instrument_groups[_dividend_reversal_key(record, year)].append(record)

    result: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
        lambda: {
            "amount": Decimal("0"),
            "amount_kzt": Decimal("0"),
            "withhold_kzt": Decimal("0"),
            "foreign_tax_credit_kzt": Decimal("0"),
        }
    )
    credit_groups: dict[tuple[Any, ...], dict[str, Decimal]] = defaultdict(
        lambda: {
            "amount": Decimal("0"),
            "amount_kzt": Decimal("0"),
            "tax_usd": Decimal("0"),
            "withholding_paid": Decimal("0"),
            "withholding_reverted": Decimal("0"),
            "rate": Decimal("0"),
        }
    )
    for (year, _instrument_key, currency), group_records in instrument_groups.items():
        representative = _dividend_group_representative(group_records)
        flags = _record_tax_flags(
            representative,
            instrument_flags,
            aix_provider=aix_provider,
            offshore_provider=offshore_provider,
        )
        dividend_flag = dividend_provider.preferential_flag(flags.get("isin") or representative.get("isin"), year)
        if dividend_flag is not None:
            flags["dividend_preferential_flag"] = dividend_flag
        key = _years_result_key("Yearly Dividends", year, {**representative, "currency": currency}, flags)
        gross_amount = sum((_decimal(record.get("gross_amount")) for record in group_records), Decimal("0"))
        gross_amount_kzt = sum((_decimal(record.get("gross_amount_kzt")) for record in group_records), Decimal("0"))
        tax_amount = sum((_decimal(record.get("tax")) for record in group_records), Decimal("0"))
        withholding_paid = sum(
            (abs(_decimal(record.get("withholding_tax"))) for record in group_records if _decimal(record.get("withholding_tax")) < 0),
            Decimal("0"),
        )
        withholding_reverted = sum(
            (_decimal(record.get("withholding_tax")) for record in group_records if _decimal(record.get("withholding_tax")) > 0),
            Decimal("0"),
        )
        rate = _rate_from_records(group_records)
        isin = _string_or_none(representative.get("isin"))
        has_withholding_or_revert = any(_decimal(record.get("withholding_tax")) != 0 for record in group_records)
        is_credit_candidate = bool(isin and isin.startswith("US")) or has_withholding_or_revert
        if is_credit_candidate:
            values = credit_groups[key]
            values["amount"] += gross_amount
            values["amount_kzt"] += gross_amount_kzt
            values["tax_usd"] += tax_amount
            values["rate"] = rate or values["rate"]
            values["withholding_paid"] += withholding_paid
            values["withholding_reverted"] += withholding_reverted
        else:
            values = result[key]
            values["amount"] += gross_amount
            values["amount_kzt"] += gross_amount_kzt

    for key, values in credit_groups.items():
        final_foreign_tax_paid = values["withholding_paid"] - values["withholding_reverted"]
        displayed_foreign_tax_paid = max(final_foreign_tax_paid, Decimal("0"))
        kz_tax_before_credit = values["tax_usd"]
        foreign_tax_credit = min(max(final_foreign_tax_paid, Decimal("0")), kz_tax_before_credit)
        if values["amount"] == 0 and values["amount_kzt"] == 0 and foreign_tax_credit == 0:
            continue
        result[key]["amount"] += values["amount"]
        result[key]["amount_kzt"] += values["amount_kzt"]
        result[key]["withhold_kzt"] -= displayed_foreign_tax_paid * values["rate"]
        result[key]["foreign_tax_credit_kzt"] -= foreign_tax_credit * values["rate"]
    return result


def _dividend_group_representative(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return next((record for record in records if _decimal(record.get("gross_amount")) != 0), records[0])


def _rate_from_records(records: Sequence[Mapping[str, Any]]) -> Decimal:
    for record in records:
        rate = record.get("kzt_rate")
        if rate not in (None, ""):
            return _decimal(rate)
    return Decimal("0")


def _dividend_reversal_key(record: Mapping[str, Any], year: int | None) -> tuple[Any, ...]:
    instrument_key = _string_or_none(record.get("isin") or record.get("symbol"))
    return (year, instrument_key, _string_or_none(record.get("currency")))


def _build_broker_realized_pl(
    reports: Sequence[ParsedIbReport],
    fx_provider: AnnualFxRateProvider,
    warnings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        year = _year_for_report(report)
        for row in report.rows.get(IB_SECTION_RU, []):
            asset_type = _string_or_none(row.get("Asset Category"))
            symbol = _string_or_none(row.get("Symbol"))
            if not asset_type or asset_type in {"Total", "Total (All Assets)"} or not symbol:
                continue
            currency = report.base_currency
            realized_pl = _decimal(row.get("Realized Total"))
            rate = _annual_rate(fx_provider, year, currency, warnings)
            rows.append(
                {
                    "year": year,
                    "asset_type": asset_type,
                    "symbol": symbol,
                    "currency": currency,
                    "realized_pl": str(realized_pl),
                    "kzt_rate": str(rate) if rate is not None else None,
                    "realized_pl_kzt": _amount_kzt(realized_pl, rate),
                    "source_report": str(report.path),
                }
            )
    return rows


def _instrument_tax_flags(dataset: CanonicalDataset) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for instrument in dataset.tables.get("Instruments", []):
        flags = {
            "isin": _string_or_none(instrument.get("isin")),
            "symbol": _string_or_none(instrument.get("symbol")),
            "issuer_country": _string_or_none(instrument.get("issuer_country") or instrument.get("country")),
            "exchange": _string_or_none(instrument.get("listing_exchange")),
            "issuer_outside_kz_flag": _bool_or_none(instrument.get("issuer_outside_kz_flag")),
            "offshore_flag": _bool_or_none(instrument.get("offshore_flag")),
            "preferential_tax_flag": _bool_or_none(instrument.get("preferential_tax_flag")),
        }
        for key in (instrument.get("isin"), instrument.get("symbol"), instrument.get("security_id")):
            if key:
                result[str(key)] = dict(flags)
    return result


def _record_tax_flags(
    record: Mapping[str, Any],
    instrument_flags: Mapping[str, Mapping[str, Any]],
    *,
    aix_provider: AixInstrumentProvider,
    offshore_provider: OffshoreJurisdictionProvider,
) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    for key in (record.get("isin"), record.get("symbol")):
        if key and str(key) in instrument_flags:
            flags.update(instrument_flags[str(key)])
            break
    isin = _string_or_none(record.get("isin") or flags.get("isin"))
    symbol = _string_or_none(record.get("symbol") or flags.get("symbol"))
    issuer_country = _string_or_none(record.get("issuer_country") or record.get("country") or flags.get("issuer_country"))
    offshore_flag = _bool_or_none(record.get("offshore_flag"))
    preferential_tax_flag = _bool_or_none(record.get("preferential_tax_flag") or record.get("kase_aix_preferential_flag"))
    issuer_outside_kz_flag = _bool_or_none(record.get("issuer_outside_kz_flag"))
    if offshore_flag is None:
        offshore_flag = _bool_or_none(flags.get("offshore_flag"))
    if preferential_tax_flag is None:
        preferential_tax_flag = _bool_or_none(flags.get("preferential_tax_flag"))
    if issuer_outside_kz_flag is None:
        issuer_outside_kz_flag = _bool_or_none(flags.get("issuer_outside_kz_flag"))
    if issuer_outside_kz_flag is None and issuer_country is not None:
        issuer_outside_kz_flag = issuer_country != "KZ"
    offshore_flag = bool(offshore_flag) or offshore_provider.is_offshore_isin(isin)
    exchange_bucket = _exchange_bucket(record, flags, aix_provider=aix_provider)
    exchange = _string_or_none(record.get("exchange") or flags.get("exchange"))
    is_aix_trade = str(exchange or "").strip().upper() == EXCHANGE_AIX or ".AIX." in str(symbol or "").upper()
    preferential_tax_flag = (
        not offshore_flag
        and (
            bool(preferential_tax_flag)
            or exchange_bucket in {EXCHANGE_AIX, EXCHANGE_KASE}
            or (not is_aix_trade and _is_kz_security(isin, symbol, exchange))
        )
    )
    return {
        "isin": isin,
        "symbol": symbol,
        "issuer_country": issuer_country,
        "issuer_outside_kz_flag": issuer_outside_kz_flag,
        "offshore_flag": offshore_flag,
        "preferential_tax_flag": preferential_tax_flag,
        "exchange_bucket": exchange_bucket,
    }


def _issuer_outside_kz_flags() -> dict[str, Any]:
    return {
        "issuer_outside_kz_flag": True,
        "offshore_flag": False,
        "preferential_tax_flag": False,
        "exchange_bucket": EXCHANGE_OUTOFKZ,
    }


def _exchange_bucket(
    record: Mapping[str, Any],
    flags: Mapping[str, Any],
    *,
    aix_provider: AixInstrumentProvider,
) -> str:
    isin = _string_or_none(record.get("isin") or flags.get("isin"))
    exchange = _string_or_none(record.get("exchange") or flags.get("exchange"))
    normalized_exchange = str(exchange or "").strip().upper().split(".", 1)[0]
    capital_gain_date = (
        record.get("exit_date")
        or record.get("pay_date")
        or record.get("date")
        or record.get("date_time")
    )
    if normalized_exchange == EXCHANGE_KASE:
        return EXCHANGE_KASE
    if normalized_exchange == EXCHANGE_AIX:
        return EXCHANGE_AIX
    if aix_provider.is_listed(isin, capital_gain_date):
        return EXCHANGE_AIX
    if str(isin or "").strip().upper().startswith("KZ"):
        return EXCHANGE_KASE
    return EXCHANGE_OUTOFKZ


def _is_kz_security(isin: str | None, symbol: str | None, exchange: str | None) -> bool:
    normalized_isin = str(isin or "").strip().upper()
    normalized_exchange = str(exchange or "").strip().upper()
    return normalized_isin.startswith("KZ") or normalized_exchange == EXCHANGE_KASE


def _years_result_key(
    table_name: str,
    year: int | None,
    record: Mapping[str, Any],
    flags: Mapping[str, Any],
) -> tuple[Any, ...]:
    country = _string_or_none(record.get("country")) if table_name in {"Yearly Trades", "Yearly Dividends"} else None
    exchange = _string_or_none(flags.get("exchange_bucket")) if table_name in {"Yearly Trades", "Yearly Derivatives"} else None
    return (
        table_name,
        year,
        _tax_source_flag(flags),
        country,
        exchange,
        _string_or_none(record.get("currency")),
    )


def _years_result_sort_key(key: tuple[Any, ...]) -> tuple[Any, ...]:
    table_name, year, flag, country, exchange, currency = key
    return (
        str(table_name or ""),
        -1 if year is None else int(year),
        str(flag or ""),
        str(country or ""),
        str(exchange or ""),
        str(currency or ""),
    )


def _tax_source_flag(flags: Mapping[str, Any]) -> str:
    dividend_flag = flags.get("dividend_preferential_flag")
    if dividend_flag in {FLAG_PREFERENTIAL_AIX, FLAG_PREFERENTIAL_KASE}:
        return str(dividend_flag)
    if flags.get("offshore_flag") is True:
        return FLAG_OFFSHORE
    if flags.get("preferential_tax_flag") is True:
        return FLAG_PREFERENTIAL
    return FLAG_NON_PREFERENTIAL


def _record_year(record: Mapping[str, Any], *date_fields: str) -> int | None:
    for field_name in date_fields:
        parsed = _parse_datetime(record.get(field_name))
        if parsed:
            return parsed.year
    year = record.get("year")
    return int(year) if year not in (None, "") else None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _populate_raw_totals(
    totals: RawReportTotals,
    reports: Sequence[ParsedIbReport],
    transfer_totals_by_currency: Mapping[str, Decimal],
    instrument_lookup: Mapping[tuple[str, int | None], dict[str, Any]],
) -> None:
    gross_trades = Decimal("0")
    commissions = Decimal("0")
    dividends_gross = Decimal("0")
    dividends_tax = Decimal("0")
    interest = Decimal("0")
    realized_pl = Decimal("0")

    for report in reports:
        year = _year_for_report(report)
        for row in report.rows.get(IB_SECTION_TRADES, []):
            if row.get("DataDiscriminator") and row.get("DataDiscriminator") != "Order":
                continue
            trade_dt = _parse_datetime(row.get("Date/Time"))
            trade_year = trade_dt.year if trade_dt else year
            trade_symbol = _strip_yield_suffix(_string_or_none(row.get("Symbol")))
            trade_currency = _string_or_none(row.get("Currency"))
            instrument = _lookup_instrument(instrument_lookup, trade_symbol, trade_year) or {}
            instrument_key = _string_or_none(instrument.get("isin") or trade_symbol)
            gross_amount = abs(_decimal(row.get("Proceeds")))
            gross_trades += gross_amount
            commission = abs(_decimal(row.get("Comm/Fee") or row.get("Comm in USD")))
            commissions += commission
            turnover_key = _dimension_key(
                metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value,
                year=trade_year,
                currency=trade_currency,
                instrument_key=instrument_key,
            )
            totals.totals_by_metric_currency[turnover_key] = (
                totals.totals_by_metric_currency.get(turnover_key, Decimal("0")) + gross_amount
            )
            asset_type = _string_or_none(row.get("Asset Category")) or instrument.get("asset_type")
            broker_pnl = _broker_trade_pnl(row, asset_type)
            if broker_pnl is not None:
                if str(asset_type or "").lower() == "forex":
                    broker_pnl -= commission
                pnl_key = _dimension_key(
                    metric=ReconciliationMetric.PNL_AFTER_ALL_COMMISSIONS_BY_INSTRUMENT.value,
                    year=trade_year,
                    currency=trade_currency,
                    instrument_key=instrument_key,
                )
                totals.totals_by_metric_currency[pnl_key] = (
                    totals.totals_by_metric_currency.get(pnl_key, Decimal("0")) + broker_pnl
                )
        for row in report.rows.get(IB_SECTION_CA, []):
            symbol, isin, _ = _extract_symbol_isin(_string_or_none(row.get("Description")))
            action = {
                "action_type": _infer_corporate_action_type(_string_or_none(row.get("Description"))),
                "quantity": str(_decimal(row.get("Quantity"))),
                "proceeds": str(_decimal(row.get("Proceeds"))),
            }
            if not _is_synthetic_exit_corporate_action(action):
                continue
            action_dt = _parse_datetime(row.get("Date/Time")) or _parse_datetime(row.get("Date_Time"))
            action_year = action_dt.year if action_dt else year
            currency = _string_or_none(row.get("Currency"))
            instrument = _lookup_instrument(instrument_lookup, isin, action_year) or _lookup_instrument(instrument_lookup, symbol, action_year) or {}
            instrument_key = _string_or_none(instrument.get("isin") or isin or symbol)
            gross_amount = abs(_decimal(row.get("Proceeds")))
            gross_trades += gross_amount
            turnover_key = _dimension_key(
                metric=ReconciliationMetric.TRADE_GROSS_AMOUNT_BY_INSTRUMENT.value,
                year=action_year,
                currency=currency,
                instrument_key=instrument_key,
            )
            totals.totals_by_metric_currency[turnover_key] = (
                totals.totals_by_metric_currency.get(turnover_key, Decimal("0")) + gross_amount
            )
        dividends_gross += _section_total(report, IB_SECTION_DIVIDENDS)
        dividends_tax += _section_total(report, IB_SECTION_TAX)
        interest += _section_total(report, IB_SECTION_INTEREST)
        for row in report.rows.get(IB_SECTION_RU, []):
            if row.get("Asset Category") == "Total (All Assets)":
                realized_pl += _decimal(row.get("Realized Total"))
        for row in report.rows.get(IB_SECTION_CASH, []):
            if row.get("Currency Summary") == "Ending Cash" and row.get("Currency") not in {None, "", "Base Currency Summary"}:
                totals.cash_by_currency[_dimension_key(year=year, currency=str(row.get("Currency")))] = _decimal(row.get("Total"))
        raw_position_keys: set[str] = set()
        for row in report.rows.get(IB_SECTION_POSITIONS, []):
            if row.get("DataDiscriminator") and row.get("DataDiscriminator") != "Summary":
                continue
            asset_type = _string_or_none(row.get("Asset Category"))
            if _is_cash_position_asset(asset_type):
                continue
            raw_symbol = _string_or_none(row.get("Symbol"))
            normalized_symbol = _normalize_position_symbol(raw_symbol)
            instrument = _lookup_instrument(instrument_lookup, normalized_symbol or raw_symbol, year) or {}
            instrument_key = _string_or_none(instrument.get("isin")) or normalized_symbol or raw_symbol
            key = _dimension_key(year=year, instrument_key=instrument_key)
            raw_position_keys.add(key)
            totals.positions_by_key[key] = totals.positions_by_key.get(key, Decimal("0")) + _decimal(row.get("Quantity"))
        for row in report.rows.get(IB_SECTION_MTM, []):
            asset_type = _string_or_none(row.get("Asset Category"))
            if not asset_type or asset_type.startswith("Total"):
                continue
            if _is_cash_position_asset(asset_type):
                continue
            raw_symbol = _string_or_none(row.get("Symbol"))
            normalized_symbol = _normalize_position_symbol(raw_symbol)
            if not normalized_symbol:
                continue
            instrument = _lookup_instrument(instrument_lookup, normalized_symbol, year) or {}
            instrument_key = _string_or_none(instrument.get("isin")) or normalized_symbol
            key = _dimension_key(year=year, instrument_key=instrument_key)
            if key in raw_position_keys:
                continue
            totals.positions_by_key[key] = totals.positions_by_key.get(key, Decimal("0")) + _decimal(row.get("Current Quantity"))

    for currency, amount in transfer_totals_by_currency.items():
        key = _dimension_key(metric=ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value, currency=currency)
        totals.totals_by_metric_currency[key] = amount

    totals.scalar_totals.update(
        {
            ReconciliationMetric.TOTAL_TRADES_GROSS_AMOUNT.value: gross_trades,
            ReconciliationMetric.TOTAL_COMMISSIONS.value: commissions,
            ReconciliationMetric.TOTAL_DIVIDENDS_GROSS.value: dividends_gross,
            ReconciliationMetric.TOTAL_DIVIDENDS_TAX.value: dividends_tax,
            ReconciliationMetric.TOTAL_DIVIDENDS_NET.value: dividends_gross + dividends_tax,
            ReconciliationMetric.TOTAL_INTEREST.value: interest,
            ReconciliationMetric.TOTAL_COUPONS.value: Decimal("0"),
            ReconciliationMetric.TOTAL_DEPOSITS_WITHDRAWALS_TRANSFERS.value: sum(transfer_totals_by_currency.values(), Decimal("0")),
            ReconciliationMetric.REALIZED_PL.value: realized_pl,
        }
    )


def _section_total(report: ParsedIbReport, section: str) -> Decimal:
    total = Decimal("0")
    for row in report.rows.get(section, []):
        marker = str(row.get("Currency") or "")
        if marker == "Total" or marker.startswith("Total"):
            continue
        total += _decimal(row.get("Amount"))
    return total


def _is_fx_trade(record: Mapping[str, Any]) -> bool:
    asset_type = str(record.get("asset_type") or "")
    return asset_type.lower() == "forex"


def _normalize_position_symbol(symbol: str | None) -> str | None:
    if not symbol:
        return None
    return symbol.split(" - ", 1)[0].strip()


def _is_cash_position_asset(asset_type: str | None) -> bool:
    return str(asset_type or "").strip().lower() in {"cash", "forex"}


def _is_total_amount_row(row: Mapping[str, Any]) -> bool:
    marker = str(row.get("Currency") or "")
    return marker == "Total" or marker.startswith("Total")


def _dimension_key(
    *,
    metric: str | None = None,
    year: int | None = None,
    currency: str | None = None,
    instrument_key: str | None = None,
) -> str:
    return "|".join("" if value is None else str(value) for value in (metric, year, currency, instrument_key))
