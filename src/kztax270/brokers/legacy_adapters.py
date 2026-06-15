"""Adapters around the existing legacy scripts.

These adapters deliberately import legacy modules only when parsing is requested.
That keeps the new architecture importable even when optional legacy dependencies
(pandas, openpyxl, lxml, financedatabase, pandasgui) are not installed.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Sequence

from kztax270.canonical.normalizer import normalize_legacy_tables

from .base import BrokerAdapterError, BrokerReport, ParseResult
from .discovery import DiscoveryRule, discover_raw_reports


class LegacyBrokerAdapter:
    broker_code: str
    legacy_module: str
    raw_folder: str

    def __init__(self, broker_code: str, legacy_module: str, raw_folder: str | None = None) -> None:
        self.broker_code = broker_code
        self.legacy_module = legacy_module
        self.raw_folder = raw_folder or broker_code

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        rule = DiscoveryRule(broker=self.raw_folder, account_id=account_id)
        reports = discover_raw_reports(raw_root, rule)
        return [
            BrokerReport(
                broker=self.broker_code,
                account_id=report.account_id,
                path=report.path,
                period_start=report.period_start,
                period_end=report.period_end,
                checksum=report.checksum,
                metadata=report.metadata,
            )
            for report in reports
        ]

    def parse_reports(self, reports: Sequence[BrokerReport], account_id: str) -> ParseResult:
        legacy_tables = self.run_legacy_pipeline(account_id)
        dataset = normalize_legacy_tables(self.broker_code, account_id, legacy_tables)
        if reports:
            dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(
            broker=self.broker_code,
            account_id=account_id,
            reports=reports,
            dataset=dataset,
            raw_totals=dataset.raw_totals,
        )

    def _module(self) -> Any:
        try:
            return importlib.import_module(self.legacy_module)
        except Exception as exc:
            raise BrokerAdapterError(
                f"Cannot import legacy module {self.legacy_module!r}. Install legacy dependencies or use a native parser."
            ) from exc

    def run_legacy_pipeline(self, account_id: str) -> dict[str, Any]:
        raise NotImplementedError


def _call_step(value: Any, step: Callable[..., Any], *args: Any) -> Any:
    return step(value, *args)


class InteractiveBrokersLegacyAdapter(LegacyBrokerAdapter):
    def __init__(self) -> None:
        super().__init__(broker_code="ib", legacy_module="legacy.CalcIBtrades", raw_folder="ib")

    def run_legacy_pipeline(self, account_id: str) -> dict[str, Any]:
        module = self._module()
        combined_dfs, dfs = module.prepare_trades_df(account_id)
        combined_dfs = module.transfers_in(combined_dfs, dfs, account_id)
        combined_dfs = module.corporate_actions(combined_dfs, dfs)
        dfs = module.fifo_calc(combined_dfs, dfs)
        dfs = module.transfers_out(dfs, account_id)
        dfs = module.add_currency(dfs)
        return module.final_preparations(dfs)


class ExanteLegacyAdapter(LegacyBrokerAdapter):
    def __init__(self) -> None:
        super().__init__(broker_code="exante", legacy_module="legacy.CalcExante", raw_folder="exante")

    def run_legacy_pipeline(self, account_id: str) -> dict[str, Any]:
        module = self._module()
        dfs = module.load_data(account_id)
        dfs = module.prep_trades_types(dfs)
        dfs = module.prep_transactions_df(dfs)
        dfs = module.prep_trades(dfs)
        dfs = module.prep_fininfo_df(dfs)
        dfs = module.add_fininfo_data(dfs)
        dfs = module.transfers_in(dfs, account_id)
        dfs = module.fifo_calc(dfs)
        dfs = module.add_currency(dfs)
        return module.final_preparations(dfs)


class TsifraLegacyAdapter(LegacyBrokerAdapter):
    def __init__(self) -> None:
        super().__init__(broker_code="tsifra", legacy_module="legacy.CalcTsifraBroker", raw_folder="tsifra")

    def run_legacy_pipeline(self, account_id: str) -> dict[str, Any]:
        module = self._module()
        dfs = module.get_full_data()
        dfs = module.prepare_trades_df(dfs)
        dfs = module.fifo_calc(dfs)
        dfs = module.add_currency(dfs)
        dfs = module.income_tax_comm(dfs)
        dfs = module.final_preparations(dfs)
        return module.prep_for_270form(dfs)
