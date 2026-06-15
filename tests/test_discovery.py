from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.discovery import DiscoveryRule, discover_raw_reports


class RawDiscoveryTests(unittest.TestCase):
    def test_discovers_reports_by_broker_account_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broker_dir = root / "ib"
            broker_dir.mkdir()
            expected = broker_dir / "U123_2024.csv"
            expected.write_text("x", encoding="utf-8")
            (broker_dir / "U999_2024.csv").write_text("x", encoding="utf-8")
            (broker_dir / "U123_notes.txt").write_text("x", encoding="utf-8")

            reports = discover_raw_reports(root, DiscoveryRule(broker="ib", account_id="U123"))

        self.assertEqual([report.path.name for report in reports], [expected.name])

    def test_transfer_out_source_workbooks_are_not_discovered_as_raw_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_root = Path(tmp) / "raw"
            broker_root = raw_root / "freedom"
            broker_root.mkdir(parents=True)
            report = broker_root / "1467068_2024-01-01_2024-12-31_all.xlsx"
            transfer_out_underscore = broker_root / "transfer_out 1467068.xlsx"
            transfer_out_space = broker_root / "transfer out 1467068.xlsx"
            report.write_text("raw report placeholder", encoding="utf-8")
            transfer_out_underscore.write_text("transfer out placeholder", encoding="utf-8")
            transfer_out_space.write_text("transfer out placeholder", encoding="utf-8")

            reports = discover_raw_reports(raw_root, DiscoveryRule(broker="freedom", account_id="1467068"))

        self.assertEqual([item.path.name for item in reports], [report.name])


if __name__ == "__main__":
    unittest.main()
