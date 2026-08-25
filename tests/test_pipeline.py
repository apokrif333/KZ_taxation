from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.base import BrokerReport, ParseResult
from kztax270.brokers.registry import BrokerRegistry
from kztax270.canonical.schema import CanonicalDataset
from kztax270.config import AccountConfig, ProjectPaths
from kztax270.pipeline import AccountPipeline
from kztax270.reference.fx import AnnualFxRateProvider


class _RecordingAdapter:
    broker_code = "uploaded"

    def __init__(self, discovered_reports: list[BrokerReport]) -> None:
        self.discovered_reports = discovered_reports
        self.fx_provider = AnnualFxRateProvider({})
        self.discover_calls: list[tuple[Path, str]] = []
        self.parse_calls: list[tuple[BrokerReport, ...]] = []

    def discover_reports(self, raw_root: Path, account_id: str) -> list[BrokerReport]:
        self.discover_calls.append((raw_root, account_id))
        return self.discovered_reports

    def parse_reports(self, reports: list[BrokerReport], account_id: str) -> ParseResult:
        self.parse_calls.append(tuple(reports))
        dataset = CanonicalDataset.empty(self.broker_code, account_id)
        dataset.raw_totals.source_reports = [str(report.path) for report in reports]
        return ParseResult(
            broker=self.broker_code,
            account_id=account_id,
            reports=reports,
            dataset=dataset,
            raw_totals=dataset.raw_totals,
        )


class AccountPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = ProjectPaths(
            raw_data=Path("raw"),
            processed_data=Path("processed"),
            output_data=Path("output"),
            nbk_rates=Path("nb_rates.xlsx"),
            form270_template=Path("270-template.json"),
        )
        self.account = AccountConfig(broker="uploaded", account_id="ACCOUNT-1")

    def _pipeline(self, adapter: _RecordingAdapter, **kwargs: object) -> AccountPipeline:
        return AccountPipeline(self.paths, registry=BrokerRegistry({adapter.broker_code: adapter}), **kwargs)

    def test_run_account_discovers_reports_then_uses_shared_processing(self) -> None:
        discovered = BrokerReport(
            broker="uploaded",
            account_id="ACCOUNT-1",
            path=Path("raw/uploaded/discovered.csv"),
        )
        adapter = _RecordingAdapter([discovered])
        pipeline = self._pipeline(adapter)

        with (
            patch("kztax270.pipeline.ensure_nbk_rates_current"),
            patch("kztax270.pipeline.ensure_kase_aix_preferential_current"),
        ):
            result = pipeline.run_account(self.account, write_excel=False, write_json=False)

        self.assertEqual(adapter.discover_calls, [(self.paths.raw_data, self.account.account_id)])
        self.assertEqual(adapter.parse_calls, [(discovered,)])
        self.assertEqual(result.dataset.raw_totals.source_reports, [str(discovered.path)])
        self.assertEqual(result.dataset.tables["Reconciliation"], [])

    def test_run_reports_uses_uploaded_paths_without_discovery_and_runs_outputs(self) -> None:
        adapter = _RecordingAdapter([])
        workbook_writer = MagicMock()
        pipeline = self._pipeline(adapter, workbook_writer=workbook_writer)
        builder = MagicMock()
        builder.build_account_draft.return_value = {"fnoContent": {}}
        uploaded_paths = (Path("uploads/first.csv"), Path("uploads/second.csv"))

        with (
            patch("kztax270.pipeline.ensure_nbk_rates_current"),
            patch("kztax270.pipeline.ensure_kase_aix_preferential_current"),
            patch("kztax270.pipeline.Form270JsonBuilder", return_value=builder),
        ):
            result = pipeline.run_reports(
                self.account,
                uploaded_paths,
                tax_year=2025,
                taxpayer={"iin": "000000000001"},
            )

        self.assertEqual(adapter.discover_calls, [])
        self.assertEqual([report.path for report in adapter.parse_calls[0]], list(uploaded_paths))
        self.assertTrue(all(report.broker == self.account.broker for report in adapter.parse_calls[0]))
        self.assertTrue(all(report.account_id == self.account.account_id for report in adapter.parse_calls[0]))
        self.assertEqual(result.dataset.raw_totals.source_reports, [str(path) for path in uploaded_paths])
        self.assertEqual(result.workbook_path, Path("processed/uploaded_ACCOUNT-1_audit.xlsx"))
        workbook_writer.write.assert_called_once_with(result.dataset, result.workbook_path)
        builder.build_account_draft.assert_called_once_with(
            result.dataset,
            tax_year=2025,
            taxpayer={"iin": "000000000001"},
        )
        builder.save.assert_called_once_with(
            {"fnoContent": {}},
            Path("output/270_2025_uploaded_ACCOUNT-1.json"),
        )


if __name__ == "__main__":
    unittest.main()
