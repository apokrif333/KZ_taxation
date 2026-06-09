from __future__ import annotations

from decimal import Decimal
import unittest

from conftest_imports import SRC  # noqa: F401
from kztax270.form270.merge import merge_form270_jsons
from kztax270.form270.split import split_form270_json


class Form270JsonTests(unittest.TestCase):
    def test_merge_concatenates_lists_and_keeps_existing_scalar(self) -> None:
        merged = merge_form270_jsons(
            [
                {"fnoYear": 2024, "rows": [{"amount": "10"}], "taxpayerCode": "111"},
                {"fnoYear": 2024, "rows": [{"amount": "20"}], "taxpayerCode": "222"},
            ]
        )
        self.assertEqual(merged["taxpayerCode"], "111")
        self.assertEqual(merged["rows"], [{"amount": "10"}, {"amount": "20"}])
        self.assertEqual(merged["_kztax270"]["status"], "merged_draft")

    def test_split_scales_amount_like_keys(self) -> None:
        split = split_form270_json(
            {"rows": [{"gross_amount": "100", "symbol": "AAPL"}]},
            {"a": Decimal("0.25"), "b": Decimal("0.75")},
        )
        self.assertEqual(split["a"]["rows"][0]["gross_amount"], "25.00")
        self.assertEqual(split["b"]["rows"][0]["symbol"], "AAPL")


if __name__ == "__main__":
    unittest.main()
