from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conftest_imports import SRC  # noqa: F401
from kztax270.diagnostics import (
    TrackedRawRow,
    clear_raw_row_context,
    current_raw_row_log_context,
    instrument_parsed_reports,
)


@dataclass
class _ParsedReport:
    path: Path
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class DiagnosticsTests(unittest.TestCase):
    def test_parsed_raw_row_is_printed_in_the_active_diagnostic_context(self) -> None:
        report = _ParsedReport(
            path=Path("client-report.xlsx"),
            rows={
                "Trades": [
                    {
                        "Ticker": "TEST.US",
                        "Quantity": "10",
                        "Price": "12.50",
                        "source_sheet": "Trades 2025",
                        "source_row": 17,
                    }
                ]
            },
        )

        clear_raw_row_context()
        instrument_parsed_reports("freedom", [report])
        row = report.rows["Trades"][0]

        self.assertIsInstance(row, TrackedRawRow)
        self.assertEqual(row["Ticker"], "TEST.US")
        context = current_raw_row_log_context()
        self.assertIn('"broker": "freedom"', context)
        self.assertIn('"source_report": "client-report.xlsx"', context)
        self.assertIn('"source_sheet": "Trades 2025"', context)
        self.assertIn('"source_row": 17', context)
        self.assertIn('"Ticker": "TEST.US"', context)
        self.assertIn('"Price": "12.50"', context)


if __name__ == "__main__":
    unittest.main()
