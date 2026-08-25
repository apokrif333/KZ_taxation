from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.schema import CanonicalDataset
from kztax270.config import AccountConfig, ProjectPaths
from kztax270.pipeline import AccountPipelineResult
from kztax270.webapi.main import create_app
from kztax270.webapi.storage import JobStore, WebApiSettings


class _FakePipelineFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[ProjectPaths, AccountConfig, list[Path], int | None, bool]] = []

    def __call__(self, paths: ProjectPaths) -> _FakePipeline:
        return _FakePipeline(paths, self)


class _FakePipeline:
    def __init__(self, paths: ProjectPaths, factory: _FakePipelineFactory) -> None:
        self.paths = paths
        self.factory = factory

    def run_reports(
        self,
        account: AccountConfig,
        report_paths: list[Path],
        *,
        tax_year: int | None = None,
        taxpayer: dict[str, object] | None = None,
        write_excel: bool = True,
        write_json: bool = True,
    ) -> AccountPipelineResult:
        del taxpayer, write_excel
        self.factory.calls.append((self.paths, account, list(report_paths), tax_year, write_json))
        dataset = CanonicalDataset.empty(account.broker, account.account_id)
        dataset.tables["Trades"] = [{"trade_id": "1"}, {"trade_id": "2"}]
        dataset.tables["Instruments"] = [{"symbol": "TEST"}]
        dataset.warnings = [f"Warning for {report_paths[0]}"]
        dataset.tables["Reconciliation"] = [
            {
                "metric": "ending_cash",
                "severity": "error",
                "broker_value": "10.00",
                "canonical_value": "9.00",
                "difference": "-1.00",
                "tolerance": "0.01",
                "currency": "USD",
                "instrument_key": None,
                "year": tax_year,
                "source": str(report_paths[0]),
                "details": "Test reconciliation",
            }
        ]
        self.paths.processed_data.mkdir(parents=True, exist_ok=True)
        self.paths.output_data.mkdir(parents=True, exist_ok=True)
        workbook_path = self.paths.processed_data / f"{account.broker}_{account.account_id}_audit.xlsx"
        form_paths: dict[str, Path] = {}
        workbook_path.write_bytes(b"audit-workbook")
        if write_json:
            form_path = self.paths.output_data / f"270_{tax_year}_{account.broker}_{account.account_id}.json"
            form_path.write_text('{"fnoYear": 2025}', encoding="utf-8")
            form_paths[account.account_id] = form_path
        return AccountPipelineResult(
            dataset=dataset,
            workbook_path=workbook_path,
            form_paths=form_paths,
            reconciliation_error_count=1,
        )


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.factory = _FakePipelineFactory()
        self.settings = WebApiSettings(
            job_root=self.root / "jobs",
            max_upload_bytes=1024,
            max_files=2,
            job_ttl_seconds=900,
            cors_origins=("http://localhost:3000",),
            project_paths=ProjectPaths(
                nbk_rates=self.root / "reference" / "nb_rates.xlsx",
                reference_data=self.root / "reference",
                form270_template=self.root / "reference" / "270-template.json",
            ),
        )
        self.client_context = TestClient(create_app(self.settings, pipeline_factory=self.factory))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _post_job(
        self,
        *,
        broker: str = "ib",
        account_id: str | None = "U1234567",
        uploads: list[tuple[str, bytes]] | None = None,
        joint_account: bool = False,
    ):
        data = {
            "broker": broker,
            "tax_year": "2025",
            "joint_account": str(joint_account).lower(),
        }
        if account_id is not None:
            data["account_id"] = account_id
        upload_values = uploads or [("report.csv", b"report-data")]
        files = [("files", (name, content, "application/octet-stream")) for name, content in upload_values]
        return self.client.post("/api/jobs", data=data, files=files)

    def test_health_is_cheap_and_returns_service_status(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "qcm-tax-270"})
        self.assertEqual(self.factory.calls, [])

    def test_config_describes_native_parser_formats_and_limits(self) -> None:
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        brokers = {item["code"]: item for item in data["brokers"]}
        self.assertEqual(brokers["ib"]["upload_extensions"], [".csv"])
        self.assertEqual(brokers["freedom"]["upload_extensions"], [".xlsx"])
        self.assertFalse(brokers["freedom"]["account_id_optional"])
        self.assertNotIn("ib_legacy", brokers)
        self.assertEqual(data["max_upload_bytes"], 1024)
        self.assertEqual(data["max_files"], 2)

    def test_valid_multipart_upload_returns_summary_and_redacted_domain_data(self) -> None:
        response = self._post_job()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(
            data["summary"],
            {
                "operations": 2,
                "instruments": 1,
                "warnings": 1,
                "reconciliation_errors": 1,
            },
        )
        self.assertIn("[uploaded report]", data["warnings"][0])
        self.assertEqual(data["reconciliation"][0]["source"], "[uploaded report]")
        self.assertNotIn(str(self.root), response.text)

    def test_multiple_uploads_are_passed_to_run_reports(self) -> None:
        response = self._post_job(uploads=[("first.csv", b"one"), ("second.csv", b"two")])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.factory.calls[0][2]), 2)

    def test_unsupported_broker_has_structured_error(self) -> None:
        response = self._post_job(broker="unknown")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_broker")

    def test_unsupported_extension_is_rejected(self) -> None:
        response = self._post_job(uploads=[("report.xlsx", b"not-a-csv")])

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json()["detail"]["code"], "unsupported_file_type")
        self.assertEqual(list(self.settings.job_root.iterdir()), [])

    def test_file_size_limit_is_enforced_while_copying(self) -> None:
        response = self._post_job(uploads=[("report.csv", b"x" * 1025)])

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["code"], "file_too_large")
        self.assertEqual(list(self.settings.job_root.iterdir()), [])

    def test_file_count_limit_is_enforced(self) -> None:
        response = self._post_job(uploads=[("one.csv", b"1"), ("two.csv", b"2"), ("three.csv", b"3")])

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["code"], "too_many_files")
        self.assertEqual(self.factory.calls, [])

    def test_path_traversal_filename_is_never_used_as_a_path(self) -> None:
        response = self._post_job(uploads=[("../../escape.csv", b"safe")])

        self.assertEqual(response.status_code, 200)
        saved_path = self.factory.calls[0][2][0]
        self.assertEqual(saved_path.parent.name, "input")
        self.assertNotIn("escape", saved_path.name)
        self.assertFalse((self.root / "escape.csv").exists())

    def test_account_id_is_extracted_with_existing_ib_parser(self) -> None:
        report = b"Account Information,Header,Field Name,Field Value\nAccount Information,Data,Account,U7654321\n"
        response = self._post_job(account_id=None, uploads=[("report.csv", report)])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], "U7654321")
        self.assertEqual(self.factory.calls[0][1].account_id, "U7654321")

    def test_freedom_requires_account_id(self) -> None:
        response = self._post_job(
            broker="freedom",
            account_id=None,
            uploads=[("report.xlsx", b"placeholder")],
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "account_id_required")

    def test_joint_account_creates_a_fixed_half_share_audit_and_draft(self) -> None:
        def create_joint_workbook(source: Path) -> Path:
            output = source.with_name("ib_U1234567_joint_audit.xlsx")
            output.write_bytes(b"joint-audit-workbook")
            return output

        class _JointFormBuilder:
            def __init__(self, _template_path: Path) -> None:
                pass

            def build_processed_workbook_draft(self, _workbook_path: Path, **_kwargs: object) -> dict[str, int]:
                return {"fnoYear": 2025}

            def save(self, form: dict[str, int], output_path: Path) -> Path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text('{"fnoYear": 2025}', encoding="utf-8")
                return output_path

        with (
            patch("kztax270.webapi.main.create_joint_audit_workbook", side_effect=create_joint_workbook),
            patch("kztax270.webapi.main.Form270JsonBuilder", _JointFormBuilder),
        ):
            response = self._post_job(joint_account=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.factory.calls[0][4])
        created = response.json()
        self.assertEqual(self.client.get(created["downloads"]["audit"]).content, b"joint-audit-workbook")
        self.assertEqual(self.client.get(created["downloads"]["form270"]).json(), {"fnoYear": 2025})

    def test_audit_download_returns_generated_workbook(self) -> None:
        created = self._post_job().json()
        response = self.client.get(created["downloads"]["audit"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"audit-workbook")
        self.assertTrue(response.headers["content-type"].startswith("application/vnd.openxmlformats"))

    def test_form270_download_returns_generated_json(self) -> None:
        created = self._post_job().json()
        response = self.client.get(created["downloads"]["form270"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"fnoYear": 2025})

    def test_unknown_job_download_returns_structured_404(self) -> None:
        response = self.client.get("/api/jobs/00000000-0000-0000-0000-000000000000/audit")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "job_not_found")

    def test_delete_removes_job_and_is_idempotent(self) -> None:
        created = self._post_job().json()
        job_root = self.settings.job_root / created["job_id"]

        first = self.client.delete(f"/api/jobs/{created['job_id']}")
        second = self.client.delete(f"/api/jobs/{created['job_id']}")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(job_root.exists())
        self.assertEqual(self.client.get(created["downloads"]["audit"]).status_code, 404)

    def test_expired_job_is_cleaned_opportunistically(self) -> None:
        now = [1000.0]
        store = JobStore(self.root / "expiring-jobs", 10, clock=lambda: now[0])
        settings = WebApiSettings(
            job_root=store.root,
            max_upload_bytes=1024,
            max_files=2,
            job_ttl_seconds=10,
            cors_origins=("http://localhost:3000",),
            project_paths=self.settings.project_paths,
        )
        factory = _FakePipelineFactory()
        with TestClient(create_app(settings, pipeline_factory=factory, job_store=store)) as client:
            response = client.post(
                "/api/jobs",
                data={"broker": "ib", "tax_year": "2025", "account_id": "U1"},
                files=[("files", ("report.csv", b"data", "text/csv"))],
            )
            job_id = response.json()["job_id"]
            job_root = store.root / job_id
            now[0] += 11

            expired = client.get(f"/api/jobs/{job_id}/audit")

        self.assertEqual(expired.status_code, 404)
        self.assertFalse(job_root.exists())

    def test_uploaded_input_files_are_removed_after_success(self) -> None:
        response = self._post_job()

        self.assertEqual(response.status_code, 200)
        job_root = self.settings.job_root / response.json()["job_id"]
        self.assertFalse((job_root / "input").exists())
        self.assertTrue((job_root / "processed").is_dir())
        self.assertTrue((job_root / "output").is_dir())


if __name__ == "__main__":
    unittest.main()
