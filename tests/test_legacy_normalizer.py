from __future__ import annotations

import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.canonical.normalizer import normalize_legacy_tables


class LegacyNormalizerTests(unittest.TestCase):
    def test_maps_known_legacy_sheets_to_canonical_sheets(self) -> None:
        dataset = normalize_legacy_tables(
            "ib",
            "U1",
            {
                "FinInfo": [{"Symbol": "AAPL", "ISIN": "US0378331005"}],
                "Dividend": [{"Amount": "10", "Withhold": "1"}],
            },
        )
        self.assertIn("Instruments", dataset.tables)
        self.assertIn("Dividends", dataset.tables)
        self.assertEqual(dataset.tables["Instruments"][0]["symbol"], "AAPL")
        self.assertEqual(dataset.tables["Dividends"][0]["gross_amount"], "10")


if __name__ == "__main__":
    unittest.main()
