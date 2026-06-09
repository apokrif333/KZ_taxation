from __future__ import annotations

from decimal import Decimal
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conftest_imports import SRC  # noqa: F401
from kztax270.reference.nbk import NBK_RATE_COLUMNS
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reference.nbk import ensure_nbk_rates_current, upsert_nbk_average_annual_rates_xlsx
from kztax270.reference.repositories import ReferenceDataStore


class NbkRatesTests(unittest.TestCase):
    def test_imports_nbk_average_annual_rates_xlsx_into_reference_store(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            xlsx_path = tmp_path / "nb_rates.xlsx"
            pd.DataFrame(
                [
                    {"Des_Currency": "US Dollar", "Currency": "USD", "Annual": 469.44, "Year": 2024},
                    {"Des_Currency": "Euro", "Currency": "EUR", "Annual": 507.66, "Year": 2024},
                ]
            ).to_excel(xlsx_path, index=False)

            store = ReferenceDataStore(tmp_path / "reference")
            changed = upsert_nbk_average_annual_rates_xlsx(xlsx_path, store)
            provider = AnnualFxRateProvider.from_reference_store(store)
            direct_provider = AnnualFxRateProvider.from_nbk_rates_xlsx(xlsx_path)

        self.assertEqual(changed, 2)
        self.assertEqual(provider.rate(2024, "USD"), Decimal("469.44"))
        self.assertEqual(provider.rate(2024, "EUR"), Decimal("507.66"))
        self.assertEqual(provider.rate(2024, "KZT"), Decimal("1"))
        self.assertEqual(direct_provider.rate(2024, "USD"), Decimal("469.44"))

    def test_nbk_current_check_does_not_update_when_previous_year_exists(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / "nb_rates.xlsx"
            pd.DataFrame(
                [{"Des_Currency": "US Dollar", "Currency": "USD", "Annual": 469.44, "Year": 2024}]
            ).to_excel(xlsx_path, index=False)

            updated = ensure_nbk_rates_current(xlsx_path, today=date(2025, 6, 4))

        self.assertFalse(updated)

    def test_nbk_current_check_raises_when_previous_year_is_still_missing_after_update(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / "nb_rates.xlsx"
            empty_rates = pd.DataFrame(columns=list(NBK_RATE_COLUMNS))
            with patch("kztax270.reference.nbk.fetch_nbk_average_annual_rates", return_value=empty_rates):
                with self.assertRaises(RuntimeError):
                    ensure_nbk_rates_current(xlsx_path, today=date(2026, 6, 4))


if __name__ == "__main__":
    unittest.main()
