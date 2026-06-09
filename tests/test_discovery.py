from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from conftest_imports import SRC  # noqa: F401
from kztax270.brokers.discovery import DiscoveryRule, discover_raw_reports


class DiscoveryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
