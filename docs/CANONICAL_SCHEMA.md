# Canonical Schema

The canonical workbook is defined in code at `src/kztax270/canonical/workbook_schema.py`. This document is the human-readable version.

## Sheets

1. `Instruments` - every financial instrument ever seen in the account reports.
2. `CorporateActions` - mergers, redemptions, buybacks, spin-offs and similar actions.
3. `Dividends` - gross, withholding tax and net dividend income.
4. `Transfers` - cash and security deposits, withdrawals and transfers.
5. `Trades` - normalized raw trades in execution order.
6. `Fifo` - realized FIFO rows with commission allocation.
7. `Positions` - end-of-year positions.
8. `Interest` - interest on broker cash balances.
9. `Coupons` - bond coupons.
10. `CashBalances` - ending cash by year/currency.
11. `Years_Results` - legacy-style yearly result blocks by income table, flag and currency.
12. `Unprocessed` - preserved rows that could not be fully processed and require user attention.
13. `Reconciliation` - raw-vs-canonical differences.

## Required Reference Tables

### `reference/fx_rates/nbk_average_annual_rates.csv`

Columns: `year`, `currency`, `average_annual_rate`, `source`, `source_date`, `valid_from`, `valid_to`.

### `reference/instruments/instrument_master.csv`

Columns: `symbol`, `description`, `conid`, `security_id`, `underlying`, `listing_exchange`, `multiplier`, `type`, `code`, `year`, `expiry`, `delivery_month`, `strike`, `issuer`, `maturity`, `cusip`, `country`, `isin`, `figi`, `issuer_country`, `offshore_flag`, `issuer_outside_kz_flag`, `preferential_tax_flag`, `source_broker`, `source_account`, `source_report`, `as_of_date`.

## New tax-rule fields

`Fifo.acquisition_cost_with_commission` is the acquisition/opening value including opening trade commission.

`Fifo.pnl` is tax FIFO P/L and deducts only the initiating/opening commission. Liquidation commission is kept separately in `Fifo.exit_commission` and in audit-only `Fifo.pnl_after_all_commissions`.

`Fifo.pnl_before_commission` is the raw price-difference result before any commissions.

`kzt_rate` fields must be annual average NBK official rates by income year. They must not be daily rates from legacy `Currency.xlsx`.

Instrument tax classification is explicit:

- `offshore_flag`
- `issuer_outside_kz_flag`
- `preferential_tax_flag`

### `reference/jurisdictions/jurisdictions.csv`

Columns: `country_code`, `country_name`, `preferential_tax_flag`, `offshore_flag`, `valid_from`, `valid_to`, `source`, `source_date`, `notes`.

### `reference/kase_aix/official_lists.csv`

Columns: `isin`, `ticker`, `exchange`, `listing_date`, `delisting_date`, `official_list_status`, `liquidity_criteria_met`, `regular_trading_criteria_met`, `source_date`, `source`.
