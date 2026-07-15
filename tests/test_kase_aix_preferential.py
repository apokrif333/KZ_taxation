from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from kztax270.reference.kase_aix import (
    KaseAixDividendProvider,
    create_kase_aix_checks,
    ensure_kase_aix_preferential_current,
)


class KaseAixPreferentialTests(unittest.TestCase):
    def test_fresh_sources_do_not_trigger_download_or_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame({"month": ["6_2026"]}).to_excel(root / "kase_pref.xlsx", index=False)
            pd.DataFrame({"period": ["2026-06"]}).to_excel(root / "aix_pref.xlsx", index=False)
            pd.DataFrame({"ready": [1]}).to_excel(root / "kase_aix_pref.xlsx", index=False)
            pd.DataFrame({"ready": [1]}).to_excel(root / "kase_aix_pref_yearly.xlsx", index=False)

            with (
                patch("kztax270.reference.kase_aix.ensure_aix_instruments_current", return_value=False),
                patch("kztax270.reference.kase_aix.update_kase_pref_data") as update_kase,
                patch("kztax270.reference.kase_aix.update_aix_pref_data") as update_aix,
                patch("kztax270.reference.kase_aix.create_kase_aix_checks") as create_checks,
            ):
                updated = ensure_kase_aix_preferential_current(root, today=date(2026, 7, 15))

        self.assertFalse(updated)
        update_kase.assert_not_called()
        update_aix.assert_not_called()
        create_checks.assert_not_called()

    def test_provider_uses_all_isin_columns_and_only_passed_years(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yearly.xlsx"
            pd.DataFrame(
                [
                    {
                        "ISIN": "US0000000001",
                        "isin2": "US0000000002",
                        "isin3": "US0000000003",
                        "Год": 2024,
                        "exchange": "AIX",
                        "year_check": 1,
                    },
                    {
                        "ISIN": "US0000000004",
                        "Год": 2024,
                        "exchange": "KASE",
                        "year_check": 0,
                    },
                ]
            ).to_excel(path, index=False)

            provider = KaseAixDividendProvider.from_xlsx(path)

        self.assertEqual(provider.preferential_flag("US0000000001", 2024), "preferential_aix")
        self.assertEqual(provider.preferential_flag("US0000000003", 2024), "preferential_aix")
        self.assertIsNone(provider.preferential_flag("US0000000004", 2024))

    def test_dual_listing_sums_monthly_liquidity_and_bypasses_free_float_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kase_path = root / "kase.xlsx"
            aix_path = root / "aix.xlsx"
            monthly_path = root / "monthly.xlsx"
            yearly_path = root / "yearly.xlsx"
            months = list(range(1, 13))
            pd.DataFrame(
                {
                    "Код": ["TEST"] * 12,
                    "Компания": ["Test issuer"] * 12,
                    "Сектор": ["акции"] * 12,
                    "Количество сделок": [30] * 12,
                    "Объём, млн KZT": [20] * 12,
                    "Free Float %": [0] * 12,
                    "IPO/SPO": [""] * 12,
                    "month": [f"{month}_2026" for month in months],
                    "ISIN": ["US0000000001"] * 12,
                }
            ).to_excel(kase_path, index=False)
            pd.DataFrame(
                {
                    "period": [f"2026-{month:02d}" for month in months],
                    "secCode": ["TEST.AIX"] * 12,
                    "name": ["Test issuer"] * 12,
                    "assetClass": ["EQTY"] * 12,
                    "isEtfEtn": [False] * 12,
                    "numberOfTrades": [20] * 12,
                    "value": [5_000_000] * 12,
                    "isin": ["US0000000001"] * 12,
                }
            ).to_excel(aix_path, index=False)

            result = create_kase_aix_checks(kase_path, aix_path, monthly_path, yearly_path)
            yearly = pd.read_excel(yearly_path)

        self.assertTrue(result["has_kase"].all())
        self.assertTrue(result["has_aix"].all())
        self.assertTrue(result["vol"].eq(25).all())
        self.assertTrue(result["trades"].eq(50).all())
        self.assertTrue(result["ipo_ff_check"].eq(1).all())
        self.assertTrue(result["month_check"].eq(1).all())
        self.assertTrue(result["year_check"].eq(1).all())
        self.assertEqual(yearly["exchange"].tolist(), ["AIX"])
