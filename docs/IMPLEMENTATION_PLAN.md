# Implementation Plan

## Phase 1 - Architecture baseline

Status: current iteration.

- Preserve `legacy/`.
- Add `src/kztax270` package boundaries.
- Define canonical workbook and reference schemas.
- Add reconciliation model and initial rule engine.
- Add Form270 JSON builder/merge/split scaffolding.
- Add CLI and config example.
- Add native IB parser with raw-total extraction and corrected FIFO commission columns.

## Phase 2 - Broker migration

- IB: finish opening lots, security transfers and corporate-action parity against legacy examples.
- Freedom: separate EN/KZ report layouts and normalize instrument fields.
- Exante: isolate transactions/trades/dividends/corporate actions.
- Tsifra: remove hardcoded account and transfer file assumptions.

## Phase 3 - Shared calculations

- Replace duplicated FIFO with `calculations.fifo` after parity tests.
- Add corporate action handlers with fixtures.
- Add dividend/coupon/interest gross-net-tax normalization.
- Add FX conversion using NBK reference table.

## Phase 4 - Reconciliation

- Extract raw totals per broker.
- Compare all required metrics with year/currency/instrument granularity.
- Fail pipeline on `error` unless explicitly overridden.
- Write reconciliation rows to Excel.

## Phase 5 - Tax engine

- Implement Form270 field mapping from canonical `Years_Results`.
- Add offshore and preferential instrument rules.
- Add joint-account ownership split before final Form270 output.
- Add multi-account client merge with deterministic conflict rules.

## Phase 6 - Operational hardening

- Add parser fixtures and golden Excel/JSON outputs.
- Add source checksums and processing manifest.
- Add logging and structured run reports.
- Add CI checks for import, unit tests, linting and schema drift.
