"""Shared FIFO calculation contract and a minimal generic implementation."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping


@dataclass(slots=True)
class FifoLot:
    symbol: str
    isin: str | None
    currency: str
    enter_date: Any
    enter_price: Decimal
    exit_date: Any
    exit_price: Decimal
    quantity: Decimal
    allocated_commission: Decimal
    pnl_before_commission: Decimal
    pnl_after_all_commissions: Decimal
    pnl: Decimal
    source_trade_id: str | None = None


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


class FifoCalculator:
    """Broker-agnostic FIFO engine for normalized trade rows.

    This implementation is intentionally small. It handles plain buy/sell rows
    and allocates commissions proportionally by closed quantity. Broker-specific
    corporate actions and options should be normalized before this step.
    """

    def calculate(self, trades: Iterable[Mapping[str, Any]]) -> list[FifoLot]:
        inventory: dict[tuple[str, str | None, str], deque[dict[str, Any]]] = defaultdict(deque)
        realized: list[FifoLot] = []

        sorted_trades = sorted(trades, key=lambda row: str(row.get("date_time") or row.get("date") or ""))
        for trade in sorted_trades:
            symbol = str(trade.get("symbol") or "")
            isin = trade.get("isin")
            currency = str(trade.get("currency") or "")
            quantity = _decimal(trade.get("quantity"))
            price = _decimal(trade.get("price"))
            commission = abs(_decimal(trade.get("commission")))
            key = (symbol, str(isin) if isin else None, currency)

            if quantity > 0:
                inventory[key].append(
                    {
                        "remaining": quantity,
                        "quantity": quantity,
                        "price": price,
                        "commission": commission,
                        "date": trade.get("date_time") or trade.get("date"),
                        "trade_id": trade.get("trade_id"),
                    }
                )
                continue

            sell_qty = abs(quantity)
            while sell_qty > 0:
                if not inventory[key]:
                    raise ValueError(f"FIFO short sale or missing opening lot for {key}: {sell_qty}")
                lot = inventory[key][0]
                closed_qty = min(sell_qty, lot["remaining"])
                buy_commission = lot["commission"] * (closed_qty / lot["quantity"])
                sell_commission = commission * (closed_qty / abs(quantity)) if quantity else Decimal("0")
                pnl_before_commission = (price - lot["price"]) * closed_qty
                pnl = pnl_before_commission - buy_commission
                pnl_after_all_commissions = pnl_before_commission - buy_commission - sell_commission
                realized.append(
                    FifoLot(
                        symbol=symbol,
                        isin=key[1],
                        currency=currency,
                        enter_date=lot["date"],
                        enter_price=lot["price"],
                        exit_date=trade.get("date_time") or trade.get("date"),
                        exit_price=price,
                        quantity=closed_qty,
                        allocated_commission=buy_commission + sell_commission,
                        pnl_before_commission=pnl_before_commission,
                        pnl_after_all_commissions=pnl_after_all_commissions,
                        pnl=pnl,
                        source_trade_id=str(trade.get("trade_id")) if trade.get("trade_id") else None,
                    )
                )
                lot["remaining"] -= closed_qty
                sell_qty -= closed_qty
                if lot["remaining"] == 0:
                    inventory[key].popleft()
        return realized
