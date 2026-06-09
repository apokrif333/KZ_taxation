"""Reference table schemas."""

from __future__ import annotations

FX_RATE_COLUMNS = (
    "year",
    "currency",
    "average_annual_rate",
    "source",
    "source_date",
    "valid_from",
    "valid_to",
)

INSTRUMENT_COLUMNS = (
    "symbol",
    "description",
    "conid",
    "security_id",
    "underlying",
    "listing_exchange",
    "multiplier",
    "type",
    "code",
    "year",
    "expiry",
    "delivery_month",
    "strike",
    "issuer",
    "maturity",
    "cusip",
    "country",
    "isin",
    "figi",
    "issuer_country",
    "offshore_flag",
    "issuer_outside_kz_flag",
    "preferential_tax_flag",
    "source_broker",
    "source_account",
    "source_report",
    "as_of_date",
)

JURISDICTION_COLUMNS = (
    "country_code",
    "country_name",
    "preferential_tax_flag",
    "offshore_flag",
    "valid_from",
    "valid_to",
    "source",
    "source_date",
    "notes",
)

KASE_AIX_COLUMNS = (
    "isin",
    "ticker",
    "exchange",
    "listing_date",
    "delisting_date",
    "official_list_status",
    "liquidity_criteria_met",
    "regular_trading_criteria_met",
    "source_date",
    "source",
)
