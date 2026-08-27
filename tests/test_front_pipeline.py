from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.account_detection import DetectedReportMetadata, detect_report_account_id
from kztax270.brokers.ib import _canonical_transfer_rows
from kztax270.canonical.schema import CanonicalDataset
from kztax270.cli import _run_form270_config
from kztax270.config import (
    Form270DefaultsConfig,
    Form270JobConfig,
    Form270OwnerConfig,
    Form270RunConfig,
    ProjectPaths,
    load_form270_run_config,
)
from kztax270.front_pipeline import (
    DiscoveredAccount,
    FrontPipeline,
    FrontPipelineResult,
    GlobalTransferLedger,
    MissingTransferBasis,
)
from kztax270.pipeline import AccountPipelineResult
from kztax270.transfers import TransferInRequest


def _transfer(
    direction: str,
    transfer_date: str,
    quantity: str,
    *,
    symbol: str = "XYZ",
    isin: str | None = "US0000000001",
    price: str | None = None,
    asset_type: str = "Stock",
    source_report: str = "report.csv",
    opening_status: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "date": transfer_date,
        "transfer_type": "security",
        "direction": direction,
        "asset_type": asset_type,
        "symbol": symbol,
        "isin": isin,
        "currency": "USD",
        "quantity": quantity,
        "price": price,
        "enter_date": "2020-02-03 00:00:00" if price else None,
        "source_report": source_report,
    }
    if opening_status:
        row["_opening_lot_status"] = opening_status
    return row


def _account(broker: str, account_id: str) -> DiscoveredAccount:
    return DiscoveredAccount(broker, account_id, (Path(f"{broker}-{account_id}.report"),))


def _dataset(broker: str, account_id: str, transfers: list[dict[str, object]]) -> CanonicalDataset:
    dataset = CanonicalDataset.empty(broker, account_id)
    dataset.tables["Transfers"] = transfers
    return dataset


def _request(
    transfer_date: date,
    quantity: str,
    *,
    symbol: str = "XYZ",
    isin: str | None = "US0000000001",
    asset_type: str = "Stock",
    source_report: str = "report.csv",
) -> TransferInRequest:
    return TransferInRequest(
        transfer_date=transfer_date,
        symbol=symbol,
        isin=isin,
        quantity=Decimal(quantity),
        currency="USD",
        asset_type=asset_type,
        source_report=source_report,
    )


class FrontPipelineConfigTests(unittest.TestCase):
    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "form270.toml"
            path.write_text(
                '[paths]\nraw_data="raw"\nprocessed_data="processed"\noutput_data="output"\n'
                '[form270]\n' + body,
                encoding="utf-8",
            )
            return load_form270_run_config(path)

    def test_front_pipeline_config_parses_and_account_list_defaults_empty(self) -> None:
        config = self._load(
            '[[form270.jobs]]\nid="front-pipeline"\nclient_id="client_1"\ntax_year=2025\n'
            'fio1="Ivanov"\nfio2="Ivan"\nfio3=""\niin="123456789012"\n'
        )
        job = config.jobs[0]
        self.assertEqual(job.mode, "front_pipeline")
        self.assertEqual(job.client_id, "client_1")
        self.assertEqual(job.joint_accounts, ())
        self.assertEqual(job.acc_not_included_for_merged, ())

    def test_missing_or_unsafe_client_id_is_rejected(self) -> None:
        base = (
            '[[form270.jobs]]\nid="front-pipeline"\ntax_year=2025\n'
            'fio1="Ivanov"\nfio2="Ivan"\niin="123456789012"\n'
        )
        with self.assertRaises(ValueError):
            self._load(base)
        with self.assertRaises(ValueError):
            self._load(base + 'client_id="../client"\n')

    def test_duplicate_joint_account_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._load(
                '[[form270.jobs]]\nid="front-pipeline"\nclient_id="client"\ntax_year=2025\n'
                'fio1="Ivanov"\nfio2="Ivan"\niin="123456789012"\n'
                'joint_accounts=["U1", "U1"]\n'
            )

    def test_excluded_merge_accounts_parse_and_duplicates_are_rejected(self) -> None:
        config = self._load(
            '[[form270.jobs]]\nid="front-pipeline"\nclient_id="client"\ntax_year=2025\n'
            'fio1="Ivanov"\nfio2="Ivan"\niin="123456789012"\n'
            'acc_not_included_for_merged=["U9871844"]\n'
        )
        self.assertEqual(config.jobs[0].acc_not_included_for_merged, ("U9871844",))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self._load(
                '[[form270.jobs]]\nid="front-pipeline"\nclient_id="client"\ntax_year=2025\n'
                'fio1="Ivanov"\nfio2="Ivan"\niin="123456789012"\n'
                'acc_not_included_for_merged=["U1", "U1"]\n'
            )

    def test_approximate_transfer_basis_is_chosen_interactively_not_in_toml(self) -> None:
        with self.assertRaisesRegex(ValueError, "no longer supported"):
            self._load(
                '[[form270.jobs]]\nid="front-pipeline"\nclient_id="client"\ntax_year=2025\n'
                'fio1="Ivanov"\nfio2="Ivan"\niin="123456789012"\n'
                'allow_approximate_transfer_basis=false\n'
            )


class FrontPipelineDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = ProjectPaths(raw_data=root / "raw", processed_data=root / "processed", output_data=root / "output")
        self.client_root = self.paths.raw_data / "clients" / "client_1"
        self.client_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _file(self, folder: str, name: str) -> Path:
        path = self.client_root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        return path

    def test_detected_brokers_group_multiple_accounts_and_reports_deterministically(self) -> None:
        self._file("IB", "z.csv")
        self._file("IB", "a.csv")
        self._file("IB", "b.csv")
        self._file("ExAnTe", "e.csv")
        self._file("tabys", "t.pdf")
        self._file("TSIFRA", "c.xml")
        self._file("freedom bank", "fb.pdf")
        self._file("freedom_759023", "f.xlsx")
        self._file("freedom_998877", "g.xlsx")

        detected = {"a": "U1", "z": "U1", "b": "U2", "e": "E1", "t": "T1", "c": "C1", "fb": "FB1"}
        with (
            patch(
                "kztax270.front_pipeline.detect_report_metadata",
                side_effect=lambda _broker, path: DetectedReportMetadata(
                    detected.get(path.stem), date(2025, 12, 31)
                ),
            ),
        ):
            accounts = FrontPipeline(self.paths).discover_accounts("client_1")

        self.assertEqual(
            [(item.broker, item.account_id) for item in accounts],
            [
                ("exante", "E1"),
                ("freedom", "759023"),
                ("freedom", "998877"),
                ("freedom_bank", "FB1"),
                ("ib", "U1"),
                ("ib", "U2"),
                ("tabys", "T1"),
                ("tsifra", "C1"),
            ],
        )
        ib_u1 = next(item for item in accounts if item.broker == "ib" and item.account_id == "U1")
        self.assertEqual([path.name for path in ib_u1.report_paths], ["a.csv", "z.csv"])

    def test_freedom_empty_suffix_is_rejected(self) -> None:
        (self.client_root / "freedom_").mkdir()
        with self.assertRaisesRegex(ValueError, "freedom_<account_id>"):
            FrontPipeline(self.paths).discover_accounts("client_1")

    def test_freedom_bank_uses_iin_extracted_from_pdf_when_it_omits_account(self) -> None:
        from types import SimpleNamespace

        report = self.client_root / "freedom bank" / "report.pdf"
        report.parent.mkdir(parents=True)
        report.write_bytes(b"fixture")
        with patch(
            "kztax270.brokers.freedom_bank.parse_freedom_bank_pdf",
            return_value=SimpleNamespace(
                brokerage_account=None,
                iin="610716400096",
                period_end=date(2025, 12, 31),
            ),
        ):
            self.assertEqual(detect_report_account_id("freedom_bank", report), "610716400096")

    def test_freedom_bank_without_report_identity_is_rejected(self) -> None:
        from types import SimpleNamespace

        report = self.client_root / "freedom_bank" / "any-name.pdf"
        report.parent.mkdir(parents=True)
        report.write_bytes(b"fixture")
        with patch(
            "kztax270.brokers.freedom_bank.parse_freedom_bank_pdf",
            return_value=SimpleNamespace(brokerage_account=None, iin=None, period_end=date(2025, 12, 31)),
        ):
            self.assertIsNone(detect_report_account_id("freedom_bank", report))

    def test_same_account_id_under_two_brokers_is_ambiguous(self) -> None:
        self._file("ib", "a.csv")
        self._file("exante", "b.csv")
        with (
            patch(
                "kztax270.front_pipeline.detect_report_metadata",
                return_value=DetectedReportMetadata("SAME", date(2025, 12, 31)),
            ),
            self.assertRaisesRegex(ValueError, "ambiguous"),
        ):
            FrontPipeline(self.paths).discover_accounts("client_1")

    def test_unknown_joint_account_is_rejected(self) -> None:
        self._file("freedom_759023", "f.xlsx")
        with (
            patch(
                "kztax270.front_pipeline.detect_report_metadata",
                return_value=DetectedReportMetadata(None, date(2025, 12, 31)),
            ),
            self.assertRaisesRegex(ValueError, "Unknown joint"),
        ):
            FrontPipeline(self.paths).run(
                client_id="client_1",
                tax_year=2025,
                taxpayer={"iin": "1"},
                joint_accounts=("UNKNOWN",),
            )

    def test_partial_or_unknown_report_period_does_not_block_discovery(self) -> None:
        report = self._file("exante", "partial.csv")
        pipeline = FrontPipeline(self.paths)
        for period_end in (date(2025, 7, 9), None):
            with self.subTest(period_end=period_end), patch(
                "kztax270.front_pipeline.detect_report_metadata",
                return_value=DetectedReportMetadata("E1", period_end),
            ):
                self.assertEqual(
                    pipeline.discover_accounts("client_1"),
                    (DiscoveredAccount("exante", "E1", (report,)),),
                )


class GlobalTransferLedgerTests(unittest.TestCase):
    def test_synthetic_reconciliation_transfer_is_not_missing_basis(self) -> None:
        account = _account("freedom_bank", "610716400096")
        synthetic = _transfer(
            "in",
            "2025-01-01",
            "39",
            symbol="FRHCSPC.ETN",
            isin="KZX000002001",
        )
        synthetic["_synthetic_reconciliation_adjustment"] = True

        resolution = GlobalTransferLedger().resolve(
            [(account, _dataset("freedom_bank", "610716400096", [synthetic]))]
        )

        self.assertEqual(resolution.missing, ())

    def test_freedom_bank_gift_with_reported_price_is_not_missing_basis(self) -> None:
        account = _account("freedom_bank", "610716400096")
        gift = _transfer(
            "in",
            "2025-08-27",
            "35444",
            symbol="FRHCSPC.ETN",
            isin="KZX000002001",
            price="0.01",
        )
        gift["_broker_reported_transfer_basis"] = True

        resolution = GlobalTransferLedger().resolve(
            [(account, _dataset("freedom_bank", "610716400096", [gift]))]
        )

        self.assertEqual(resolution.missing, ())

    def test_in_memory_transfer_rows_retain_fifo_provenance_but_not_unrelated_fields(self) -> None:
        rows = _canonical_transfer_rows(
            [
                {
                    **_transfer("out", "2024-01-01", "10", price="2"),
                    "_transfer_id": "report:transfer:1",
                    "_opening_lot_status": "pending_transfer_out_fifo_cost_basis",
                    "_fifo_source_account": "A",
                    "_broker_reported_transfer_basis": True,
                    "_unrelated_internal": "discard",
                }
            ]
        )
        self.assertEqual(rows[0]["_transfer_id"], "report:transfer:1")
        self.assertEqual(rows[0]["_fifo_source_account"], "A")
        self.assertTrue(rows[0]["_broker_reported_transfer_basis"])
        self.assertNotIn("_unrelated_internal", rows[0])

    def test_exact_cross_broker_transfer_propagates_price_and_original_date(self) -> None:
        source = _account("ib", "U1")
        destination = _account("exante", "E1")
        resolution = GlobalTransferLedger().resolve(
            [
                (source, _dataset("ib", "U1", [_transfer("out", "2024-01-01", "10", price="12.5")])),
                (destination, _dataset("exante", "E1", [_transfer("in", "2024-01-02", "10")])),
            ]
        )

        self.assertEqual(resolution.missing, ())
        lots = resolution.resolver_for("exante", "E1")(_request(date(2024, 1, 2), "10"))
        assert lots is not None
        self.assertEqual(lots[0].price, Decimal("12.5"))
        self.assertEqual(lots[0].enter_date, datetime(2020, 2, 3))
        self.assertEqual(lots[0].source_broker, "ib")
        self.assertEqual(lots[0].source_account, "U1")

    def test_chain_a_to_b_to_c_uses_one_chronological_ledger(self) -> None:
        a = _account("ib", "A")
        b = _account("exante", "B")
        c = _account("tsifra", "C")
        resolution = GlobalTransferLedger().resolve(
            [
                (a, _dataset("ib", "A", [_transfer("out", "2024-01-01", "1000", price="7")])),
                (
                    b,
                    _dataset(
                        "exante",
                        "B",
                        [
                            _transfer("in", "2024-01-02", "1000"),
                            _transfer(
                                "out",
                                "2024-01-03",
                                "1000",
                                price="99",
                                opening_status="pending_transfer_out_fifo_cost_basis",
                            ),
                        ],
                    ),
                ),
                (c, _dataset("tsifra", "C", [_transfer("in", "2024-01-04", "1000")])),
            ]
        )

        self.assertEqual(resolution.missing, ())
        b_lots = resolution.resolver_for("exante", "B")(_request(date(2024, 1, 2), "1000"))
        c_lots = resolution.resolver_for("tsifra", "C")(_request(date(2024, 1, 4), "1000"))
        assert b_lots is not None and c_lots is not None
        self.assertEqual(c_lots[0].price, Decimal("7"))
        self.assertEqual(c_lots[0].enter_date, datetime(2020, 2, 3))
        self.assertEqual(c_lots[0].source_account, "A")

    def test_sale_between_transfers_consumes_the_original_fifo_lots(self) -> None:
        a = _account("ib", "A")
        b = _account("exante", "B")
        c = _account("tsifra", "C")
        a_dataset = _dataset(
            "ib",
            "A",
            [
                _transfer("out", "2024-01-01", "5", price="1"),
                _transfer("out", "2024-01-01", "5", price="2"),
            ],
        )
        b_dataset = _dataset(
            "exante",
            "B",
            [
                _transfer("in", "2024-01-02", "10"),
                _transfer(
                    "out",
                    "2024-01-04",
                    "5",
                    price="99",
                    opening_status="pending_transfer_out_fifo_cost_basis",
                ),
            ],
        )
        b_dataset.tables["Fifo"] = [
            {
                "exit_date": "2024-01-03 12:00:00",
                "exit_quantity": "5",
                "symbol": "XYZ",
                "isin": "US0000000001",
                "_opening_lot_status": "pending_transfer_out_fifo_cost_basis",
            }
        ]
        c_dataset = _dataset("tsifra", "C", [_transfer("in", "2024-01-05", "5")])

        resolution = GlobalTransferLedger().resolve([(a, a_dataset), (b, b_dataset), (c, c_dataset)])
        lots = resolution.resolver_for("tsifra", "C")(_request(date(2024, 1, 5), "5"))

        assert lots is not None
        self.assertEqual(lots[0].price, Decimal("2"))
        self.assertEqual(lots[0].source_account, "A")

    def test_quantity_mismatch_and_out_after_in_stay_structurally_unresolved(self) -> None:
        source = _account("ib", "U1")
        destination = _account("exante", "E1")
        mismatch = GlobalTransferLedger().resolve(
            [
                (source, _dataset("ib", "U1", [_transfer("out", "2024-01-01", "9", price="1")])),
                (destination, _dataset("exante", "E1", [_transfer("in", "2024-01-02", "10")])),
            ]
        )
        future = GlobalTransferLedger().resolve(
            [
                (source, _dataset("ib", "U1", [_transfer("out", "2024-01-03", "10", price="1")])),
                (destination, _dataset("exante", "E1", [_transfer("in", "2024-01-02", "10")])),
            ]
        )
        self.assertEqual(mismatch.missing[0].reason, "quantity_mismatch")
        self.assertEqual(future.missing[0].reason, "missing_source")

    def test_equally_close_sources_are_ambiguous_and_missing_is_deduplicated(self) -> None:
        destination = _account("tsifra", "D")
        destination_dataset = _dataset("tsifra", "D", [_transfer("in", "2024-01-02", "10")])
        resolution = GlobalTransferLedger().resolve(
            [
                (_account("ib", "A"), _dataset("ib", "A", [_transfer("out", "2024-01-01", "10", price="1")])),
                (_account("exante", "B"), _dataset("exante", "B", [_transfer("out", "2024-01-01", "10", price="2")])),
                (destination, destination_dataset),
                (destination, destination_dataset),
            ]
        )
        self.assertEqual(len(resolution.missing), 1)
        self.assertEqual(resolution.missing[0].reason, "ambiguous_source")

    def test_isin_is_preferred_and_debt_scaling_is_reused(self) -> None:
        destination = _account("tsifra", "D")
        resolution = GlobalTransferLedger().resolve(
            [
                (
                    _account("ib", "A"),
                    _dataset(
                        "ib",
                        "A",
                        [_transfer("out", "2024-01-01", "2000", price="98", asset_type="Bond")],
                    ),
                ),
                (
                    _account("exante", "B"),
                    _dataset(
                        "exante",
                        "B",
                        [_transfer("out", "2024-01-01", "200000", price="5", isin=None, asset_type="Bond")],
                    ),
                ),
                (
                    destination,
                    _dataset(
                        "tsifra",
                        "D",
                        [_transfer("in", "2024-01-02", "200000", asset_type="Bond")],
                    ),
                ),
            ]
        )
        lots = resolution.resolver_for("tsifra", "D")(
            _request(date(2024, 1, 2), "200000", asset_type="Bond")
        )
        assert lots is not None
        self.assertEqual(lots[0].quantity, Decimal("200000"))
        self.assertEqual(lots[0].price, Decimal("0.98"))
        self.assertEqual(lots[0].source_account, "A")


class FrontPipelineOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = ProjectPaths(
            raw_data=root / "raw",
            processed_data=root / "processed",
            output_data=root / "output",
            nbk_rates=root / "nb.xlsx",
            form270_template=root / "template.json",
        )
        self.paths.processed_data.mkdir(parents=True)
        self.paths.output_data.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _runner(self, datasets: dict[tuple[str, str], CanonicalDataset], actions: list[str] | None = None):
        def run(account: DiscoveredAccount, resolver):
            if actions is not None:
                actions.append(f"run:{account.account_id}:{resolver is not None}")
            path = self.paths.processed_data / f"{account.broker}_{account.account_id}_audit.xlsx"
            path.write_bytes(b"audit")
            return AccountPipelineResult(datasets[(account.broker, account.account_id)], path, {}, 0)

        return run

    def test_strict_missing_basis_writes_json_and_prevents_finalization(self) -> None:
        account = _account("ib", "U1")
        dataset = _dataset("ib", "U1", [_transfer("in", "2024-01-02", "10")])
        pipeline = FrontPipeline(self.paths, account_runner=self._runner({("ib", "U1"): dataset}))
        with patch.object(pipeline, "discover_accounts", return_value=(account,)):
            result = pipeline.run(client_id="client", tax_year=2025, taxpayer={"iin": "1"})

        self.assertFalse(result.completed)
        self.assertIsNone(result.merged_workbook_path)
        self.assertEqual(result.form270_paths, ())
        diagnostic = self.paths.processed_data / "client_missing_transfer_basis.json"
        self.assertTrue(diagnostic.exists())
        self.assertEqual(json.loads(diagnostic.read_text(encoding="utf-8"))["missing_transfer_basis"][0]["reason"], "missing_source")

    def test_joint_is_applied_after_full_quantity_resolution_and_only_joint_file_is_merged(self) -> None:
        actions: list[str] = []
        source = _account("ib", "U15272903 (Custom Consolidated)")
        destination = _account("exante", "E1")
        datasets = {
            ("ib", "U15272903 (Custom Consolidated)"): _dataset(
                "ib", "U15272903 (Custom Consolidated)", [_transfer("out", "2024-01-01", "1000", price="7")]
            ),
            ("exante", "E1"): _dataset("exante", "E1", [_transfer("in", "2024-01-02", "1000")]),
        }
        pipeline = FrontPipeline(self.paths, account_runner=self._runner(datasets, actions))
        builder = MagicMock()
        builder.build_processed_workbook_draft.return_value = {"fnoContent": {}}

        def joint(source_path: Path) -> Path:
            actions.append("joint")
            path = source_path.with_name("ib_U15272903 (Custom Consolidated)_joint_audit.xlsx")
            path.write_bytes(b"joint")
            return path

        def merge(inputs, output):
            actions.append("merge")
            output.write_bytes(b"merged")
            return output

        def save(_draft, output):
            output.write_text("{}", encoding="utf-8")
            return output

        builder.save.side_effect = save
        with (
            patch.object(pipeline, "discover_accounts", return_value=(source, destination)),
            patch("kztax270.front_pipeline.create_joint_audit_workbook", side_effect=joint) as create_joint,
            patch("kztax270.front_pipeline.merge_audit_workbooks", side_effect=merge) as merge_workbooks,
            patch("kztax270.front_pipeline.Form270JsonBuilder", return_value=builder),
        ):
            result = pipeline.run(
                client_id="client",
                tax_year=2025,
                taxpayer={"iin": "1"},
                joint_accounts=("U15272903",),
            )

        self.assertTrue(result.completed)
        self.assertEqual(
            actions[:4],
            [
                "run:U15272903 (Custom Consolidated):False",
                "run:E1:False",
                "run:U15272903 (Custom Consolidated):True",
                "run:E1:True",
            ],
        )
        self.assertEqual(actions[4:], ["joint", "merge"])
        create_joint.assert_called_once_with(
            self.paths.processed_data / "ib_U15272903 (Custom Consolidated)_audit.xlsx"
        )
        merge_inputs = merge_workbooks.call_args.args[0]
        self.assertEqual(
            [path.name for path in merge_inputs],
            ["ib_U15272903 (Custom Consolidated)_joint_audit.xlsx", "exante_E1_audit.xlsx"],
        )
        self.assertNotIn(
            self.paths.processed_data / "ib_U15272903 (Custom Consolidated)_audit.xlsx",
            result.final_merge_input_paths,
        )
        self.assertEqual(result.merged_workbook_path.name, "merged_client.xlsx")
        self.assertEqual(result.form270_paths[0].name, "270_2025_client_filled.json")
        builder.build_processed_workbook_draft.assert_called_once()
        self.assertEqual(builder.build_processed_workbook_draft.call_args.args[0], result.merged_workbook_path)

    def test_excluded_account_is_used_for_transfer_basis_but_not_merged(self) -> None:
        actions: list[str] = []
        source = _account("ib", "U9871844 (Custom Consolidated)")
        destination = _account("exante", "MOTHER")
        datasets = {
            ("ib", "U9871844 (Custom Consolidated)"): _dataset(
                "ib", "U9871844 (Custom Consolidated)", [_transfer("out", "2024-01-01", "10", price="7")]
            ),
            ("exante", "MOTHER"): _dataset("exante", "MOTHER", [_transfer("in", "2024-01-02", "10")]),
        }
        pipeline = FrontPipeline(self.paths, account_runner=self._runner(datasets, actions))
        builder = MagicMock()
        builder.build_processed_workbook_draft.return_value = {}
        builder.save.side_effect = lambda _draft, output: output.write_text("{}", encoding="utf-8") or output

        def merge(inputs, output):
            output.write_bytes(b"merged")
            return output

        with (
            patch.object(pipeline, "discover_accounts", return_value=(source, destination)),
            patch("kztax270.front_pipeline.merge_audit_workbooks", side_effect=merge) as merge_workbooks,
            patch("kztax270.front_pipeline.Form270JsonBuilder", return_value=builder),
        ):
            result = pipeline.run(
                client_id="client",
                tax_year=2025,
                taxpayer={"iin": "1"},
                acc_not_included_for_merged=("U9871844",),
            )

        self.assertTrue(result.completed)
        self.assertEqual(
            actions,
            [
                "run:U9871844 (Custom Consolidated):False",
                "run:MOTHER:False",
                "run:U9871844 (Custom Consolidated):True",
                "run:MOTHER:True",
            ],
        )
        self.assertEqual(
            [path.name for path in result.individual_workbook_paths],
            ["ib_U9871844 (Custom Consolidated)_audit.xlsx", "exante_MOTHER_audit.xlsx"],
        )
        self.assertEqual([path.name for path in result.final_merge_input_paths], ["exante_MOTHER_audit.xlsx"])
        merge_workbooks.assert_not_called()
        self.assertEqual(result.merged_workbook_path.read_bytes(), b"audit")
        self.assertEqual(builder.build_processed_workbook_draft.call_args.args[0], result.merged_workbook_path)

    def test_unknown_or_all_excluded_merge_accounts_are_rejected(self) -> None:
        account = _account("ib", "U1")
        pipeline = FrontPipeline(self.paths, account_runner=MagicMock())
        with patch.object(pipeline, "discover_accounts", return_value=(account,)):
            with self.assertRaisesRegex(ValueError, "Unknown acc_not_included_for_merged"):
                pipeline.run(
                    client_id="client",
                    tax_year=2025,
                    taxpayer={"iin": "1"},
                    acc_not_included_for_merged=("U2",),
                )
            with self.assertRaisesRegex(ValueError, "cannot exclude every"):
                pipeline.run(
                    client_id="client",
                    tax_year=2025,
                    taxpayer={"iin": "1"},
                    acc_not_included_for_merged=("U1",),
                )

    def test_one_account_still_gets_merged_copy_and_approximate_mode_completes(self) -> None:
        account = _account("ib", "U1")
        dataset = _dataset("ib", "U1", [_transfer("in", "2024-01-02", "10")])
        pipeline = FrontPipeline(self.paths, account_runner=self._runner({("ib", "U1"): dataset}))
        builder = MagicMock()
        builder.build_processed_workbook_draft.return_value = {}
        builder.save.side_effect = lambda _draft, output: output.write_text("{}", encoding="utf-8") or output
        with (
            patch.object(pipeline, "discover_accounts", return_value=(account,)),
            patch("kztax270.front_pipeline.Form270JsonBuilder", return_value=builder),
        ):
            result = pipeline.run(
                client_id="client",
                tax_year=2025,
                taxpayer={"iin": "1"},
                allow_approximate_transfer_basis=True,
            )
        self.assertTrue(result.completed)
        self.assertTrue(result.used_approximate_transfer_basis)
        self.assertTrue((self.paths.processed_data / "merged_client.xlsx").exists())

    def test_adding_source_account_and_rerunning_resolves_strict_basis(self) -> None:
        source = _account("ib", "U1")
        destination = _account("exante", "E1")
        destination_dataset = _dataset("exante", "E1", [_transfer("in", "2024-01-02", "10")])
        datasets = {
            ("ib", "U1"): _dataset("ib", "U1", [_transfer("out", "2024-01-01", "10", price="3")]),
            ("exante", "E1"): destination_dataset,
        }
        pipeline = FrontPipeline(self.paths, account_runner=self._runner(datasets))
        with patch.object(pipeline, "discover_accounts", return_value=(destination,)):
            first = pipeline.run(client_id="client", tax_year=2025, taxpayer={"iin": "1"})
        self.assertFalse(first.completed)

        builder = MagicMock()
        builder.build_processed_workbook_draft.return_value = {}
        builder.save.side_effect = lambda _draft, output: output.write_text("{}", encoding="utf-8") or output

        def merge(_inputs, output):
            output.write_bytes(b"merged")
            return output

        with (
            patch.object(pipeline, "discover_accounts", return_value=(source, destination)),
            patch("kztax270.front_pipeline.merge_audit_workbooks", side_effect=merge),
            patch("kztax270.front_pipeline.Form270JsonBuilder", return_value=builder),
        ):
            second = pipeline.run(client_id="client", tax_year=2025, taxpayer={"iin": "1"})

        self.assertTrue(second.completed)
        self.assertEqual(second.missing_transfer_basis, ())
        self.assertFalse((self.paths.processed_data / "client_missing_transfer_basis.json").exists())

    def test_cli_calls_domain_pipeline_and_returns_nonzero_for_strict_missing_basis(self) -> None:
        account = _account("ib", "U1")
        missing = MissingTransferBasis(
            transfer_date=date(2024, 1, 2),
            symbol="XYZ",
            isin="US0000000001",
            quantity=Decimal("10"),
            currency="USD",
            destination_broker="ib",
            destination_account="U1",
            reason="missing_source",
        )
        result = FrontPipelineResult(
            client_id="client",
            tax_year=2025,
            discovered_accounts=(account,),
            individual_workbook_paths=(self.paths.processed_data / "ib_U1_audit.xlsx",),
            joint_workbook_paths=(),
            final_merge_input_paths=(),
            merged_workbook_path=None,
            form270_paths=(),
            missing_transfer_basis=(missing,),
            used_approximate_transfer_basis=False,
            completed=False,
        )
        job = Form270JobConfig(
            mode="front_pipeline",
            owner=Form270OwnerConfig("Ivanov", "Ivan", "", "123456789012"),
            tax_year=2025,
            job_id="front-pipeline",
            client_id="client",
        )
        config = Form270RunConfig(
            paths=self.paths,
            defaults=Form270DefaultsConfig(),
            banks={},
            jobs=(job,),
        )
        domain = MagicMock()
        domain.run.return_value = result
        with (
            patch("kztax270.cli.FrontPipeline", return_value=domain),
            patch("builtins.input", return_value="N"),
        ):
            exit_code = _run_form270_config(config)

        self.assertEqual(exit_code, 1)
        domain.run.assert_called_once()
        self.assertEqual(domain.run.call_args.kwargs["client_id"], "client")

    def test_cli_repeats_front_pipeline_in_approximate_mode_after_yes(self) -> None:
        account = _account("ib", "U1")
        missing = MissingTransferBasis(
            transfer_date=date(2024, 1, 2),
            symbol="XYZ",
            isin="US0000000001",
            quantity=Decimal("10"),
            currency="USD",
            destination_broker="ib",
            destination_account="U1",
            reason="missing_source",
        )
        strict_result = FrontPipelineResult(
            client_id="client",
            tax_year=2025,
            discovered_accounts=(account,),
            individual_workbook_paths=(self.paths.processed_data / "ib_U1_audit.xlsx",),
            joint_workbook_paths=(),
            final_merge_input_paths=(),
            merged_workbook_path=None,
            form270_paths=(),
            missing_transfer_basis=(missing,),
            used_approximate_transfer_basis=False,
            completed=False,
        )
        approximate_result = replace(
            strict_result,
            merged_workbook_path=self.paths.processed_data / "merged_client.xlsx",
            form270_paths=(self.paths.output_data / "270_2025_client_filled.json",),
            used_approximate_transfer_basis=True,
            completed=True,
        )
        job = Form270JobConfig(
            mode="front_pipeline",
            owner=Form270OwnerConfig("Ivanov", "Ivan", "", "123456789012"),
            tax_year=2025,
            job_id="front-pipeline",
            client_id="client",
        )
        config = Form270RunConfig(
            paths=self.paths,
            defaults=Form270DefaultsConfig(),
            banks={},
            jobs=(job,),
        )
        domain = MagicMock()
        domain.run.side_effect = (strict_result, approximate_result)
        with (
            patch("kztax270.cli.FrontPipeline", return_value=domain),
            patch("builtins.input", return_value="Y"),
        ):
            exit_code = _run_form270_config(config)

        self.assertEqual(exit_code, 0)
        self.assertEqual(domain.run.call_count, 2)
        self.assertNotIn("allow_approximate_transfer_basis", domain.run.call_args_list[0].kwargs)
        self.assertTrue(domain.run.call_args_list[1].kwargs["allow_approximate_transfer_basis"])

    def test_form270_05_preparation_is_reused_on_the_merged_workbook(self) -> None:
        account = _account("freedom", "759023")
        dataset = _dataset("freedom", "759023", [])
        pipeline = FrontPipeline(
            self.paths,
            account_runner=self._runner({("freedom", "759023"): dataset}),
        )
        builder = MagicMock()
        builder.build_processed_workbook_draft.return_value = {}
        builder.save.side_effect = lambda _draft, output: output.write_text("{}", encoding="utf-8") or output
        with (
            patch.object(pipeline, "discover_accounts", return_value=(account,)),
            patch("kztax270.front_pipeline.Form270JsonBuilder", return_value=builder),
            patch("kztax270.front_pipeline.AnnualFxRateProvider.from_nbk_rates_xlsx", return_value=MagicMock()),
            patch("kztax270.front_pipeline.prepare_form270_05_trades_workbook") as prepare,
        ):
            result = pipeline.run(
                client_id="client",
                tax_year=2025,
                taxpayer={"iin": "1"},
                form270_05=True,
            )

        prepare.assert_called_once_with(result.merged_workbook_path, ANY)
        self.assertTrue(builder.build_processed_workbook_draft.call_args.kwargs["form270_05"])


if __name__ == "__main__":
    unittest.main()
