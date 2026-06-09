# Architecture

## Boundary rules

1. Broker-specific raw parsing lives in `kztax270.brokers`.
2. Shared financial logic lives in `kztax270.calculations`.
3. Reference data access lives in `kztax270.reference`.
4. Form 270 JSON generation lives in `kztax270.form270`.
5. Excel output is only a presentation/audit layer and must not contain hidden business logic.
6. `legacy/` remains callable through lazy adapters until each broker parser is migrated.

## Migration strategy

The legacy scripts currently combine raw parsing, enrichment, FIFO, tax aggregation and Excel writing in one flow. The migration path is intentionally incremental:

1. Wrap each legacy script with an adapter and normalize its resulting tables to canonical sheets.
2. Migrate one broker at a time to native parsers. IB native parsing has started in `kztax270.brokers.ib`.
3. Extract duplicated functions into shared modules, starting with FIFO, FX conversion, dividend tax gross/net handling, and corporate-action mechanics.
4. Replace broker-specific Excel writers with `ExcelAuditWorkbookWriter`.
5. Move raw-total extraction into parser adapters and feed `ReconciliationEngine`.
6. Implement reference updaters for NBK, KASE and AIX snapshots with persisted source metadata.
7. Implement tax rules against canonical tables only.

## Data contracts

The stable contract is `CanonicalDataset` plus `CANONICAL_WORKBOOK_SHEETS`. Broker parsers may keep extra source columns, but required canonical columns must exist in every account workbook.

## Error policy

Reconciliation `error` means the generated workbook cannot be trusted for filing until reviewed. `warning` means the workbook can be reviewed manually, usually because the broker did not provide a directly comparable metric or the mismatch is not position/cash-critical. `info` means values are within tolerance.

IB-specific note: `realized_pl` may warn until opening lots, security transfers and all corporate actions are fully modeled for tax FIFO. Do not hide this mismatch; it is useful audit information.
