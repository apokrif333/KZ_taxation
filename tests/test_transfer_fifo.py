from __future__ import annotations

from datetime import date
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conftest_imports import SRC  # noqa: F401
from kztax270.transfers import InteractiveTransferInFifoResolver, TransferInRequest, load_transfer_out_lots_from_audit_workbook


class TransferFifoSourceTests(unittest.TestCase):
    def test_loads_matching_fifo_lots_from_canonical_transfers_sheet(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "source.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-28",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Currency": "USD",
                        "Quantity": 25,
                        "Price": 91,
                        "Enter_Date": "2021-01-10 10:00:00",
                    },
                    {
                        "Date": "2023-12-28",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Currency": "USD",
                        "Quantity": 50,
                        "Price": 92,
                        "Enter_Date": "2022-02-10 11:00:00",
                    },
                ]
            ).to_excel(workbook_path, sheet_name="Transfers", index=False)

            lots = load_transfer_out_lots_from_audit_workbook(
                workbook_path,
                TransferInRequest(
                    transfer_date=date(2023, 12, 28),
                    symbol="BIL",
                    isin="US78468R6633",
                    quantity=Decimal("75"),
                    currency="USD",
                    asset_type="Stocks",
                ),
                broker="ib",
            )

        self.assertEqual([lot.quantity for lot in lots], [Decimal("25"), Decimal("50")])
        self.assertEqual([lot.price for lot in lots], [Decimal("91"), Decimal("92")])
        self.assertEqual(
            [lot.enter_date.isoformat(sep=" ") if lot.enter_date else None for lot in lots],
            ["2021-01-10 10:00:00", "2022-02-10 11:00:00"],
        )
        self.assertEqual({lot.source_file for lot in lots}, {str(workbook_path)})

    def test_loads_minimal_manual_transfer_out_template(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "manual_source.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-28",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Quantity": 25,
                        "Price": 91,
                        "Enter_Date": "2021-01-10 10:00:00",
                    },
                    {
                        "Date": "2023-12-28",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Quantity": 50,
                        "Price": 92,
                        "Enter_Date": "2022-02-10 11:00:00",
                    },
                ]
            ).to_excel(workbook_path, sheet_name="Transfers", index=False)

            lots = load_transfer_out_lots_from_audit_workbook(
                workbook_path,
                TransferInRequest(
                    transfer_date=date(2024, 2, 1),
                    symbol="BIL",
                    isin="US78468R6633",
                    quantity=Decimal("75"),
                    currency="USD",
                    asset_type="Stocks",
                ),
                broker="manual",
            )

        self.assertEqual([lot.quantity for lot in lots], [Decimal("25"), Decimal("50")])
        self.assertEqual([lot.price for lot in lots], [Decimal("91"), Decimal("92")])

    def test_matches_bond_transfer_out_when_source_uses_price_per_100_quantity_scale(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "freedom_source.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-10-23",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Asset_Type": "Bonds",
                        "Symbol": "B.0.061324.BND",
                        "ISIN": "US912797FS14",
                        "Currency": "USD",
                        "Quantity": 2000,
                        "Price": 95.73635,
                        "Enter_Date": "2023-07-13 16:52:52",
                    },
                ]
            ).to_excel(workbook_path, sheet_name="Transfers", index=False)

            lots = load_transfer_out_lots_from_audit_workbook(
                workbook_path,
                TransferInRequest(
                    transfer_date=date(2023, 10, 23),
                    symbol="912797FS1",
                    isin="US912797FS14",
                    quantity=Decimal("200000"),
                    currency="USD",
                    asset_type="Treasury Bills",
                ),
                broker="freedom",
            )

        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].quantity, Decimal("200000"))
        self.assertEqual(lots[0].price, Decimal("0.9573635"))
        self.assertEqual(lots[0].enter_date.isoformat(sep=" "), "2023-07-13 16:52:52")

    def test_matches_closest_prior_transfer_out_day_and_rejects_future_out(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "source.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-28",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Currency": "USD",
                        "Quantity": 75,
                        "Price": 91,
                        "Enter_Date": "2021-01-10 10:00:00",
                    },
                    {
                        "Date": "2024-03-01",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Currency": "USD",
                        "Quantity": 75,
                        "Price": 92,
                        "Enter_Date": "2022-02-10 11:00:00",
                    },
                ]
            ).to_excel(workbook_path, sheet_name="Transfers", index=False)

            lots = load_transfer_out_lots_from_audit_workbook(
                workbook_path,
                TransferInRequest(
                    transfer_date=date(2024, 2, 1),
                    symbol="BIL",
                    isin="US78468R6633",
                    quantity=Decimal("75"),
                    currency="USD",
                    asset_type="Stocks",
                ),
                broker="ib",
            )
            future_only_lots = load_transfer_out_lots_from_audit_workbook(
                workbook_path,
                TransferInRequest(
                    transfer_date=date(2023, 12, 1),
                    symbol="BIL",
                    isin="US78468R6633",
                    quantity=Decimal("75"),
                    currency="USD",
                    asset_type="Stocks",
                ),
                broker="ib",
            )

        self.assertEqual([lot.price for lot in lots], [Decimal("91")])
        self.assertEqual(future_only_lots, [])

    def test_interactive_resolver_reuses_previously_entered_source_workbook(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            workbook_path = Path(tmp) / "source.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-28",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Currency": "USD",
                        "Quantity": 75,
                        "Price": 91,
                    },
                    {
                        "Date": "2023-12-28",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "AAPL",
                        "ISIN": "US0378331005",
                        "Currency": "USD",
                        "Quantity": 10,
                        "Price": 100,
                    },
                ]
            ).to_excel(workbook_path, sheet_name="Transfers", index=False)
            resolver = InteractiveTransferInFifoResolver(Path(tmp))

            with patch("builtins.input", side_effect=["ib", str(workbook_path), ""]) as mocked_input, patch("builtins.print"):
                first = resolver(
                    TransferInRequest(
                        transfer_date=date(2024, 1, 15),
                        symbol="BIL",
                        isin="US78468R6633",
                        quantity=Decimal("75"),
                        currency="USD",
                        asset_type="Stocks",
                    )
                )
                second = resolver(
                    TransferInRequest(
                        transfer_date=date(2024, 1, 15),
                        symbol="AAPL",
                        isin="US0378331005",
                        quantity=Decimal("10"),
                        currency="USD",
                        asset_type="Stocks",
                    )
                )

        self.assertEqual([lot.price for lot in first or []], [Decimal("91")])
        self.assertEqual([lot.price for lot in second or []], [Decimal("100")])
        self.assertEqual(mocked_input.call_count, 3)

    def test_interactive_resolver_can_add_second_source_workbook_for_later_ticker(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            first_workbook = Path(tmp) / "source1.xlsx"
            second_workbook = Path(tmp) / "source2.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-28",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Currency": "USD",
                        "Quantity": 75,
                        "Price": 91,
                    },
                ]
            ).to_excel(first_workbook, sheet_name="Transfers", index=False)
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-29",
                        "Transfer_Type": "security",
                        "Direction": "out",
                        "Symbol": "AAPL",
                        "ISIN": "US0378331005",
                        "Currency": "USD",
                        "Quantity": 10,
                        "Price": 100,
                    },
                ]
            ).to_excel(second_workbook, sheet_name="Transfers", index=False)
            resolver = InteractiveTransferInFifoResolver(Path(tmp))

            with patch(
                "builtins.input",
                side_effect=["ib", str(first_workbook), "ib", str(second_workbook), ""],
            ) as mocked_input, patch("builtins.print"):
                first = resolver(
                    TransferInRequest(
                        transfer_date=date(2024, 1, 15),
                        symbol="BIL",
                        isin="US78468R6633",
                        quantity=Decimal("75"),
                        currency="USD",
                        asset_type="Stocks",
                    )
                )
                second = resolver(
                    TransferInRequest(
                        transfer_date=date(2024, 1, 15),
                        symbol="AAPL",
                        isin="US0378331005",
                        quantity=Decimal("10"),
                        currency="USD",
                        asset_type="Stocks",
                    )
                )
                third = resolver(
                    TransferInRequest(
                        transfer_date=date(2024, 1, 15),
                        symbol="AAPL",
                        isin="US0378331005",
                        quantity=Decimal("10"),
                        currency="USD",
                        asset_type="Stocks",
                    )
                )

        self.assertEqual([lot.price for lot in first or []], [Decimal("91")])
        self.assertEqual([lot.price for lot in second or []], [Decimal("100")])
        self.assertEqual([lot.price for lot in third or []], [Decimal("100")])
        self.assertEqual(mocked_input.call_count, 5)

    def test_interactive_resolver_finds_source_workbook_under_raw_broker_folder(self) -> None:
        import pandas as pd  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_ib = root / "raw" / "ib"
            raw_ib.mkdir(parents=True)
            workbook_path = raw_ib / "transfer out Freedom Ushanev.xlsx"
            pd.DataFrame(
                [
                    {
                        "Date": "2023-12-28",
                        "Symbol": "BIL",
                        "ISIN": "US78468R6633",
                        "Quantity": 75,
                        "Price": 91,
                    },
                ]
            ).to_excel(workbook_path, sheet_name="Transfers", index=False)
            resolver = InteractiveTransferInFifoResolver(root / "processed", raw_root=root / "raw")

            with patch("builtins.input", side_effect=["IB", "transfer out Freedom Ushanev"]) as mocked_input, patch("builtins.print"):
                lots = resolver(
                    TransferInRequest(
                        transfer_date=date(2024, 1, 15),
                        symbol="BIL",
                        isin="US78468R6633",
                        quantity=Decimal("75"),
                        currency="USD",
                        asset_type="Stocks",
                    )
                )

        self.assertEqual([lot.price for lot in lots or []], [Decimal("91")])
        self.assertEqual({lot.source_file for lot in lots or []}, {str(workbook_path)})
        self.assertEqual(mocked_input.call_count, 2)

    def test_interactive_resolver_does_not_raise_for_missing_source_workbook(self) -> None:
        resolver = InteractiveTransferInFifoResolver(Path("missing_processed"), raw_root=Path("missing_raw"))

        with patch("builtins.input", side_effect=["IB", "missing source workbook"]), patch("builtins.print"):
            lots = resolver(
                TransferInRequest(
                    transfer_date=date(2024, 1, 15),
                    symbol="BIL",
                    isin="US78468R6633",
                    quantity=Decimal("75"),
                    currency="USD",
                    asset_type="Stocks",
                )
            )

        self.assertIsNone(lots)


if __name__ == "__main__":
    unittest.main()
