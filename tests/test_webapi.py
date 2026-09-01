from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from conftest_imports import SRC  # noqa: F401
from kztax270.config import ProjectPaths
from kztax270.excel.joint_workbook import joint_workbook_path
from kztax270.front_pipeline import (
    DiscoveredAccount,
    FrontPipelineResult,
    MissingTransferBasis,
)
from kztax270.webapi.main import UPLOAD_CHUNK_SIZE, _save_upload, create_app
from kztax270.webapi.storage import JobStore, WebApiSettings


class _FakeFrontPipelineFactory:
    def __init__(self) -> None:
        self.instances: list[_FakeFrontPipeline] = []
        self.discover_calls: list[tuple[ProjectPaths, str]] = []
        self.run_calls: list[dict[str, object]] = []
        self.discover_error: Exception | None = None
        self.behaviors: list[str] = []

    def __call__(self, paths: ProjectPaths) -> _FakeFrontPipeline:
        instance = _FakeFrontPipeline(paths, self)
        self.instances.append(instance)
        return instance


class _FakeFrontPipeline:
    def __init__(self, paths: ProjectPaths, factory: _FakeFrontPipelineFactory) -> None:
        self.paths = paths
        self.factory = factory

    def discover_accounts(self, client_id: str) -> tuple[DiscoveredAccount, ...]:
        self.factory.discover_calls.append((self.paths, client_id))
        if self.factory.discover_error is not None:
            raise self.factory.discover_error
        client_root = self.paths.raw_data / "clients" / client_id
        grouped: dict[tuple[str, str], list[Path]] = {}
        for folder in sorted(client_root.iterdir(), key=lambda path: path.name.casefold()):
            reports = sorted(folder.iterdir(), key=lambda path: path.name.casefold())
            if folder.name.startswith("freedom_"):
                grouped[("freedom", folder.name.removeprefix("freedom_"))] = reports
                continue
            broker = folder.name
            if broker == "ib":
                for report in reports:
                    account_id = "U2" if report.name.casefold().startswith("u2") else "U1"
                    grouped.setdefault((broker, account_id), []).append(report)
            else:
                account_id = {
                    "exante": "EX1",
                    "tabys": "T1",
                    "tsifra": "C1",
                    "freedom_bank": "FB1",
                }[broker]
                grouped[(broker, account_id)] = reports
        return tuple(
            DiscoveredAccount(broker, account_id, tuple(paths))
            for (broker, account_id), paths in sorted(grouped.items())
        )

    def run(self, **kwargs: object) -> FrontPipelineResult:
        self.factory.run_calls.append(dict(kwargs))
        client_id = str(kwargs["client_id"])
        tax_year = int(kwargs["tax_year"])
        accounts = self.discover_accounts(client_id)
        behavior = self.factory.behaviors.pop(0) if self.factory.behaviors else "completed"
        individual_paths: list[Path] = []
        for account in accounts:
            path = self.paths.processed_data / f"{account.broker}_{account.account_id}_audit.xlsx"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"audit:{account.broker}:{account.account_id}".encode())
            individual_paths.append(path)

        missing = (
            MissingTransferBasis(
                transfer_date=date(2024, 8, 19),
                symbol="MVEU",
                isin="IE00B86MWN23",
                quantity=Decimal("507"),
                currency="EUR",
                destination_broker="ib",
                destination_account="U1",
                reason="missing_source",
            ),
        )
        if behavior == "missing":
            return FrontPipelineResult(
                client_id=client_id,
                tax_year=tax_year,
                discovered_accounts=accounts,
                individual_workbook_paths=tuple(individual_paths),
                joint_workbook_paths=(),
                final_merge_input_paths=(),
                merged_workbook_path=None,
                form270_paths=(),
                missing_transfer_basis=missing,
                used_approximate_transfer_basis=False,
                completed=False,
            )

        joint_ids = set(kwargs.get("joint_accounts", []))
        excluded_ids = set(kwargs.get("acc_not_included_for_merged", []))
        joint_paths: list[Path] = []
        merge_inputs: list[Path] = []
        for account, ordinary in zip(accounts, individual_paths, strict=True):
            final = ordinary
            if account.account_id in joint_ids:
                final = joint_workbook_path(ordinary)
                final.write_bytes(f"joint:{account.broker}:{account.account_id}".encode())
                joint_paths.append(final)
            if account.account_id not in excluded_ids:
                merge_inputs.append(final)
        merged = self.paths.processed_data / f"merged_{client_id}.xlsx"
        merged.write_bytes(b"merged")
        form = self.paths.output_data / f"270_{tax_year}_{client_id}_filled.json"
        form.parent.mkdir(parents=True, exist_ok=True)
        form.write_text('{"fnoYear": 2025}', encoding="utf-8")
        approximate = bool(kwargs.get("allow_approximate_transfer_basis"))
        return FrontPipelineResult(
            client_id=client_id,
            tax_year=tax_year,
            discovered_accounts=accounts,
            individual_workbook_paths=tuple(individual_paths),
            joint_workbook_paths=tuple(joint_paths),
            final_merge_input_paths=tuple(merge_inputs),
            merged_workbook_path=merged,
            form270_paths=(form,),
            missing_transfer_basis=missing if approximate else (),
            used_approximate_transfer_basis=approximate,
            completed=True,
        )


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.factory = _FakeFrontPipelineFactory()
        self.settings = WebApiSettings(
            job_root=self.root / "jobs",
            max_upload_bytes=32,
            max_files=3,
            max_job_files=6,
            pending_job_ttl_seconds=3600,
            job_ttl_seconds=900,
            cors_origins=("http://localhost:3000",),
            project_paths=ProjectPaths(
                nbk_rates=self.root / "reference" / "nb_rates.xlsx",
                reference_data=self.root / "reference",
                form270_template=self.root / "reference" / "270-template.json",
            ),
        )
        self.client_context = TestClient(
            create_app(self.settings, front_pipeline_factory=self.factory)
        )
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _create(self) -> dict[str, object]:
        response = self.client.post("/api/jobs")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _upload(
        self,
        job_id: str,
        *,
        broker: str = "ib",
        account_id: str | None = None,
        uploads: list[tuple[str, bytes]] | None = None,
    ):
        data = {"broker": broker}
        if account_id is not None:
            data["account_id"] = account_id
        values = uploads or [("u1-report.csv", b"report")]
        files = [("files", (name, content, "application/octet-stream")) for name, content in values]
        return self.client.post(f"/api/jobs/{job_id}/reports", data=data, files=files)

    def _discover(self, job_id: str):
        return self.client.post(f"/api/jobs/{job_id}/discover")

    def _process(self, job_id: str, **overrides: object):
        payload: dict[str, object] = {
            "tax_year": 2025,
            "taxpayer": {
                "fio1": "Ivanov",
                "fio2": "Ivan",
                "fio3": "Ivanovich",
                "iin": "123456789012",
            },
            "joint_accounts": [],
            "acc_not_included_for_merged": [],
            "form270_05": False,
            "allow_approximate_transfer_basis": False,
        }
        payload.update(overrides)
        return self.client.post(f"/api/jobs/{job_id}/process", json=payload)

    def _ready_job(self) -> str:
        job_id = str(self._create()["job_id"])
        self.assertEqual(self._upload(job_id).status_code, 200)
        self.assertEqual(self._discover(job_id).status_code, 200)
        return job_id

    def test_health_is_cheap(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.json(), {"status": "ok", "service": "qcm-tax-270"})
        self.assertEqual(self.factory.instances, [])

    def test_config_only_exposes_front_pipeline_brokers_and_limits(self) -> None:
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        brokers = {item["code"]: item for item in data["brokers"]}
        self.assertEqual(set(brokers), {"ib", "exante", "tabys", "tsifra", "freedom", "freedom_bank"})
        self.assertEqual(brokers["ib"]["account_id_mode"], "auto")
        self.assertEqual(brokers["freedom"]["account_id_mode"], "manual")
        self.assertEqual(brokers["freedom"]["upload_extensions"], [".xlsx"])
        self.assertEqual(data["max_job_files"], 6)
        self.assertEqual(data["pending_job_ttl_seconds"], 3600)

    def test_create_job_is_empty_and_collecting(self) -> None:
        created = self._create()
        self.assertEqual(created["status"], "collecting")
        root = self.settings.job_root / str(created["job_id"])
        self.assertEqual(list((root / "input" / "clients").iterdir())[0].name[:4], "job_")
        self.assertEqual(self.factory.instances, [])

    def test_openapi_declares_report_files_as_multipart_binary(self) -> None:
        openapi = self.client.get("/openapi.json").json()
        request_schema = openapi["paths"]["/api/jobs/{job_id}/reports"]["post"]["requestBody"]["content"][
            "multipart/form-data"
        ]["schema"]
        if "$ref" in request_schema:
            request_schema = openapi["components"]["schemas"][request_schema["$ref"].rsplit("/", 1)[-1]]
        files = request_schema["properties"]["files"]
        self.assertEqual(files["type"], "array")
        self.assertEqual(files["items"]["type"], "string")
        self.assertEqual(files["items"]["format"], "binary")

    def test_repeated_batches_and_auto_ib_account_without_account_id(self) -> None:
        job_id = str(self._create()["job_id"])
        first = self._upload(job_id, uploads=[("u1-first.csv", b"one")])
        second = self._upload(job_id, uploads=[("u2-second.csv", b"two")])
        self.assertEqual(first.json()["total_files"], 1)
        self.assertEqual(second.json()["total_files"], 2)
        discovered = self._discover(job_id).json()
        self.assertEqual(
            [(item["broker"], item["account_id"], item["report_count"]) for item in discovered["accounts"]],
            [("ib", "U1", 1), ("ib", "U2", 1)],
        )

    def test_freedom_requires_account_id_and_auto_brokers_reject_it(self) -> None:
        job_id = str(self._create()["job_id"])
        missing = self._upload(job_id, broker="freedom", uploads=[("report.xlsx", b"one")])
        forbidden = self._upload(job_id, broker="ib", account_id="U1")
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["detail"]["code"], "account_id_required")
        self.assertEqual(forbidden.status_code, 422)
        self.assertEqual(forbidden.json()["detail"]["code"], "account_id_not_allowed")

    def test_invalid_manual_account_id_is_rejected(self) -> None:
        job_id = str(self._create()["job_id"])
        response = self._upload(job_id, broker="freedom", account_id="../bad", uploads=[("r.xlsx", b"x")])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "invalid_account_id")

    def test_multiple_freedom_accounts_use_separate_front_pipeline_folders(self) -> None:
        job_id = str(self._create()["job_id"])
        self.assertEqual(
            self._upload(job_id, broker="freedom", account_id="759023", uploads=[("a.xlsx", b"a")]).status_code,
            200,
        )
        self.assertEqual(
            self._upload(job_id, broker="freedom", account_id="998877", uploads=[("b.xlsx", b"b")]).status_code,
            200,
        )
        record = self.client.app.state.job_store.get(job_id)
        self.assertIsNotNone(record)
        folders = {path.parent.name for path in record.uploads.values()}
        self.assertEqual(folders, {"freedom_759023", "freedom_998877"})
        accounts = self._discover(job_id).json()["accounts"]
        self.assertEqual({item["account_id"] for item in accounts}, {"759023", "998877"})

    def test_upload_limits_and_extension_validation(self) -> None:
        cases = [
            ([('wrong.xlsx', b'x')], 415, "unsupported_file_type"),
            ([('large.csv', b'x' * 33)], 413, "file_too_large"),
            ([('1.csv', b'1'), ('2.csv', b'2'), ('3.csv', b'3'), ('4.csv', b'4')], 413, "too_many_files"),
        ]
        for uploads, status, code in cases:
            with self.subTest(code=code):
                job_id = str(self._create()["job_id"])
                response = self._upload(job_id, uploads=uploads)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["detail"]["code"], code)

    def test_total_job_file_limit(self) -> None:
        job_id = str(self._create()["job_id"])
        for batch in range(2):
            uploads = [(f"{batch}-{index}.csv", f"{batch}-{index}".encode()) for index in range(3)]
            self.assertEqual(self._upload(job_id, uploads=uploads).status_code, 200)
        response = self._upload(job_id, uploads=[("extra.csv", b"extra")])
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["code"], "too_many_job_files")

    def test_safe_filenames_traversal_and_duplicate_names(self) -> None:
        job_id = str(self._create()["job_id"])
        response = self._upload(
            job_id,
            uploads=[("../../report.csv", b"one"), ("report.csv", b"two")],
        )
        self.assertEqual(response.status_code, 200)
        record = self.client.app.state.job_store.get(job_id)
        names = sorted(path.name for path in record.uploads.values())
        self.assertEqual(names, ["report.csv", "report__2.csv"])
        self.assertFalse((self.root / "report.csv").exists())

    def test_duplicate_binary_upload_is_rejected_without_removing_original(self) -> None:
        job_id = str(self._create()["job_id"])
        self.assertEqual(self._upload(job_id, uploads=[("first.csv", b"same")]).status_code, 200)
        response = self._upload(job_id, uploads=[("renamed.csv", b"same")])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "duplicate_report")
        record = self.client.app.state.job_store.get(job_id)
        self.assertEqual(len(record.uploads), 1)
        self.assertTrue(next(iter(record.uploads.values())).exists())

    def test_uploaded_report_can_be_deleted_before_processing(self) -> None:
        job_id = str(self._create()["job_id"])
        uploaded = self._upload(job_id, uploads=[("bad.csv", b"bad"), ("good.csv", b"good")]).json()
        report_id = uploaded["reports"][0]["report_id"]
        record = self.client.app.state.job_store.get(job_id)
        self.assertIsNotNone(record)
        removed_path = record.uploads[report_id]

        response = self.client.delete(f"/api/jobs/{job_id}/reports/{report_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_files"], 1)
        self.assertNotIn(report_id, record.uploads)
        self.assertFalse(removed_path.exists())

    def test_upload_copy_is_chunked_and_hashes_while_streaming(self) -> None:
        class Upload:
            def __init__(self) -> None:
                self.chunks = [b"abc", b"def", b""]
                self.read_sizes: list[int] = []

            async def read(self, size: int) -> bytes:
                self.read_sizes.append(size)
                return self.chunks.pop(0)

        upload = Upload()
        destination = self.root / "streamed.csv"
        digest = asyncio.run(_save_upload(upload, destination, 20))  # type: ignore[arg-type]
        self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())
        self.assertEqual(upload.read_sizes, [UPLOAD_CHUNK_SIZE] * 3)

    def test_discover_returns_accounts_without_paths_and_does_not_process(self) -> None:
        job_id = str(self._create()["job_id"])
        self._upload(job_id, uploads=[("u1-a.csv", b"a"), ("u1-b.csv", b"b")])
        self._upload(job_id, broker="exante", uploads=[("ex.csv", b"ex")])
        response = self._discover(job_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "awaiting_options")
        self.assertEqual(sum(item["report_count"] for item in response.json()["accounts"]), 3)
        self.assertNotIn(str(self.root), response.text)
        self.assertEqual(self.factory.run_calls, [])

    def test_discover_returns_actionable_report_validation_error(self) -> None:
        job_id = str(self._create()["job_id"])
        self._upload(job_id, broker="exante", uploads=[("Custom_IEO1069.001 2023.csv", b"report")])
        self.factory.discover_error = ValueError(
            "Cannot detect account ID in exante report Custom_IEO1069.001 2023.csv"
        )

        response = self._discover(job_id)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "validation_error")
        self.assertEqual(
            response.json()["detail"]["message"],
            "В отчёте Exante «Custom_IEO1069.001 2023.csv» не удалось определить номер счёта.",
        )

    def test_process_passes_options_directly_to_front_pipeline(self) -> None:
        job_id = self._ready_job()
        response = self._process(
            job_id,
            joint_accounts=["U1"],
            acc_not_included_for_merged=["EX1"],
            form270_05=True,
            allow_approximate_transfer_basis=True,
        )
        self.assertEqual(response.status_code, 200)
        call = self.factory.run_calls[-1]
        self.assertEqual(call["tax_year"], 2025)
        self.assertEqual(call["taxpayer"]["iin"], "123456789012")
        self.assertEqual(call["joint_accounts"], ["U1"])
        self.assertEqual(call["acc_not_included_for_merged"], ["EX1"])
        self.assertTrue(call["form270_05"])
        self.assertTrue(call["allow_approximate_transfer_basis"])

    def test_processing_runs_through_threadpool(self) -> None:
        job_id = self._ready_job()
        calls: list[str] = []

        async def immediate(function, *args, **kwargs):
            calls.append(function.__name__)
            return function(*args, **kwargs)

        with patch("kztax270.webapi.main.run_in_threadpool", side_effect=immediate):
            response = self._process(job_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["run"])

    def test_missing_basis_keeps_raw_and_accepts_more_reports_then_completes(self) -> None:
        job_id = self._ready_job()
        self.factory.behaviors = ["missing", "completed"]
        first = self._process(job_id)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "needs_additional_reports")
        self.assertEqual(first.json()["missing_transfer_basis"][0]["quantity"], "507")
        record = self.client.app.state.job_store.get(job_id)
        self.assertTrue(record.workspace.input_dir.exists())

        self.assertEqual(self._upload(job_id, uploads=[("u2-source.csv", b"source")]).status_code, 200)
        self.assertEqual(self._discover(job_id).status_code, 200)
        stale = record.workspace.output_dir / "stale.json"
        stale.write_text("stale", encoding="utf-8")
        second = self._process(job_id)
        self.assertEqual(second.json()["status"], "completed")
        self.assertFalse(stale.exists())
        self.assertFalse(record.workspace.input_dir.exists())

    def test_approximate_retry_completes_without_more_uploads(self) -> None:
        job_id = self._ready_job()
        self.factory.behaviors = ["missing", "completed"]
        self.assertEqual(self._process(job_id).json()["status"], "needs_additional_reports")
        completed = self._process(job_id, allow_approximate_transfer_basis=True)
        self.assertEqual(completed.json()["status"], "completed")
        self.assertTrue(completed.json()["used_approximate_transfer_basis"])

    def test_completed_artifacts_cover_all_outputs_and_excluded_audit(self) -> None:
        job_id = str(self._create()["job_id"])
        self._upload(job_id, uploads=[("u1.csv", b"ib")])
        self._upload(job_id, broker="exante", uploads=[("ex.csv", b"ex")])
        self._discover(job_id)
        response = self._process(
            job_id,
            joint_accounts=["U1"],
            acc_not_included_for_merged=["EX1"],
        )
        artifacts = response.json()["artifacts"]
        kinds = [item["kind"] for item in artifacts]
        self.assertEqual(kinds.count("account_audit"), 2)
        self.assertIn("joint_audit", kinds)
        self.assertIn("merged_audit", kinds)
        self.assertIn("form270", kinds)
        excluded = next(item for item in artifacts if item.get("account_id") == "EX1")
        download = self.client.get(excluded["download_url"])
        self.assertEqual(download.content, b"audit:exante:EX1")
        self.assertNotIn(str(self.root), response.text)

    def test_artifact_download_whitelist_and_all_zip(self) -> None:
        job_id = self._ready_job()
        completed = self._process(job_id).json()
        artifact = completed["artifacts"][0]
        self.assertEqual(self.client.get(artifact["download_url"]).status_code, 200)
        arbitrary = self.client.get(f"/api/jobs/{job_id}/artifacts/../../input/secret")
        self.assertIn(arbitrary.status_code, {404, 405})
        response = self.client.get(f"/api/jobs/{job_id}/all")
        self.assertEqual(response.status_code, 200)
        archive = self.root / "download.zip"
        archive.write_bytes(response.content)
        with zipfile.ZipFile(archive) as handle:
            names = set(handle.namelist())
        self.assertEqual(names, {item["filename"] for item in completed["artifacts"]})

    def test_completed_job_is_frozen_and_raw_inputs_are_removed(self) -> None:
        job_id = self._ready_job()
        completed = self._process(job_id)
        self.assertEqual(completed.status_code, 200)
        record = self.client.app.state.job_store.get(job_id)
        self.assertFalse(record.workspace.input_dir.exists())
        upload = self._upload(job_id)
        process = self._process(job_id)
        self.assertEqual(upload.json()["detail"]["code"], "job_completed")
        self.assertEqual(process.json()["detail"]["code"], "job_completed")

    def test_delete_removes_pending_and_completed_jobs_idempotently(self) -> None:
        pending = str(self._create()["job_id"])
        completed = self._ready_job()
        self._process(completed)
        for job_id in (pending, completed):
            root = self.settings.job_root / job_id
            self.assertEqual(self.client.delete(f"/api/jobs/{job_id}").status_code, 200)
            self.assertEqual(self.client.delete(f"/api/jobs/{job_id}").status_code, 200)
            self.assertFalse(root.exists())

    def test_unknown_job_is_structured(self) -> None:
        response = self.client.post("/api/jobs/00000000-0000-0000-0000-000000000000/discover")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "job_not_found")

    def test_pending_and_completed_ttls_use_different_expirations(self) -> None:
        now = [1000.0]
        store = JobStore(self.root / "expiring", 10, 5, clock=lambda: now[0])
        settings = WebApiSettings(
            job_root=store.root,
            max_upload_bytes=32,
            max_files=3,
            max_job_files=6,
            pending_job_ttl_seconds=10,
            job_ttl_seconds=5,
            cors_origins=("http://localhost:3000",),
            project_paths=self.settings.project_paths,
        )
        factory = _FakeFrontPipelineFactory()
        with TestClient(create_app(settings, front_pipeline_factory=factory, job_store=store)) as client:
            pending = client.post("/api/jobs").json()["job_id"]
            pending_root = store.root / pending
            now[0] += 11
            expired = client.post(f"/api/jobs/{pending}/discover")
            self.assertEqual(expired.status_code, 410)
            self.assertEqual(expired.json()["detail"]["code"], "job_expired")
            self.assertFalse(pending_root.exists())

            now[0] = 2000.0
            completed = client.post("/api/jobs").json()["job_id"]
            client.post(
                f"/api/jobs/{completed}/reports",
                data={"broker": "ib"},
                files=[("files", ("u1.csv", b"data", "text/csv"))],
            )
            client.post(f"/api/jobs/{completed}/discover")
            result = client.post(
                f"/api/jobs/{completed}/process",
                json={
                    "tax_year": 2025,
                    "taxpayer": {"fio1": "A", "fio2": "B", "fio3": "", "iin": "1"},
                },
            ).json()
            completed_root = store.root / completed
            now[0] += 6
            expired = client.get(result["artifacts"][0]["download_url"])
            self.assertEqual(expired.status_code, 410)
            self.assertFalse(completed_root.exists())


if __name__ == "__main__":
    unittest.main()
