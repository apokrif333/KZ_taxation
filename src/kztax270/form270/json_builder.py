"""Form 270 JSON draft builder."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from kztax270.canonical.schema import CanonicalDataset


ZERO = Decimal("0")
HALF = Decimal("0.5")
SECURITIES_ASSET_CODE = "3"
DERIVATIVE_ASSET_CODE = "4"
SECURITIES_ASSET_NAME = "ценные бумаги"
DERIVATIVE_ASSET_NAME = "производные финансовые инструменты"
OPERATION_PURCHASE = "Покупка"
OPERATION_EXCHANGE_ACQUIRED = "Приобретено путем обмена"
OPERATION_EXCHANGE_DISPOSED = "Отчуждено путем обмена"
OPERATION_SALE = "Продажа"
OPERATION_GRATUITOUS = "Безвозмездно полученное (за исключением наследства)"
OPERATION_OTHER = "Другой способ"
BOND_REDEMPTION_OTHER_TEXT = "Погашение"
REFERENCE_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "data" / "templates"
COUNTRY_CODES_FILE = "ThreeSymbolsISOCountres.json"
CURRENCY_CODES_FILE = "ThreeSymbolsCurrency.json"
TRADES_TYPES_FILE = "TradesTypes.json"
ASSET_TYPES_FILE = "AssetsTypes.json"
SOURCE_OWN_FUNDS = (
    "собственные средства (денежные средства, полученный доход с момента представления "
    "первоначальной Декларации об активах и обязательствах)"
)
SOURCE_ASSET_SALE = "денежные средства от реализации активов"

COUNTRY_ISO3_BY_ISO2 = {
    "BM": "BMU",
    "BS": "BHS",
    "CA": "CAN",
    "CH": "CHE",
    "CY": "CYP",
    "CYPRUS": "CYP",
    "GB": "GBR",
    "IE": "IRL",
    "IL": "ISR",
    "JE": "JEY",
    "KY": "CYM",
    "KZ": "KAZ",
    "KAZAKHSTAN": "KAZ",
    "LU": "LUX",
    "MH": "MHL",
    "MU": "MUS",
    "NL": "NLD",
    "PA": "PAN",
    "PR": "PRI",
    "RU": "RUS",
    "RUSSIA": "RUS",
    "SG": "SGP",
    "TW": "TWN",
    "US": "USA",
    "UNITED STATES": "USA",
}

COUNTRY_NAME_RU_BY_CODE = {
    "BM": "Бермуды",
    "BMU": "Бермуды",
    "BS": "Багамы",
    "BHS": "Багамы",
    "CA": "Канада",
    "CAN": "Канада",
    "CH": "Швейцария",
    "CHE": "Швейцария",
    "CY": "Кипр",
    "CYP": "Кипр",
    "GB": "Великобритания",
    "GBR": "Великобритания",
    "IE": "Ирландия",
    "IRL": "Ирландия",
    "IL": "Израиль",
    "ISR": "Израиль",
    "JE": "Джерси",
    "JEY": "Джерси",
    "KY": "Каймановы острова",
    "CYM": "Каймановы острова",
    "KZ": "Казахстан",
    "KAZ": "Казахстан",
    "LU": "Люксембург",
    "LUX": "Люксембург",
    "MH": "Маршалловы острова",
    "MHL": "Маршалловы острова",
    "MU": "Маврикий",
    "MUS": "Маврикий",
    "NL": "Нидерланды",
    "NLD": "Нидерланды",
    "PA": "Панама",
    "PAN": "Панама",
    "PR": "Пуэрто-Рико",
    "PRI": "Пуэрто-Рико",
    "RU": "Россия",
    "RUS": "Россия",
    "SG": "Сингапур",
    "SGP": "Сингапур",
    "TW": "Тайвань",
    "TWN": "Тайвань",
    "US": "США",
    "USA": "США",
}

DEFAULT_BROKER_BANK_INFO: dict[str, dict[str, str]] = {
    "ib": {"code": "IBKRUS33XXX", "name": "Interactive Brokers LLC", "country": "USA"},
    "exante": {"code": "EXAEMTM1", "name": "EXT LTD", "country": "Cyprus"},
    "tsifra": {"code": "FRFLRUMM", "name": "ООО «Цифра брокер»", "country": "Russia"},
    "freedom": {"code": "KCJBKZKX", "name": "Freedom Finance Global PLC", "country": "Kazakhstan"},
}

YEARS_HEADER_ALIASES = {
    "Year": "year",
    "Flag": "flag",
    "Country": "country",
    "Exchange": "tax_exchange",
    "Tax_Exchange": "tax_exchange",
    "Currency": "currency",
    "PnL": "pnl",
    "PnL_KZT": "pnl_kzt",
    "Amount": "amount",
    "Amount_KZT": "amount_kzt",
    "OnlyProfit": "only_profit",
    "OnlyProfit_KZT": "only_profit_kzt",
    "Withhold_KZT": "withhold_kzt",
    "Tax_KZT": "tax_kzt",
    "Tax_KZT_Withhold": "tax_kzt_withhold",
}


@dataclass(frozen=True, slots=True)
class Form270Owner:
    fio1: str
    fio2: str
    fio3: str
    iin: str

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.fio1, self.fio2, self.fio3) if part).strip()


@dataclass(frozen=True, slots=True)
class BrokerBankInfo:
    code: str
    name: str
    country: str


@dataclass(slots=True)
class Form270JsonBuilder:
    template_path: Path

    def load_template(self) -> dict[str, Any]:
        with self.template_path.open("r", encoding="utf-8-sig") as handle:
            template = json.load(handle)
        if not isinstance(template, dict):
            raise ValueError("Form 270 template must be a JSON object")
        return template

    def build_account_draft(
        self,
        dataset: CanonicalDataset,
        *,
        tax_year: int,
        taxpayer: Mapping[str, Any] | Form270Owner | None = None,
        split: bool = False,
        civ_servant: bool = False,
        bank_info: Mapping[str, Any] | BrokerBankInfo | None = None,
        bank_infos: Mapping[str, BrokerBankInfo] | None = None,
    ) -> dict[str, Any]:
        broker = dataset.metadata.broker
        return self._build_draft(
            dataset.tables,
            tax_year=tax_year,
            taxpayer=taxpayer,
            broker=broker,
            split=split,
            civ_servant=civ_servant,
            bank_info=bank_info,
            bank_infos=bank_infos,
        )

    def build_processed_workbook_draft(
        self,
        workbook_path: Path,
        *,
        tax_year: int,
        taxpayer: Mapping[str, Any] | Form270Owner | None = None,
        broker: str | None = None,
        account_id: str | None = None,
        split: bool = False,
        civ_servant: bool = False,
        bank_info: Mapping[str, Any] | BrokerBankInfo | None = None,
        bank_infos: Mapping[str, BrokerBankInfo] | None = None,
    ) -> dict[str, Any]:
        tables = load_processed_workbook_tables(workbook_path)
        resolved_broker, _resolved_account = _broker_account_from_workbook_path(workbook_path)
        return self._build_draft(
            tables,
            tax_year=tax_year,
            taxpayer=taxpayer,
            broker=broker or resolved_broker,
            split=split,
            civ_servant=civ_servant,
            bank_info=bank_info,
            bank_infos=bank_infos,
        )

    def _build_draft(
        self,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        tax_year: int,
        taxpayer: Mapping[str, Any] | Form270Owner | None,
        broker: str,
        split: bool,
        civ_servant: bool,
        bank_info: Mapping[str, Any] | BrokerBankInfo | None,
        bank_infos: Mapping[str, BrokerBankInfo] | None,
    ) -> dict[str, Any]:
        draft = copy.deepcopy(self.load_template())
        draft["fnoYear"] = tax_year
        _apply_taxpayer(draft, taxpayer)

        content = draft.setdefault("fnoContent", {})
        content["type"] = "fno270"
        selected_applications: list[str] = []

        application_01 = content.setdefault("application_01", {})
        tax_values = _build_application_01(tables.get("Years_Results", []), tax_year=tax_year, split=split)
        if tax_values is not None:
            _deep_update(application_01, tax_values)
            selected_applications.append("application_01")

        application_04 = content.setdefault("application_04", {"B": [], "C": [], "D": [], "E": []})
        application_04["B"] = [] if civ_servant else _build_application_04_b(tables, tax_year=tax_year, split=split)
        application_04["C"] = _build_application_04_c(
            tables,
            tax_year=tax_year,
            split=split,
            broker=broker,
            bank_info=_resolve_bank_info(broker, bank_info),
            bank_infos=bank_infos,
        )
        application_04["D"] = []
        application_04["E"] = _build_application_04_e(tables, tax_year=tax_year)
        if any(application_04.get(section) for section in ("B", "C", "D", "E")):
            selected_applications.append("application_04")

        application_05 = content.setdefault("application_05", {"B": [], "C": []})
        if civ_servant:
            owner = _owner_from_taxpayer(taxpayer)
            application_05["B"], application_05["C"] = _build_application_05(
                tables,
                tax_year=tax_year,
                split=split,
                owner=owner,
            )
            if application_05["B"] or application_05["C"]:
                selected_applications.append("application_05")
        else:
            application_05["B"] = []
            application_05["C"] = []

        content.setdefault("commonInfo", {})["selectedApplications"] = selected_applications
        return draft

    def save(self, form: Mapping[str, Any], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(form, handle, ensure_ascii=False, indent=4)
        return output_path


def load_processed_workbook_tables(workbook_path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise RuntimeError("Reading processed workbooks requires pandas and openpyxl.") from exc

    from kztax270.canonical.workbook_schema import CANONICAL_WORKBOOK_SHEETS
    from kztax270.excel.audit_workbook import display_column_name

    if not workbook_path.exists():
        raise FileNotFoundError(workbook_path)

    with pd.ExcelFile(workbook_path) as excel:
        sheet_names = frozenset(excel.sheet_names)
    tables: dict[str, list[dict[str, Any]]] = {}
    for sheet in CANONICAL_WORKBOOK_SHEETS:
        if sheet.name not in sheet_names:
            continue
        if sheet.name == "Years_Results":
            tables[sheet.name] = _read_years_results_sheet(workbook_path)
            continue

        display_to_canonical = {display_column_name(column): column for column in sheet.required_columns}
        display_to_canonical.update({column: column for column in sheet.required_columns})
        df = pd.read_excel(workbook_path, sheet_name=sheet.name, dtype=object)
        df = df.rename(columns={column: display_to_canonical.get(str(column), _snake_case(str(column))) for column in df.columns})
        df = df.where(pd.notna(df), None)
        tables[sheet.name] = [dict(record) for record in df.to_dict(orient="records")]
    return tables


def _read_years_results_sheet(workbook_path: Path) -> list[dict[str, Any]]:
    import pandas as pd  # type: ignore

    df = pd.read_excel(workbook_path, sheet_name="Years_Results", header=None, dtype=object)
    rows: list[dict[str, Any]] = []
    idx = 0
    while idx < len(df):
        non_null = [value for value in df.iloc[idx].tolist() if not _is_missing(value)]
        if len(non_null) != 1 or not str(non_null[0]).startswith("Yearly"):
            idx += 1
            continue

        table_name = str(non_null[0]).strip()
        idx += 1
        while idx < len(df) and all(_is_missing(value) for value in df.iloc[idx].tolist()):
            idx += 1
        if idx >= len(df):
            break

        headers = [_canonical_years_header(value) for value in df.iloc[idx].tolist()]
        idx += 1
        while idx < len(df):
            raw_values = df.iloc[idx].tolist()
            non_null = [value for value in raw_values if not _is_missing(value)]
            if not non_null:
                break
            if len(non_null) == 1 and str(non_null[0]).startswith("Yearly"):
                idx -= 1
                break

            record: dict[str, Any] = {"table": table_name}
            for header, value in zip(headers, raw_values, strict=False):
                if header is None:
                    continue
                record[header] = None if _is_missing(value) else value
            rows.append(record)
            idx += 1
        idx += 1
    return rows


def _build_application_01(
    years_results: Sequence[Mapping[str, Any]],
    *,
    tax_year: int,
    split: bool,
) -> dict[str, Any] | None:
    rows = [row for row in years_results if _int_or_none(row.get("year")) == tax_year]
    if not rows:
        return None

    preferential_flags = {"preferential", "Issuer_KZ", "Preferential"}
    # Offshore trades retain their separate audit flag and tax base, but they
    # are non-preferential when income is split between A.1.1 and A.1.2.
    non_preferential_trade_flags = {"non-preferential", "offshore"}
    trades_kz_preferential = _sum_then_floor(
        rows,
        "pnl_kzt",
        table="Yearly Trades",
        flags=preferential_flags,
        country_is_kz=True,
    )
    trades_kz_non_preferential = _sum_then_floor(
        rows,
        "pnl_kzt",
        table="Yearly Trades",
        flags=non_preferential_trade_flags,
        country_is_kz=True,
    )
    trades_foreign_preferential = _sum_then_floor(
        rows,
        "pnl_kzt",
        table="Yearly Trades",
        flags=preferential_flags,
        country_is_kz=False,
    )
    trades_foreign_non_preferential = _sum_then_floor(
        rows,
        "pnl_kzt",
        table="Yearly Trades",
        flags=non_preferential_trade_flags,
        country_is_kz=False,
    )
    trades_kz = trades_kz_preferential + trades_kz_non_preferential
    trades_foreign = trades_foreign_preferential + trades_foreign_non_preferential
    preferential_trades = _sum_positive(rows, "pnl_kzt", table="Yearly Trades", flags=preferential_flags)
    preferential_aix_trades = _sum_positive(
        rows,
        "pnl_kzt",
        table="Yearly Trades",
        flags=preferential_flags,
        tax_exchange="AIX",
    )
    preferential_kase_trades = preferential_trades - preferential_aix_trades
    dividends = _sum_positive(rows, "amount_kzt", table="Yearly Dividends")
    dividend_corrections = _sum_positive(rows, "amount_kzt", table="Yearly Dividends", flags=preferential_flags)
    preferential_kase_dividends = _sum_positive(
        rows,
        "amount_kzt",
        table="Yearly Dividends",
        flags={"preferential_kase"},
    )
    preferential_aix_dividends = _sum_positive(
        rows,
        "amount_kzt",
        table="Yearly Dividends",
        flags={"preferential_aix"},
    )
    interest = _sum_positive(rows, "only_profit_kzt", table="Yearly Interest")
    coupons = _sum_positive(rows, "only_profit_kzt", table="Yearly Coupons")
    coupon_corrections = _sum_positive(
        rows,
        "only_profit_kzt",
        table="Yearly Coupons",
        flags=preferential_flags,
    )
    bond_redemptions = _sum_positive(rows, "pnl_kzt", table="Yearly Bonds Redemption")
    corp_actions = _sum_positive(rows, "pnl_kzt", table="Yearly Corp Actions")
    repos = _sum_positive(rows, "pnl_kzt", table="Yearly Repo")
    derivatives = _sum_positive(rows, "only_profit_kzt", table="Yearly Derivatives")
    swaps = _sum_positive(rows, "pnl_kzt", table="Yearly Swaps")
    other = _sum_positive(rows, "pnl_kzt", table="Yearly SpinOff_Redemp")

    values = {
        "trades_kz": trades_kz,
        "trades_kz_preferential": trades_kz_preferential,
        "trades_kz_non_preferential": trades_kz_non_preferential,
        "trades_foreign_preferential": trades_foreign_preferential,
        "trades_foreign_non_preferential": trades_foreign_non_preferential,
        "preferential_kase_trades": preferential_kase_trades,
        "preferential_aix_trades": preferential_aix_trades,
        "trades_foreign": trades_foreign,
        "dividends": dividends,
        "dividend_corrections": dividend_corrections,
        "preferential_kase_dividends": preferential_kase_dividends,
        "preferential_aix_dividends": preferential_aix_dividends,
        "interest": interest,
        "coupons": coupons,
        "coupon_corrections": coupon_corrections,
        "bond_redemptions": bond_redemptions,
        "corp_actions": corp_actions,
        "repos": repos,
        "derivatives": derivatives,
        "swaps": swaps,
        "other": other,
        "foreign_tax_credit": _foreign_tax_credit(rows),
    }
    if split:
        values = {key: value * HALF for key, value in values.items()}

    a1 = values["trades_kz"] + values["trades_foreign"]
    b_1_4 = values["dividends"]
    b_1_5 = values["interest"] + values["coupons"] + values["bond_redemptions"] + values["corp_actions"] + values["repos"]
    b_1_9 = values["derivatives"] + values["swaps"] + values["other"]
    b1 = b_1_4 + b_1_5 + b_1_9
    d_total = a1 + b1
    e1 = (
        values["preferential_kase_trades"]
        + values["coupon_corrections"]
        + values["bond_redemptions"]
        + values["corp_actions"]
        + values["dividend_corrections"]
        + values["preferential_kase_dividends"]
    )
    e4 = values["preferential_aix_trades"] + values["preferential_aix_dividends"]
    e_total = e1 + e4
    g = d_total - e_total
    h = g * Decimal("0.10")
    i = values["foreign_tax_credit"]
    k = h - i

    if d_total <= ZERO:
        return None

    return {
        "A": {
            "_A": _round_int(a1),
            "_A1": _round_int(a1),
            "_01": _amount_or_none(values["trades_kz"]),
            "_02": _amount_or_none(values["trades_foreign"]),
            "_A2": None,
            "_A3": None,
            "_A4": None,
            "_A5": None,
        },
        "B": {
            "_B": _round_int(b1),
            "_B1": _round_int(b1),
            "_01": None,
            "_02": None,
            "_03": None,
            "_04": _amount_or_none(b_1_4),
            "_05": _amount_or_none(b_1_5),
            "_06": None,
            "_07": None,
            "_08": None,
            "_09": _amount_or_none(b_1_9),
            "_B2": None,
            "_B3": None,
            "_B4": None,
            "_B5": None,
            "_B6": None,
            "_B7": None,
        },
        "_C": None,
        "_D": _round_int(d_total),
        "E": {
            "_E": _round_int(e_total),
            "_E1": _round_int(e1),
            "_E2": None,
            "_E3": None,
            "_E4": _amount_or_none(e4),
        },
        "F": {"F": None, "_F1": None, "_F2": None},
        "_G": _round_int(g),
        "_H": _round_int(h),
        "_I": _round_int(i),
        "_J": None,
        "_K": _round_int(k),
    }


def _build_application_04_b(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    tax_year: int,
    split: bool,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Decimal]] = {}
    for row in tables.get("Trades", []):
        parsed_date = _parse_date(row.get("date_time"))
        if parsed_date is None or parsed_date.year != tax_year:
            continue
        if not _is_application_04_property_asset(row):
            continue
        country = _country_from_row(row)
        if country == "KZ":
            continue

        quantity = _decimal(row.get("quantity"))
        if quantity == ZERO:
            continue
        operation, operation_other = _application_04_operation(row, quantity)
        identifier = _instrument_identifier(row)
        if identifier is None:
            continue
        asset_code = _asset_kind_code(row)
        country_code = _country_code_for_form(country)
        currency_code = _currency_code_for_form(_str_or_none(row.get("currency")))
        registration_date = _format_date(parsed_date)
        key = (
            parsed_date.date().isoformat(),
            registration_date,
            operation,
            operation_other,
            asset_code,
            identifier,
            country_code,
            currency_code,
        )
        values = grouped.setdefault(key, {"quantity": ZERO, "amount": ZERO})
        values["quantity"] += abs(quantity)
        values["amount"] += abs(_decimal(row.get("amount")))

    rows: list[dict[str, Any]] = []
    for index, (key, values) in enumerate(sorted(grouped.items(), key=lambda item: item[0]), start=1):
        _sort_date, registration_date, operation, operation_other, asset_code, identifier, country_code, currency_code = key
        quantity = values["quantity"] * HALF if split else values["quantity"]
        amount = values["amount"] * HALF if split else values["amount"]
        rows.append(
            {
                "A": _row_no(index),
                "B": operation,
                "_01": operation_other,
                "C": asset_code,
                "_02": None,
                "D": _decimal_json(quantity, places=4),
                "E": identifier,
                "F": registration_date,
                "G": "-",
                "H": country_code,
                "I": currency_code,
                "val_J": {"value": _decimal_json(amount, places=2), "manual": True},
                "index": index - 1,
            }
        )

    return rows


def _build_application_04_c(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    tax_year: int,
    split: bool,
    broker: str,
    bank_info: BrokerBankInfo | None,
    bank_infos: Mapping[str, BrokerBankInfo] | None,
) -> list[dict[str, Any]]:
    balances: dict[tuple[str, str], Decimal] = {}
    for row in tables.get("CashBalances", []):
        if _int_or_none(row.get("year")) != tax_year:
            continue
        source_broker = (_str_or_none(row.get("broker")) or broker).lower()
        currency = _str_or_none(row.get("currency"))
        if currency is None or currency == "KZT":
            continue
        amount = _decimal(row.get("ending_cash"))
        if amount <= ZERO:
            continue
        key = (source_broker, currency)
        balances[key] = balances.get(key, ZERO) + amount

    rows = []
    for (source_broker, currency), amount in sorted(balances.items()):
        source_bank_info = (bank_infos or {}).get(source_broker)
        if source_bank_info is None and source_broker == broker.lower():
            source_bank_info = bank_info
        if source_bank_info is None:
            source_bank_info = _resolve_bank_info(source_broker, None)
        if source_bank_info is None:
            continue
        balance = amount * HALF if split else amount
        rounded_balance = _round_int(balance)
        if rounded_balance <= 1:
            continue
        index = len(rows) + 1
        rows.append(
            {
                "A": _row_no(index),
                "B": source_bank_info.code,
                "C": source_bank_info.name,
                "D": _country_code_for_form(source_bank_info.country),
                "E": currency,
                "F": rounded_balance,
                "index": index - 1,
            }
        )
    return rows


def _build_application_04_e(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    tax_year: int,
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for row in tables.get("Positions", []):
        if _int_or_none(row.get("year")) != tax_year:
            continue
        if _is_excluded_security(row) or _is_forex(row):
            continue
        country = _country_from_row(row)
        if country == "KZ":
            continue
        identifier = _instrument_identifier(row)
        if identifier is None:
            continue
        key = (identifier, country or "")
        if key in seen:
            continue
        seen.add(key)
        country_code = _country_code_for_form(country)
        rows.append(
            {
                "A": _row_no(len(rows) + 1),
                "B": _asset_kind_code(row),
                "_01": None,
                "C": identifier,
                "D": country_code,
                "E": COUNTRY_NAME_RU_BY_CODE.get(country_code, COUNTRY_NAME_RU_BY_CODE.get(country or "", country_code)),
                "index": len(rows),
            }
        )
    return rows


def _build_application_05(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    tax_year: int,
    split: bool,
    owner: Form270Owner | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trades = [
        row
        for row in tables.get("Trades", [])
        if not _is_excluded_security(row) and not _is_forex(row) and _parse_date(row.get("date_time")) is not None
    ]
    if not trades:
        return [], []

    rates = _rate_lookup(tables)
    running_sale_funds = ZERO
    source_by_key: dict[int, tuple[str, str, str]] = {}
    for idx, row in sorted(enumerate(trades), key=lambda item: _parse_date(item[1].get("date_time")) or datetime.min):
        signed_amount = _signed_trade_amount(row)
        currency = _str_or_none(row.get("currency")) or "KZT"
        year = (_parse_date(row.get("date_time")) or datetime(tax_year, 1, 1)).year
        amount_kzt = signed_amount * rates.get((year, currency), Decimal("1"))
        if amount_kzt < ZERO:
            running_sale_funds += amount_kzt
            source_by_key[idx] = ("", "", "")
            continue
        if amount_kzt > ZERO and abs(running_sale_funds) > amount_kzt:
            running_sale_funds += amount_kzt
            source_by_key[idx] = _source_tuple(owner, SOURCE_ASSET_SALE)
        elif amount_kzt > ZERO:
            source_by_key[idx] = _source_tuple(owner, SOURCE_OWN_FUNDS)
        else:
            source_by_key[idx] = ("", "", "")

    buys: list[dict[str, Any]] = []
    sells: list[dict[str, Any]] = []
    for idx, row in sorted(enumerate(trades), key=lambda item: _parse_date(item[1].get("date_time")) or datetime.min):
        parsed_date = _parse_date(row.get("date_time"))
        if parsed_date is None or parsed_date.year != tax_year:
            continue
        quantity = _decimal(row.get("quantity"))
        identifier = _instrument_identifier(row)
        if identifier is None or quantity == ZERO:
            continue
        country = _country_code_for_form(_country_from_row(row))
        currency = _str_or_none(row.get("currency")) or ""
        amount = abs(_decimal(row.get("amount_with_commission") or row.get("amount")))
        if split:
            amount *= HALF
        if quantity > ZERO:
            source, source_id, source_name = source_by_key.get(idx, ("", "", ""))
            buys.append(
                {
                    "A": _row_no(len(buys) + 1),
                    "B": _asset_kind_code(row),
                    "C": identifier,
                    "D": _format_date(parsed_date),
                    "E": country,
                    "F": "-",
                    "G": currency,
                    "H": _decimal_json(amount, places=2),
                    "I": source,
                    "J": source_id,
                    "K": source_name,
                    "L": currency,
                    "M": _decimal_json(amount, places=2),
                    "index": len(buys),
                }
            )
        else:
            year = parsed_date.year
            amount_kzt = amount * rates.get((year, currency), Decimal("1"))
            sells.append(
                {
                    "A": _row_no(len(sells) + 1),
                    "B": _asset_kind_code(row),
                    "C": identifier,
                    "D": _format_date(parsed_date),
                    "E": country,
                    "F": "-",
                    "G": "-",
                    "H": "-",
                    "I": _decimal_json(amount_kzt, places=2),
                    "index": len(sells),
                }
            )
    return buys, sells


def _apply_taxpayer(draft: dict[str, Any], taxpayer: Mapping[str, Any] | Form270Owner | None) -> None:
    taxpayer_map = _taxpayer_mapping(taxpayer)
    owner = _owner_from_taxpayer(taxpayer)
    iin = owner.iin if owner else _str_or_none(taxpayer_map.get("iin") or taxpayer_map.get("taxpayerCode"))
    full_name = owner.full_name if owner else _str_or_none(taxpayer_map.get("full_name") or taxpayer_map.get("taxpayerNameRu"))
    full_name_upper = full_name.upper() if full_name else None

    if iin:
        for key in ("taxpayerCode", "creatorCode", "headTaxpayerCode"):
            draft[key] = iin
    if full_name_upper:
        for key in ("taxpayerNameRu", "taxpayerNameKk", "taxpayerNameEn", "taxpayerNameQq"):
            draft[key] = full_name_upper
        for key in ("creatorNameRu", "creatorNameKk", "creatorNameEn", "creatorNameQq"):
            draft[key] = full_name_upper

    content = draft.setdefault("fnoContent", {})
    common_info = content.setdefault("commonInfo", {})
    if taxpayer_map.get("phone") is not None:
        common_info["_4"] = taxpayer_map.get("phone")
    if taxpayer_map.get("email") is not None:
        common_info["_4_1"] = taxpayer_map.get("email")
    if taxpayer_map.get("spouse_iin") is not None:
        common_info["_6"] = taxpayer_map.get("spouse_iin")
    if taxpayer_map.get("ogdCodeByResidence") is not None:
        draft["ogdCodeByResidence"] = taxpayer_map.get("ogdCodeByResidence")
    if taxpayer_map.get("ogdCodeByLocation") is not None:
        draft["ogdCodeByLocation"] = taxpayer_map.get("ogdCodeByLocation")

    responsibility = content.setdefault("taxpayerResponsibility", {})
    if full_name_upper:
        responsibility["fullNameOfHead"] = full_name_upper
    responsibility["declarationDate"] = taxpayer_map.get("declarationDate") or datetime.now().strftime("%d.%m.%Y")

    for key, value in taxpayer_map.items():
        if value is not None and key in draft:
            draft[key] = value


def _deep_update(target: dict[str, Any], values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _resolve_bank_info(broker: str, bank_info: Mapping[str, Any] | BrokerBankInfo | None) -> BrokerBankInfo | None:
    if isinstance(bank_info, BrokerBankInfo):
        return bank_info
    if bank_info is None:
        bank_info = DEFAULT_BROKER_BANK_INFO.get(broker.lower())
    if not bank_info:
        return None
    code = _str_or_none(bank_info.get("code") or bank_info.get("bank_code"))
    name = _str_or_none(bank_info.get("name") or bank_info.get("bank_name"))
    country = _str_or_none(bank_info.get("country") or bank_info.get("bank_country"))
    if not code or not name or not country:
        return None
    return BrokerBankInfo(code=code, name=name, country=country)


def _owner_from_taxpayer(taxpayer: Mapping[str, Any] | Form270Owner | None) -> Form270Owner | None:
    if isinstance(taxpayer, Form270Owner):
        return taxpayer
    if not taxpayer:
        return None
    fio1 = _str_or_none(taxpayer.get("fio1"))
    fio2 = _str_or_none(taxpayer.get("fio2"))
    fio3 = _str_or_none(taxpayer.get("fio3"))
    iin = _str_or_none(taxpayer.get("iin") or taxpayer.get("taxpayerCode"))
    if fio1 and fio2 and iin:
        return Form270Owner(fio1=fio1, fio2=fio2, fio3=fio3 or "", iin=iin)
    return None


def _taxpayer_mapping(taxpayer: Mapping[str, Any] | Form270Owner | None) -> dict[str, Any]:
    if isinstance(taxpayer, Form270Owner):
        return {"fio1": taxpayer.fio1, "fio2": taxpayer.fio2, "fio3": taxpayer.fio3, "iin": taxpayer.iin}
    return dict(taxpayer or {})


def _source_tuple(owner: Form270Owner | None, source: str) -> tuple[str, str, str]:
    if owner is None:
        return source, "", ""
    return source, owner.iin, owner.full_name


def _sum_positive(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    table: str,
    flags: set[str] | None = None,
    exclude_flags: set[str] | None = None,
    tax_exchange: str | None = None,
) -> Decimal:
    total = ZERO
    for row in rows:
        if row.get("table") != table:
            continue
        flag = _str_or_none(row.get("flag")) or ""
        if flags is not None and flag not in flags:
            continue
        if exclude_flags is not None and flag in exclude_flags:
            continue
        row_tax_exchange = _str_or_none(row.get("tax_exchange") or row.get("exchange")) or ""
        if tax_exchange is not None and row_tax_exchange.upper() != tax_exchange.upper():
            continue
        total += max(_decimal(row.get(field)), ZERO)
    return total


def _sum_then_floor(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    table: str,
    flags: set[str],
    country_is_kz: bool,
) -> Decimal:
    total = ZERO
    for row in rows:
        if row.get("table") != table:
            continue
        if (_str_or_none(row.get("flag")) or "") not in flags:
            continue
        row_is_kz = (_str_or_none(row.get("country")) or "").upper() == "KZ"
        if row_is_kz != country_is_kz:
            continue
        total += _decimal(row.get(field))
    return max(total, ZERO)


def _foreign_tax_credit(rows: Sequence[Mapping[str, Any]]) -> Decimal:
    """Return foreign-tax credit, pooling taxable dividends by country.

    ``rows`` are already filtered to one tax year by ``_build_application_01``.
    Preferential dividends reduce the taxable base through E.1/E.4, therefore
    neither their income nor their withholding may increase the credit limit.
    """

    total = ZERO
    dividend_groups: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        if row.get("table") == "Yearly Dividends" and "withhold_kzt" in row:
            if _is_preferential_dividend(row):
                continue
            country = _country_code_for_form(_str_or_none(row.get("country"))) or "UNKNOWN"
            values = dividend_groups.setdefault(country, {"amount_kzt": ZERO, "withhold_kzt": ZERO})
            values["amount_kzt"] += _decimal(row.get("amount_kzt"))
            values["withhold_kzt"] += _decimal(row.get("withhold_kzt"))
            continue
        tax = _decimal(row.get("tax_kzt"))
        tax_after_withhold = _decimal(row.get("tax_kzt_withhold"))
        if "tax_kzt_withhold" in row and tax > tax_after_withhold:
            total += tax - tax_after_withhold
    for values in dividend_groups.values():
        kazakhstan_tax_limit = max(values["amount_kzt"], ZERO) * Decimal("0.10")
        foreign_tax_withheld = max(-values["withhold_kzt"], ZERO)
        total += min(foreign_tax_withheld, kazakhstan_tax_limit)
    return total


def _is_preferential_dividend(row: Mapping[str, Any]) -> bool:
    flag = (_str_or_none(row.get("flag")) or "").casefold()
    return flag == "issuer_kz" or flag.startswith("preferential")


def _rate_lookup(tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[tuple[int, str], Decimal]:
    rates: dict[tuple[int, str], Decimal] = {}
    for sheet_name, date_field in (
        ("Dividends", "date"),
        ("Interest", "date"),
        ("Coupons", "date"),
        ("Fifo", "exit_date"),
        ("Positions", "date"),
    ):
        for row in tables.get(sheet_name, []):
            parsed_date = _parse_date(row.get(date_field))
            currency = _str_or_none(row.get("currency"))
            rate = _decimal(row.get("kzt_rate"))
            if parsed_date and currency and rate > ZERO:
                rates[(parsed_date.year, currency)] = rate
    for row in tables.get("CashBalances", []):
        year = _int_or_none(row.get("year"))
        currency = _str_or_none(row.get("currency"))
        cash = _decimal(row.get("ending_cash"))
        cash_kzt = _decimal(row.get("ending_cash_kzt"))
        if year and currency and cash != ZERO and cash_kzt != ZERO:
            rates[(year, currency)] = abs(cash_kzt / cash)
    return rates


def _signed_trade_amount(row: Mapping[str, Any]) -> Decimal:
    amount = abs(_decimal(row.get("amount_with_commission") or row.get("amount")))
    quantity = _decimal(row.get("quantity"))
    return amount if quantity > ZERO else -amount


def _asset_kind_code(row: Mapping[str, Any]) -> str:
    if _is_derivative_asset(row):
        return _reference_code_or_original(DERIVATIVE_ASSET_NAME, ASSET_TYPES_FILE) or DERIVATIVE_ASSET_CODE
    return _reference_code_or_original(SECURITIES_ASSET_NAME, ASSET_TYPES_FILE) or SECURITIES_ASSET_CODE


def _is_application_04_property_asset(row: Mapping[str, Any]) -> bool:
    if _is_excluded_security(row):
        return False
    if _is_derivative_asset(row):
        return True
    asset_type = str(row.get("asset_type") or row.get("Asset_Type") or "").lower()
    return any(
        token in asset_type
        for token in ("stock", "stocks", "share", "shares", "bond", "bonds", "treasury", "t-bill", "акци", "облига")
    )


def _is_derivative_asset(row: Mapping[str, Any]) -> bool:
    asset_type = str(row.get("asset_type") or row.get("Asset_Type") or "").lower()
    symbol = str(row.get("symbol") or row.get("Symbol") or "").upper()
    if asset_type.strip() == "forex":
        return False
    return (
        any(token in asset_type for token in ("option", "future", "futures", "derivative", "fx spot", "fx_spot", "currency", "опцион", "фьюч"))
        or ".FX" in symbol
    )


def _application_04_operation(row: Mapping[str, Any], quantity: Decimal) -> tuple[str, str | None]:
    trade_type = str(row.get("trade_type") or "").lower()
    if trade_type.startswith("corporate_action:"):
        action_type = trade_type.split(":", 1)[1]
        if action_type in {"merger", "merged", "exchange", "acquisition"}:
            operation = OPERATION_EXCHANGE_ACQUIRED if quantity > ZERO else OPERATION_EXCHANGE_DISPOSED
            return _trade_type_code_for_form(operation), None
        if action_type in {"spinoff", "spin_off", "enrolment_of_rights", "rights"}:
            return _trade_type_code_for_form(OPERATION_GRATUITOUS), None
        if action_type in {"maturity", "full_call", "redemption"} and _is_bond_asset(row):
            return _trade_type_code_for_form(OPERATION_OTHER), BOND_REDEMPTION_OTHER_TEXT
    operation = OPERATION_PURCHASE if quantity > ZERO else OPERATION_SALE
    return _trade_type_code_for_form(operation), None


def _is_bond_asset(row: Mapping[str, Any]) -> bool:
    asset_type = str(row.get("asset_type") or row.get("Asset_Type") or "").lower()
    return any(token in asset_type for token in ("bond", "bonds", "treasury", "t-bill", "облига"))


def _is_excluded_security(row: Mapping[str, Any]) -> bool:
    identifier = " ".join(str(value or "") for value in (row.get("isin"), row.get("symbol"), row.get("security_id")))
    upper = identifier.upper()
    return ".SWAP" in upper or ".REPO" in upper


def _is_forex(row: Mapping[str, Any]) -> bool:
    asset_type = str(row.get("asset_type") or row.get("Asset_Type") or "").lower()
    symbol = str(row.get("symbol") or row.get("Symbol") or "").upper()
    return asset_type in {"currency", "forex", "fx_spot", "cash"} or "FOREX" in asset_type or ".FX" in symbol


def _instrument_identifier(row: Mapping[str, Any]) -> str | None:
    for key in ("isin", "security_id", "symbol", "trade_id"):
        value = _str_or_none(row.get(key))
        if value:
            return value
    return None


def _country_from_row(row: Mapping[str, Any]) -> str | None:
    country = _str_or_none(row.get("country") or row.get("issuer_country"))
    if country:
        return country.upper()
    identifier = _instrument_identifier(row)
    if identifier and len(identifier) >= 2 and identifier[:2].isalpha():
        prefix = identifier[:2].upper()
        if prefix in COUNTRY_ISO3_BY_ISO2:
            return prefix
    return None


def _country_code_for_form(country: str | None) -> str:
    if not country:
        return ""
    normalized = country.upper()
    candidate = COUNTRY_ISO3_BY_ISO2.get(normalized, normalized)
    return _reference_code_or_original(candidate, COUNTRY_CODES_FILE)


def _currency_code_for_form(currency: str | None) -> str:
    return _reference_code_or_original(currency, CURRENCY_CODES_FILE)


def _trade_type_code_for_form(operation: str | None) -> str:
    return _reference_code_or_original(operation, TRADES_TYPES_FILE)


def _reference_code_or_original(value: str | None, filename: str) -> str:
    normalized = _normalized_reference_key(value)
    if not normalized:
        return ""
    return _reference_code_map(filename).get(normalized, normalized)


@lru_cache(maxsize=None)
def _reference_codes(filename: str) -> frozenset[str]:
    return frozenset(_reference_code_map(filename).values())


@lru_cache(maxsize=None)
def _reference_code_map(filename: str) -> dict[str, str]:
    path = REFERENCE_TEMPLATES_DIR / filename
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            rows = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(rows, list):
        return {}

    codes: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = _normalized_reference_key(row.get("code"))
        if not code:
            continue
        for key in ("code", "nameRu", "nameKk", "nameEn"):
            alias = _normalized_reference_key(row.get(key))
            if alias and alias != "NULL":
                codes.setdefault(alias, code)
    return codes


def _normalized_reference_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _broker_account_from_workbook_path(path: Path) -> tuple[str, str]:
    name = path.stem
    if name.endswith("_audit"):
        name = name[: -len("_audit")]
    if name.endswith("_audit_fixed"):
        name = name[: -len("_audit_fixed")]
    if "_" not in name:
        return "", name
    broker, account_id = name.split("_", 1)
    if broker == "freedom" and account_id.startswith("bank_"):
        return "freedom_bank", account_id[len("bank_") :]
    if broker == "freedom" and account_id.startswith("broker_"):
        return "freedom_broker", account_id[len("broker_") :]
    return broker, account_id


def _canonical_years_header(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return YEARS_HEADER_ALIASES.get(text, _snake_case(text))


def _snake_case(value: str) -> str:
    text = value.strip().replace(" ", "_").replace("-", "_")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return text.lower()


def _decimal(value: Any) -> Decimal:
    if _is_missing(value):
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return ZERO


def _int_or_none(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(Decimal(str(value)))
    except Exception:
        return None


def _str_or_none(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() == "nan"
    try:
        return bool(value != value)
    except Exception:
        return False


def _round_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _amount_or_none(value: Decimal) -> int | None:
    return None if value == ZERO else _round_int(value)


def _decimal_json(value: Decimal, *, places: int) -> int | float:
    quant = Decimal("1") if places <= 0 else Decimal("1").scaleb(-places)
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def _row_no(index: int) -> str:
    return f"{index:08d}"


def _parse_date(value: Any) -> datetime | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", ""), text.split(".")[0]):
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass
    return None


def _format_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")
