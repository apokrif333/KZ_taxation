from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.cli import _run_form270_config, _workbook_path_for_form, main
from kztax270.config import (
    Form270DefaultsConfig,
    Form270FillConfig,
    Form270JobConfig,
    Form270OwnerConfig,
    Form270RunConfig,
    ProjectPaths,
)
from kztax270.pipeline import AccountPipelineResult


class CliTests(unittest.TestCase):
    def test_form_workbook_list_is_merged_to_owner_named_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            processed = Path(tmp)
            config = Form270RunConfig(
                paths=ProjectPaths(processed_data=processed),
                defaults=Form270DefaultsConfig(tax_year=2024),
                banks={},
                forms=(),
            )
            form = Form270FillConfig(
                broker="merged",
                account_id="MULTI",
                owner=Form270OwnerConfig("Ашихмин", "Алексей", "", "000000000001"),
                workbooks=(Path("ib_U1_audit.xlsx"), Path("exante_E1_audit.xlsx")),
            )
            with patch("kztax270.cli.merge_audit_workbooks") as merge:
                path = _workbook_path_for_form(config, form)

        self.assertEqual(path.name, "merged_Алексей_Ашихмин.xlsx")
        merge.assert_called_once_with(
            (processed / "ib_U1_audit.xlsx", processed / "exante_E1_audit.xlsx"),
            processed / "merged_Алексей_Ашихмин.xlsx",
        )

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

    def test_run_270_executes_excel_merge_joint_excel_and_json_jobs(self) -> None:
        result = AccountPipelineResult(
            dataset=CanonicalDataset.empty("ib", "U1"),
            workbook_path=Path("data/processed/ib_U1_audit.xlsx"),
            form_paths={},
            reconciliation_error_count=0,
        )
        jobs = (
            Form270JobConfig(mode="excel", broker="ib", account_id="U1", job_id="audit"),
            Form270JobConfig(
                mode="merge_excel",
                owner=Form270OwnerConfig("Owner", "One", "", "000000000001"),
                workbooks=(Path("ib_U1_audit.xlsx"), Path("exante_E1_audit.xlsx")),
                job_id="merge-audit",
            ),
            Form270JobConfig(
                mode="joint_excel",
                workbook=Path("ib_U1_audit"),
                job_id="joint-audit",
            ),
            Form270JobConfig(
                mode="json",
                broker="ib",
                account_id="U1",
                owner=Form270OwnerConfig("Owner", "One", "", "000000000001"),
                workbook=Path("ib_U1_joint_audit"),
                job_id="form",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = Form270RunConfig(
                paths=ProjectPaths(processed_data=Path(tmp) / "processed", output_data=Path(tmp) / "output"),
                defaults=Form270DefaultsConfig(tax_year=2024),
                banks={},
                jobs=jobs,
            )
            pipeline = MagicMock()
            pipeline.run_account.return_value = result
            builder = MagicMock()
            builder.build_processed_workbook_draft.return_value = {"fnoContent": {}}
            with (
                patch("kztax270.cli.AccountPipeline", return_value=pipeline),
                patch("kztax270.cli.InteractiveTransferInFifoResolver"),
                patch("kztax270.cli.Form270JsonBuilder", return_value=builder),
                patch("kztax270.cli.merge_audit_workbooks") as merge,
                patch(
                    "kztax270.cli.create_joint_audit_workbook",
                    return_value=config.paths.processed_data / "ib_U1_joint_audit.xlsx",
                ) as joint,
            ):
                exit_code = _run_form270_config(config)

        self.assertEqual(exit_code, 0)
        self.assertEqual(pipeline.run_account.call_count, 1)
        self.assertEqual(merge.call_count, 1)
        joint.assert_called_once_with(config.paths.processed_data / "ib_U1_audit.xlsx")
        self.assertEqual(builder.build_processed_workbook_draft.call_count, 1)
        self.assertEqual(builder.save.call_count, 1)
        self.assertEqual(
            builder.build_processed_workbook_draft.call_args_list[0].args[0].name,
            "ib_U1_joint_audit.xlsx",
        )
        self.assertFalse(builder.build_processed_workbook_draft.call_args_list[0].kwargs["split"])
