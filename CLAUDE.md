# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`kztax270` is an ETL pipeline that transforms raw broker reports from Interactive Brokers (IB), Exante, Freedom Finance, and Tsifra (Цифра) into Kazakhstan Form 270 tax declaration drafts (JSON) and Excel audit workbooks.

### Pipeline contract

1. Raw broker files arrive in `data/raw/{broker}/`. Processing is always for **one specified broker account**.
2. All reports for that account are discovered, parsed, and normalised into a **single Excel audit workbook** covering all years.
3. Every broker must produce **identical sheet structure** in the audit workbook (canonical schema) so workbooks from different brokers are directly comparable.
4. The normalised dataset drives **Form 270.00 JSON** generation from `data/templates/270 new template.json`.
5. Multiple account JSONs are **merged** into one final Form 270.00 per client.
6. **Joint accounts**: one audit workbook → two Form 270.00 JSONs split by ownership ratio.

### Legacy code

`legacy/` contains ~80% working business logic (the original standalone scripts). It is the **source of truth** for tax calculation rules and broker parsing behaviour. When implementing features not yet covered in `src/`, port logic from the matching legacy script rather than inventing a new approach. Do not extend `legacy/` — migrate to `src/`.

### DRY mandate

Shared logic (FIFO cost basis, dividend/interest accounting, corporate actions, FX conversion, income categorisation) must live in `src/kztax270/calculations/` or `src/kztax270/utils/`, **never duplicated per broker**. Any function usable by ≥2 brokers goes into a shared module before it gets a second copy.

### Planned reference modules (not yet implemented)

| Path | Contents |
|------|----------|
| `reference/jurisdictions` | Per-instrument country + offshore/preferential-tax flags, effective date, source. Populated from broker reports; gaps filled manually. |
| `reference/kase_aix` | Snapshots of KASE/AIX official instrument lists: ISIN, ticker, exchange, listing/delisting dates, liquidity criteria, source date. Downloaded from AIX/KASE to determine preferential-tax eligibility. |

## Setup & Commands

```bash
# Install (editable)
pip install -e ".[dev]"

# Run linter
ruff check src tests

# Run tests
pytest

# Run a single test file
pytest tests/path/to/test_file.py

# Run a single test
pytest tests/path/to/test_file.py::test_function_name

# Type check
mypy src
```

## CLI Commands

```bash
# Bootstrap reference CSVs (run once per project)
kztax270 init-reference --root reference

# Fetch/update NBK FX rates
kztax270 update-nbk-rates --path data/nb_rates.xlsx

# Run one broker account (Excel audit only)
kztax270 run-account ib U1717377 --form-year 2024 --no-json

# Run one broker account (Excel + Form 270 JSON)
kztax270 run-account ib U1717377 --form-year 2024

# Run all accounts for a configured client
kztax270 run-client configs/my_client.toml client_demo
```

See `configs/accounts.example.toml` for the client config format (paths, taxpayer metadata, account list with optional joint-owner splits).

## Architecture

### Data Flow

```
Raw broker files (data/raw/)
  → BrokerParser.discover_reports() + parse_reports()
  → CanonicalDataset (tables dict: sheet_name → list[Record])
  → ReconciliationEngine (adds "Reconciliation" table)
  → ExcelAuditWorkbookWriter → data/processed/<broker>_<id>_audit.xlsx
  → Form270JsonBuilder → data/output/270_<year>_<broker>_<id>.json
  → merge_form270_jsons() → 270_<year>_<client>_merged.json
```

### Key Modules

| Path | Role |
|------|------|
| `src/kztax270/cli.py` | Argument parsing, entry point |
| `src/kztax270/pipeline.py` | `AccountPipeline` and `ClientPipeline` — orchestrate the full flow |
| `src/kztax270/config.py` | `ProjectPaths`, `AccountConfig`, `ClientConfig`, TOML loading |
| `src/kztax270/canonical/schema.py` | Core data types: `CanonicalDataset`, `Record`/`Table`/`Tables`, `Instrument`, `MoneyAmount` |
| `src/kztax270/brokers/` | One file per broker (`ib.py`, `exante.py`, `freedom.py`, `tsifra.py`). All implement the `BrokerParser` Protocol from `base.py`. `registry.py` maps broker codes to parsers. |
| `src/kztax270/calculations/` | `fifo.py` (position cost basis), `income.py` (dividend/interest), `corporate_actions.py`, `tax_rules.py` (KZ-specific tax logic) |
| `src/kztax270/reference/` | `fx.py` (FX rate lookups), `nbk.py` (NBK website scraper), `repositories.py` (CSV-backed stores), `schemas.py` |
| `src/kztax270/form270/` | `json_builder.py` (builds Form 270 JSON from dataset), `merge.py` (combines per-account JSONs), `split.py` (splits joint accounts by ownership ratio) |
| `src/kztax270/excel/audit_workbook.py` | Writes `CanonicalDataset.tables` to a multi-sheet xlsx |
| `src/kztax270/reconciliation/` | Cross-checks computed totals against broker-reported totals |
| `src/kztax270/transfers.py` | Resolves transfer-in cost basis via FIFO interactively |
| `legacy/` | Old standalone scripts; replaced by the `src/` package — do not extend |

### Adding a New Broker

1. Create `src/kztax270/brokers/<name>.py` implementing `BrokerParser` Protocol (`broker_code`, `discover_reports`, `parse_reports`).
2. Register it in `src/kztax270/brokers/registry.py` → `default_registry()`.
3. `parse_reports` must return a `ParseResult` whose `dataset.tables` uses the canonical sheet names expected by `ExcelAuditWorkbookWriter` and `Form270JsonBuilder`.

### Reference Data

`reference/` directory contains CSV files seeded by `kztax270 init-reference`. These store instrument metadata overrides (offshore flags, preferential tax status, country codes) that cannot be reliably inferred from broker reports alone. `ReferenceDataStore` in `reference/repositories.py` reads/writes them.

NBK FX rates are fetched from the National Bank of Kazakhstan website and cached in `data/nb_rates.xlsx`.
