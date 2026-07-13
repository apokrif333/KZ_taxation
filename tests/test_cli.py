from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.cli import main
from kztax270.pipeline import AccountPipelineResult


class CliTests(unittest.TestCase):
    def test_run_account_creates_excel_without_form_year_or_json(self) -> None:
        result = AccountPipelineResult(
            dataset=CanonicalDataset.empty("exante", "HXR2208.001"),
            workbook_path=None,
            form_paths={},
            reconciliation_error_count=0,
        )
        pipeline = MagicMock()
        pipeline.run_account.return_value = result

        with (
            patch("kztax270.cli.AccountPipeline", return_value=pipeline),
            patch("kztax270.cli.InteractiveTransferInFifoResolver"),
        ):
            exit_code = main(["run-account", "exante", "HXR2208.001"])

        self.assertEqual(exit_code, 0)
        call = pipeline.run_account.call_args
        self.assertEqual(call.args[0].broker, "exante")
        self.assertEqual(call.args[0].account_id, "HXR2208.001")
        self.assertTrue(call.kwargs["write_excel"])
        self.assertFalse(call.kwargs["write_json"])
