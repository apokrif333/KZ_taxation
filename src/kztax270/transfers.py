"""Helpers for resolving incoming security transfers from prior FIFO audit data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class TransferInRequest:
    transfer_date: date | None
    symbol: str | None
    isin: str | None
    quantity: Decimal
    currency: str | None
    asset_type: str | None
    source_report: str | None = None
    counterparty: str | None = None

    def prompt(self) -> str:
        return (
            "Укажите брокера и название файла, где находятся FIFO данные по "
            f"{self.transfer_date.isoformat() if self.transfer_date else ''} "
            f"{self.symbol or ''} {self.isin or ''} {format(self.quantity.normalize(), 'f')}"
        ).strip()


@dataclass(frozen=True, slots=True)
class TransferInFifoLot:
    quantity: Decimal
    price: Decimal
    enter_date: datetime | None = None
    source_broker: str | None = None
    source_file: str | None = None
    source_row: int | None = None


@dataclass(frozen=True, slots=True)
class _TransferFifoSource:
    broker: str
    workbook_path: Path
    rows: tuple[dict[str, Any], ...]


class TransferInFifoResolver(Protocol):
    def __call__(self, request: TransferInRequest) -> Sequence[TransferInFifoLot] | None:
        """Return FIFO lots for the incoming transfer, or None when unresolved."""


def load_transfer_out_lots_from_audit_workbook(
    workbook_path: Path,
    request: TransferInRequest,
    *,
    broker: str | None = None,
) -> list[TransferInFifoLot]:
    """Read FIFO allocation rows from a canonical audit workbook Transfers sheet.

    Matching uses one outgoing security-transfer day with the same ISIN/symbol
    and total quantity equal to the incoming transfer quantity. The outgoing
    date must not be after the incoming transfer date; when several days match,
    the closest prior outgoing day is used.
    """

    rows = _read_transfer_rows(workbook_path)
    return _match_transfer_out_lots(rows, request, broker=broker, workbook_path=workbook_path)


class InteractiveTransferInFifoResolver:
    """Prompt for source workbooks once and reuse them for later Transfer IN rows."""

    def __init__(self, processed_root: Path, raw_root: Path | None = None) -> None:
        self.processed_root = processed_root
        self.raw_root = raw_root
        self.sources: list[_TransferFifoSource] = []

    def __call__(self, request: TransferInRequest) -> Sequence[TransferInFifoLot] | None:
        print(request.prompt())
        broker = _clean_user_input(input("Брокер источник (Enter = использовать уже указанные файлы / пропустить): "))
        if not broker:
            lots = self._load_from_known_sources(request)
            if not lots:
                print("FIFO rows not found in known source workbooks")
            return lots or None
        file_name = _clean_user_input(input("Файл audit workbook или полный путь: ")).strip('"')
        if not file_name:
            lots = self._load_from_known_sources(request)
            if not lots:
                print("FIFO rows not found in known source workbooks")
            return lots or None

        workbook_path = _resolve_workbook_path(self.processed_root, broker, file_name, raw_root=self.raw_root)
        try:
            source = self._add_source(broker, workbook_path)
        except FileNotFoundError:
            print(f"FIFO source workbook not found: {workbook_path}")
            lots = self._load_from_known_sources(request)
            return lots or None
        except Exception as exc:
            print(f"Cannot read FIFO source workbook {workbook_path}: {exc}")
            lots = self._load_from_known_sources(request)
            return lots or None
        lots = _match_transfer_out_lots(
            source.rows,
            request,
            broker=source.broker,
            workbook_path=source.workbook_path,
        )
        if not lots:
            print(f"FIFO rows not found in {workbook_path}")
            return None
        return lots

    def _load_from_known_sources(self, request: TransferInRequest) -> list[TransferInFifoLot]:
        for source in self.sources:
            lots = _match_transfer_out_lots(
                source.rows,
                request,
                broker=source.broker,
                workbook_path=source.workbook_path,
            )
            if lots:
                return lots
        return []

    def _add_source(self, broker: str, workbook_path: Path) -> _TransferFifoSource:
        resolved_path = workbook_path.resolve()
        for source in self.sources:
            if source.broker == broker and source.workbook_path.resolve() == resolved_path:
                return source
        source = _TransferFifoSource(broker=broker, workbook_path=workbook_path, rows=_read_transfer_rows(workbook_path))
        self.sources.append(source)
        return source


def _read_transfer_rows(workbook_path: Path) -> tuple[dict[str, Any], ...]:
    if not workbook_path.exists():
        raise FileNotFoundError(workbook_path)
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency error path
        raise RuntimeError("Reading transfer FIFO source workbooks requires pandas/openpyxl.") from exc

    df = pd.read_excel(workbook_path, sheet_name="Transfers")
    if df.empty:
        return ()
    return tuple(
        _normalize_excel_row(record, source_row=idx)
        for idx, record in enumerate(df.to_dict(orient="records"), start=2)
    )


def _match_transfer_out_lots(
    rows: Sequence[Mapping[str, Any]],
    request: TransferInRequest,
    *,
    broker: str | None,
    workbook_path: Path,
) -> list[TransferInFifoLot]:
    candidates = [
        row
        for row in rows
        if _is_source_transfer_out_row(row)
        and _same_instrument(row, request)
        and _decimal(row.get("quantity")) > 0
        and row.get("price") not in (None, "")
    ]
    if not candidates:
        return []

    grouped: dict[tuple[str | None, str | None], list[Mapping[str, Any]]] = {}
    for row in candidates:
        grouped.setdefault((_text(row.get("date")) or None, _text(row.get("currency")) or None), []).append(row)

    exact_quantity_groups = [
        (group_rows, scale)
        for group_rows in grouped.values()
        for scale in (_matching_quantity_scale(group_rows, request),)
        if scale is not None and _is_not_after_transfer_in(group_rows, request)
    ]
    if not exact_quantity_groups:
        return []

    selected, quantity_scale = min(
        exact_quantity_groups,
        key=lambda item: _transfer_date_distance(item[0], request),
    )
    lots: list[TransferInFifoLot] = []
    for row in selected:
        lots.append(
            TransferInFifoLot(
                quantity=_decimal(row.get("quantity")) * quantity_scale,
                price=_decimal(row.get("price")) / quantity_scale,
                enter_date=_parse_datetime(row.get("enter_date")),
                source_broker=broker,
                source_file=str(workbook_path),
                source_row=int(row["_source_row_number"]) if row.get("_source_row_number") not in (None, "") else None,
            )
        )
    return lots


def _matching_quantity_scale(
    group_rows: Sequence[Mapping[str, Any]],
    request: TransferInRequest,
) -> Decimal | None:
    source_quantity = sum((_decimal(row.get("quantity")) for row in group_rows), Decimal("0"))
    requested_quantity = abs(request.quantity)
    if abs(source_quantity - requested_quantity) <= Decimal("0.0001"):
        return Decimal("1")
    if _is_debt_asset(request.asset_type):
        for scale in (Decimal("100"),):
            if abs(source_quantity * scale - requested_quantity) <= Decimal("0.0001"):
                return scale
    return None


def _is_debt_asset(asset_type: str | None) -> bool:
    text = _text(asset_type).lower()
    return any(token in text for token in ("bond", "bill", "note", "debt", "облиг"))


def _resolve_workbook_path(processed_root: Path, broker: str, file_name: str, raw_root: Path | None = None) -> Path:
    path = Path(file_name)
    if path.exists():
        return path
    if not path.suffix:
        path_with_xlsx = path.with_suffix(".xlsx")
        if path_with_xlsx.exists():
            return path_with_xlsx
    broker_path_parts = _broker_path_parts(broker)
    raw_candidates: list[Path] = []
    if raw_root is not None:
        for broker_part in broker_path_parts:
            raw_candidates.extend(
                [
                    raw_root / broker_part / file_name,
                    raw_root / broker_part / path.name,
                    raw_root / broker_part / f"{file_name}.xlsx",
                ]
            )
    candidates = [
        *raw_candidates,
        processed_root / file_name,
        processed_root / path.name,
        processed_root / f"{broker}_{file_name}_audit.xlsx",
        processed_root / f"{broker}_{file_name}.xlsx",
        processed_root / f"{file_name}.xlsx",
        Path("data/templates") / file_name,
        Path("data/templates") / f"{file_name}.xlsx",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _broker_path_parts(broker: str) -> tuple[str, ...]:
    broker = _clean_user_input(broker)
    variants = [broker.lower(), broker, broker.upper()]
    return tuple(dict.fromkeys(part for part in variants if part))


def _clean_user_input(value: str) -> str:
    return value.strip().lstrip("\ufeff").removeprefix("п»ї").strip()


def _normalize_excel_row(record: Mapping[str, Any], *, source_row: int | None = None) -> dict[str, Any]:
    row = {_normalize_column_name(key): value for key, value in record.items()}
    row["_source_row_number"] = source_row
    return row


def _normalize_column_name(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _same_instrument(row: Mapping[str, Any], request: TransferInRequest) -> bool:
    row_isin = _text(row.get("isin"))
    if request.isin and row_isin:
        return row_isin == request.isin
    row_symbol = _text(row.get("symbol"))
    return bool(request.symbol and row_symbol == request.symbol)


def _is_source_transfer_out_row(row: Mapping[str, Any]) -> bool:
    direction = _text(row.get("direction")).lower()
    transfer_type = _text(row.get("transfer_type")).lower()
    return direction in {"", "out"} and transfer_type in {"", "security"}


def _transfer_date_distance(group_rows: Sequence[Mapping[str, Any]], request: TransferInRequest) -> tuple[int, int]:
    if request.transfer_date is None:
        return (0, 0)
    row_date = _parse_date(group_rows[0].get("date"))
    if row_date is None:
        return (1, 999999)
    return (0, abs((request.transfer_date - row_date).days))


def _is_not_after_transfer_in(group_rows: Sequence[Mapping[str, Any]], request: TransferInRequest) -> bool:
    if request.transfer_date is None:
        return True
    row_date = _parse_date(group_rows[0].get("date"))
    return row_date is not None and row_date <= request.transfer_date


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if hasattr(value, "date"):
        return value.date()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat"}:
        return None
    return date.fromisoformat(text[:10])


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat"}:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(text[:10])


def _decimal(value: Any) -> Decimal:
    if value in (None, "", "--"):
        return Decimal("0")
    return Decimal(str(value).replace(",", ""))


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if str(value).lower() in {"nan", "nat"}:
        return ""
    return str(value).strip()
