"""Client-wide multi-broker orchestration for the local front-pipeline workflow."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from kztax270.brokers.account_detection import (
    BROKER_REPORT_SPECS,
    DetectedReportMetadata,
    detect_report_metadata,
)
from kztax270.canonical.schema import CanonicalDataset
from kztax270.config import AccountConfig, ProjectPaths, validate_client_id
from kztax270.excel.form270_05_trades import prepare_form270_05_trades_workbook
from kztax270.excel.joint_workbook import create_joint_audit_workbook
from kztax270.excel.merge_workbooks import merge_audit_workbooks
from kztax270.form270.json_builder import BrokerBankInfo, Form270JsonBuilder
from kztax270.pipeline import AccountPipeline, AccountPipelineResult
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.transfers import (
    TransferInFifoLot,
    TransferInFifoResolver,
    TransferInRequest,
    matching_transfer_quantity_scale,
    same_transfer_instrument,
)

# ``freedom bank`` is accepted as a compatibility alias for the existing
# client folder; ``freedom_bank`` is the canonical spelling for new folders.
FRONT_BROKER_FOLDERS = {
    "ib": "ib",
    "exante": "exante",
    "tabys": "tabys",
    "tsifra": "tsifra",
    "freedom_bank": "freedom_bank",
    "freedom bank": "freedom_bank",
}
TRANSFER_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class DiscoveredAccount:
    broker: str
    account_id: str
    report_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class InvalidReportPeriod:
    broker: str
    account_id: str
    report_name: str
    period_end: date | None


class InvalidReportPeriodError(ValueError):
    """One or more client reports are not complete calendar-year reports."""

    def __init__(self, reports: Sequence[InvalidReportPeriod]) -> None:
        self.reports = tuple(reports)
        super().__init__(
            "Отчёт сформирован некорректно: брокерские отчёты должны заканчиваться 31 декабря."
        )


@dataclass(frozen=True, slots=True)
class MissingTransferBasis:
    transfer_date: date | None
    symbol: str | None
    isin: str | None
    quantity: Decimal
    currency: str | None
    destination_broker: str
    destination_account: str
    reason: str


@dataclass(frozen=True, slots=True)
class FrontPipelineResult:
    client_id: str
    tax_year: int
    discovered_accounts: tuple[DiscoveredAccount, ...]
    individual_workbook_paths: tuple[Path, ...]
    joint_workbook_paths: tuple[Path, ...]
    final_merge_input_paths: tuple[Path, ...]
    merged_workbook_path: Path | None
    form270_paths: tuple[Path, ...]
    missing_transfer_basis: tuple[MissingTransferBasis, ...]
    used_approximate_transfer_basis: bool
    completed: bool


@dataclass(slots=True)
class _TransferEvent:
    broker: str
    account_id: str
    event_date: date | None
    direction: str
    symbol: str | None
    isin: str | None
    currency: str | None
    asset_type: str | None
    source_report: str | None
    counterparty: str | None
    rows: list[Mapping[str, Any]]
    ordinal: int

    @property
    def quantity(self) -> Decimal:
        return sum((_decimal(row.get("quantity")) for row in self.rows), Decimal("0"))

    def request(self) -> TransferInRequest:
        return TransferInRequest(
            transfer_date=self.event_date,
            symbol=self.symbol,
            isin=self.isin,
            quantity=abs(self.quantity),
            currency=self.currency,
            asset_type=self.asset_type,
            source_report=self.source_report,
            counterparty=self.counterparty,
        )


@dataclass(slots=True)
class _AvailableTransferOut:
    event: _TransferEvent
    lots: list[TransferInFifoLot]
    basis_complete: bool
    used: bool = False


@dataclass(frozen=True, slots=True)
class _TransferredLotConsumption:
    broker: str
    account_id: str
    event_date: date | None
    symbol: str | None
    isin: str | None
    quantity: Decimal
    ordinal: int


@dataclass(frozen=True, slots=True)
class _ResolvedIncoming:
    request: TransferInRequest
    lots: tuple[TransferInFifoLot, ...]


class _PlannedResolver:
    def __init__(self, resolutions: Sequence[_ResolvedIncoming]) -> None:
        self._remaining = list(resolutions)

    def __call__(self, request: TransferInRequest) -> Sequence[TransferInFifoLot] | None:
        key = _request_key(request)
        for index, resolution in enumerate(self._remaining):
            if _request_key(resolution.request) == key:
                return self._remaining.pop(index).lots
        return None


@dataclass(frozen=True, slots=True)
class GlobalTransferResolution:
    resolutions: Mapping[tuple[str, str], tuple[_ResolvedIncoming, ...]]
    missing: tuple[MissingTransferBasis, ...]

    @property
    def has_resolved_transfers(self) -> bool:
        return any(self.resolutions.values())

    def resolver_for(self, broker: str, account_id: str) -> TransferInFifoResolver:
        return _PlannedResolver(self.resolutions.get((broker, account_id), ()))


class GlobalTransferLedger:
    """Resolve all client security transfers in one deterministic chronological view."""

    def resolve(
        self,
        account_datasets: Sequence[tuple[DiscoveredAccount, CanonicalDataset]],
    ) -> GlobalTransferResolution:
        transfer_events = [
            event
            for account, dataset in account_datasets
            for event in _transfer_events(account, dataset.tables.get("Transfers", ()))
        ]
        consumption_events = [
            event
            for account, dataset in account_datasets
            for event in _transferred_lot_consumptions(account, dataset.tables.get("Fifo", ()))
        ]
        events: list[_TransferEvent | _TransferredLotConsumption] = [*transfer_events, *consumption_events]
        events.sort(key=_event_sort_key)
        available: list[_AvailableTransferOut] = []
        propagated: dict[tuple[str, str, str, str], list[TransferInFifoLot]] = defaultdict(list)
        resolved: dict[tuple[str, str], list[_ResolvedIncoming]] = defaultdict(list)
        missing: set[MissingTransferBasis] = set()

        for event in events:
            if isinstance(event, _TransferredLotConsumption):
                inventory = _find_inventory(propagated, event)
                _consume_propagated_lots(inventory, event.quantity)
                continue
            if event.direction == "out":
                lots, complete = self._outgoing_lots(event, propagated)
                available.append(_AvailableTransferOut(event=event, lots=lots, basis_complete=complete))
                continue

            request = event.request()
            candidates = self._instrument_candidates(available, request)
            exact = [
                source
                for source in candidates
                if matching_transfer_quantity_scale(source.event.rows, request) is not None
            ]
            if not exact:
                reason = "quantity_mismatch" if candidates else "missing_source"
                missing.add(_missing_basis(event, reason))
                continue
            closest_date = max(source.event.event_date or date.min for source in exact)
            closest = [source for source in exact if (source.event.event_date or date.min) == closest_date]
            if len(closest) != 1:
                missing.add(_missing_basis(event, "ambiguous_source"))
                continue
            source = closest[0]
            if not source.basis_complete:
                missing.add(_missing_basis(event, "missing_source"))
                continue
            scale = matching_transfer_quantity_scale(source.event.rows, request)
            assert scale is not None
            lots = tuple(
                TransferInFifoLot(
                    quantity=lot.quantity * scale,
                    price=lot.price / scale,
                    enter_date=lot.enter_date,
                    source_broker=lot.source_broker or source.event.broker,
                    source_account=lot.source_account or source.event.account_id,
                    source_file=lot.source_file,
                    source_row=lot.source_row,
                )
                for lot in source.lots
            )
            source.used = True
            resolved[(event.broker, event.account_id)].append(_ResolvedIncoming(request, lots))
            propagated[_inventory_key(event.broker, event.account_id, event.isin, event.symbol)].extend(lots)

        frozen_resolutions = {key: tuple(value) for key, value in resolved.items()}
        return GlobalTransferResolution(
            resolutions=frozen_resolutions,
            missing=tuple(sorted(missing, key=_missing_sort_key)),
        )

    @staticmethod
    def _instrument_candidates(
        available: Sequence[_AvailableTransferOut],
        request: TransferInRequest,
    ) -> list[_AvailableTransferOut]:
        prior = [
            source
            for source in available
            if not source.used
            and (request.transfer_date is None or source.event.event_date is None or source.event.event_date <= request.transfer_date)
            and same_transfer_instrument(source.event.rows[0], request)
        ]
        if request.isin:
            exact_isin = [source for source in prior if source.event.isin == request.isin]
            if exact_isin:
                return exact_isin
        return prior

    @staticmethod
    def _outgoing_lots(
        event: _TransferEvent,
        propagated: dict[tuple[str, str, str, str], list[TransferInFifoLot]],
    ) -> tuple[list[TransferInFifoLot], bool]:
        lots: list[TransferInFifoLot] = []
        complete = True
        inventory = _find_inventory(propagated, event)
        for row_index, row in enumerate(event.rows, start=1):
            quantity = abs(_decimal(row.get("quantity")))
            status = str(row.get("_opening_lot_status") or "").lower()
            from_transfer = "transfer" in status or bool(row.get("_fifo_source_broker"))
            if from_transfer:
                consumed = _consume_propagated_lots(
                    inventory, quantity
                )
                lots.extend(consumed)
                consumed_quantity = sum((lot.quantity for lot in consumed), Decimal("0"))
                if abs(consumed_quantity - quantity) > TRANSFER_TOLERANCE:
                    complete = False
                continue
            if row.get("price") in (None, ""):
                complete = False
                continue
            lots.append(
                TransferInFifoLot(
                    quantity=quantity,
                    price=_decimal(row.get("price")),
                    enter_date=_parse_datetime(row.get("enter_date")),
                    source_broker=event.broker,
                    source_account=event.account_id,
                    source_file=str(row.get("source_report") or "") or None,
                    source_row=row_index,
                )
            )
        return lots, complete and abs(sum((lot.quantity for lot in lots), Decimal("0")) - abs(event.quantity)) <= TRANSFER_TOLERANCE


AccountRunner = Callable[
    [DiscoveredAccount, TransferInFifoResolver | None],
    AccountPipelineResult,
]


class FrontPipeline:
    """Run one complete client calculation across every discovered account."""

    def __init__(self, paths: ProjectPaths, *, account_runner: AccountRunner | None = None) -> None:
        self.paths = paths
        self._account_runner = account_runner or self._run_account
        self._report_metadata: dict[tuple[str, Path], DetectedReportMetadata] = {}

    def discover_accounts(self, client_id: str) -> tuple[DiscoveredAccount, ...]:
        self._report_metadata.clear()
        client_id = validate_client_id(client_id)
        clients_root = (self.paths.raw_data / "clients").resolve()
        client_root = (clients_root / client_id).resolve()
        if client_root.parent != clients_root:
            raise ValueError("client_id escapes the configured clients directory")
        if not client_root.is_dir():
            raise FileNotFoundError(f"Client raw report directory not found: {client_id}")

        grouped: dict[tuple[str, str], list[Path]] = defaultdict(list)
        for folder in sorted(client_root.iterdir(), key=lambda path: path.name.casefold()):
            if not folder.is_dir():
                raise ValueError(f"Unsupported item in client report directory: {folder.name}")
            folder_name = folder.name.casefold()
            broker = FRONT_BROKER_FOLDERS.get(folder_name)
            if broker is not None:
                self._collect_detected_accounts(grouped, broker, folder)
                continue
            if folder_name.startswith("freedom_"):
                account_id = folder.name[len("freedom_") :].strip()
                if not account_id:
                    raise ValueError("Freedom directory must be named freedom_<account_id>")
                self._collect_explicit_account(grouped, "freedom", account_id, folder)
                continue
            raise ValueError(f"Unsupported broker folder: {folder.name}")

        accounts = tuple(
            DiscoveredAccount(broker, account_id, tuple(sorted(paths, key=_path_sort_key)))
            for (broker, account_id), paths in sorted(grouped.items())
        )
        if not accounts:
            raise ValueError(f"No supported reports found for client {client_id}")
        self._validate_unique_account_ids(accounts)
        self._validate_report_periods(accounts)
        return accounts

    def run(
        self,
        *,
        client_id: str,
        tax_year: int,
        taxpayer: Mapping[str, Any],
        joint_accounts: Sequence[str] = (),
        acc_not_included_for_merged: Sequence[str] = (),
        allow_approximate_transfer_basis: bool = False,
        form270_05: bool = False,
        bank_infos: Mapping[str, BrokerBankInfo] | None = None,
    ) -> FrontPipelineResult:
        client_id = validate_client_id(client_id)
        accounts = self.discover_accounts(client_id)
        joint_ids = tuple(joint_accounts)
        if len(set(joint_ids)) != len(joint_ids):
            raise ValueError("joint_accounts contains duplicate account IDs")
        excluded_ids = tuple(acc_not_included_for_merged)
        if len(set(excluded_ids)) != len(excluded_ids):
            raise ValueError("acc_not_included_for_merged contains duplicate account IDs")
        joint_ids = _resolve_configured_account_ids(accounts, joint_ids, field_name="joint_accounts")
        excluded_ids = _resolve_configured_account_ids(
            accounts,
            excluded_ids,
            field_name="acc_not_included_for_merged",
        )
        if len(excluded_ids) == len(accounts):
            raise ValueError("acc_not_included_for_merged cannot exclude every discovered account")

        provisional = {
            (account.broker, account.account_id): self._account_runner(account, None)
            for account in accounts
        }
        individual_paths = _require_workbook_paths(accounts, provisional)
        resolution = GlobalTransferLedger().resolve(
            [(account, provisional[(account.broker, account.account_id)].dataset) for account in accounts]
        )
        diagnostic_path = self.paths.processed_data / f"{client_id}_missing_transfer_basis.json"
        merged_path = self.paths.processed_data / f"merged_{client_id}.xlsx"
        form_path = self.paths.output_data / f"270_{tax_year}_{client_id}_filled.json"

        if resolution.missing:
            _write_missing_diagnostic(diagnostic_path, client_id, resolution.missing)
        elif diagnostic_path.exists():
            diagnostic_path.unlink()

        if resolution.missing and not allow_approximate_transfer_basis:
            _remove_if_exists(merged_path)
            _remove_if_exists(form_path)
            return FrontPipelineResult(
                client_id=client_id,
                tax_year=tax_year,
                discovered_accounts=accounts,
                individual_workbook_paths=individual_paths,
                joint_workbook_paths=(),
                final_merge_input_paths=(),
                merged_workbook_path=None,
                form270_paths=(),
                missing_transfer_basis=resolution.missing,
                used_approximate_transfer_basis=False,
                completed=False,
            )

        final_results = provisional
        if resolution.has_resolved_transfers:
            final_results = {
                (account.broker, account.account_id): self._account_runner(
                    account, resolution.resolver_for(account.broker, account.account_id)
                )
                for account in accounts
            }
            individual_paths = _require_workbook_paths(accounts, final_results)

        joint_paths: list[Path] = []
        merge_inputs: list[Path] = []
        for account, ordinary_path in zip(accounts, individual_paths, strict=True):
            if account.account_id in joint_ids:
                joint_path = create_joint_audit_workbook(ordinary_path)
                joint_paths.append(joint_path)
                final_audit_path = joint_path
            else:
                final_audit_path = ordinary_path
            if account.account_id not in excluded_ids:
                merge_inputs.append(final_audit_path)

        merged_path.parent.mkdir(parents=True, exist_ok=True)
        if len(merge_inputs) == 1:
            shutil.copy2(merge_inputs[0], merged_path)
        else:
            merge_audit_workbooks(tuple(merge_inputs), merged_path)

        if form270_05:
            prepare_form270_05_trades_workbook(
                merged_path,
                AnnualFxRateProvider.from_nbk_rates_xlsx(self.paths.nbk_rates),
            )
        builder = Form270JsonBuilder(self.paths.form270_template)
        draft = builder.build_processed_workbook_draft(
            merged_path,
            tax_year=tax_year,
            taxpayer=taxpayer,
            broker="merged",
            account_id=client_id,
            form270_05=form270_05,
            bank_infos=bank_infos,
        )
        builder.save(draft, form_path)
        return FrontPipelineResult(
            client_id=client_id,
            tax_year=tax_year,
            discovered_accounts=accounts,
            individual_workbook_paths=individual_paths,
            joint_workbook_paths=tuple(joint_paths),
            final_merge_input_paths=tuple(merge_inputs),
            merged_workbook_path=merged_path,
            form270_paths=(form_path,),
            missing_transfer_basis=resolution.missing,
            used_approximate_transfer_basis=bool(resolution.missing),
            completed=True,
        )

    def _collect_detected_accounts(
        self,
        grouped: dict[tuple[str, str], list[Path]],
        broker: str,
        folder: Path,
    ) -> None:
        for report in self._reports_in_folder(broker, folder):
            metadata = detect_report_metadata(broker, report)
            self._report_metadata[(broker, report)] = metadata
            account_id = metadata.account_id
            account_id = account_id.strip() if account_id and account_id.strip() else None
            if not account_id:
                raise ValueError(f"Cannot detect account ID in {broker} report {report.name}")
            grouped[(broker, account_id)].append(report)

    def _collect_explicit_account(
        self,
        grouped: dict[tuple[str, str], list[Path]],
        broker: str,
        account_id: str,
        folder: Path,
    ) -> None:
        reports = self._reports_in_folder(broker, folder)
        for report in reports:
            self._report_metadata[(broker, report)] = detect_report_metadata(broker, report)
        grouped[(broker, account_id)].extend(reports)

    @staticmethod
    def _reports_in_folder(broker: str, folder: Path) -> tuple[Path, ...]:
        spec = BROKER_REPORT_SPECS[broker]
        reports: list[Path] = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.casefold() not in spec.extensions:
                raise ValueError(f"Unsupported report in {folder.name}: {path.name}")
            reports.append(path)
        if not reports:
            raise ValueError(f"Broker folder contains no reports: {folder.name}")
        return tuple(reports)

    @staticmethod
    def _validate_unique_account_ids(accounts: Sequence[DiscoveredAccount]) -> None:
        brokers_by_id: dict[str, set[str]] = defaultdict(set)
        for account in accounts:
            brokers_by_id[account.account_id].add(account.broker)
        ambiguous = sorted(account_id for account_id, brokers in brokers_by_id.items() if len(brokers) > 1)
        if ambiguous:
            raise ValueError(f"Account IDs are ambiguous across brokers: {', '.join(ambiguous)}")

    def _validate_report_periods(self, accounts: Sequence[DiscoveredAccount]) -> None:
        invalid: list[InvalidReportPeriod] = []
        for account in accounts:
            for report_path in account.report_paths:
                metadata = self._report_metadata.get((account.broker, report_path))
                if metadata is None:
                    metadata = detect_report_metadata(account.broker, report_path)
                period_end = metadata.period_end
                if period_end is None or (period_end.month, period_end.day) != (12, 31):
                    invalid.append(
                        InvalidReportPeriod(
                            broker=account.broker,
                            account_id=account.account_id,
                            report_name=report_path.name,
                            period_end=period_end,
                        )
                    )
        if invalid:
            raise InvalidReportPeriodError(invalid)

    def _run_account(
        self,
        account: DiscoveredAccount,
        resolver: TransferInFifoResolver | None,
    ) -> AccountPipelineResult:
        return AccountPipeline(self.paths, transfer_in_resolver=resolver).run_reports(
            AccountConfig(broker=account.broker, account_id=account.account_id),
            account.report_paths,
            write_excel=True,
            write_json=False,
        )


def _transfer_events(
    account: DiscoveredAccount,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[_TransferEvent, ...]:
    grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    ordinals: dict[tuple[Any, ...], int] = {}
    for ordinal, row in enumerate(rows):
        if row.get("_synthetic_reconciliation_adjustment"):
            continue
        direction = str(row.get("direction") or "").strip().lower()
        if direction == "in" and row.get("_broker_reported_transfer_basis"):
            continue
        if str(row.get("transfer_type") or "").strip().lower() != "security" or direction not in {"in", "out"}:
            continue
        key = (
            _parse_date(row.get("date")),
            direction,
            _text(row.get("symbol")),
            _text(row.get("isin")),
            _text(row.get("currency")),
            _text(row.get("asset_type")),
            _text(row.get("source_report")),
            _text(row.get("counterparty")),
            _text(row.get("_transfer_id")),
        )
        grouped[key].append(row)
        ordinals.setdefault(key, ordinal)
    return tuple(
        _TransferEvent(
            broker=account.broker,
            account_id=account.account_id,
            event_date=key[0],
            direction=key[1],
            symbol=key[2],
            isin=key[3],
            currency=key[4],
            asset_type=key[5],
            source_report=key[6],
            counterparty=key[7],
            rows=group_rows,
            ordinal=ordinals[key],
        )
        for key, group_rows in grouped.items()
    )


def _transferred_lot_consumptions(
    account: DiscoveredAccount,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[_TransferredLotConsumption, ...]:
    events: list[_TransferredLotConsumption] = []
    for ordinal, row in enumerate(rows):
        status = str(row.get("_source_opening_lot_status") or row.get("_opening_lot_status") or "").lower()
        if "transfer" not in status and not row.get("_fifo_source_broker"):
            continue
        quantity = abs(_decimal(row.get("exit_quantity")))
        if quantity <= 0:
            continue
        events.append(
            _TransferredLotConsumption(
                broker=account.broker,
                account_id=account.account_id,
                event_date=_parse_date(row.get("exit_date")),
                symbol=_text(row.get("symbol")),
                isin=_text(row.get("isin")),
                quantity=quantity,
                ordinal=ordinal,
            )
        )
    return tuple(events)


def _consume_propagated_lots(
    inventory: list[TransferInFifoLot],
    quantity: Decimal,
) -> list[TransferInFifoLot]:
    remaining = quantity
    consumed: list[TransferInFifoLot] = []
    index = 0
    while remaining > TRANSFER_TOLERANCE and index < len(inventory):
        lot = inventory[index]
        # Inventory already belongs to this account. Instrument compatibility is
        # represented by the transfer event that inserted it, so FIFO order is
        # sufficient for transfer-derived outgoing rows.
        matched = min(remaining, lot.quantity)
        consumed.append(
            TransferInFifoLot(
                quantity=matched,
                price=lot.price,
                enter_date=lot.enter_date,
                source_broker=lot.source_broker,
                source_account=lot.source_account,
                source_file=lot.source_file,
                source_row=lot.source_row,
            )
        )
        remaining -= matched
        if matched == lot.quantity:
            inventory.pop(index)
        else:
            inventory[index] = TransferInFifoLot(
                quantity=lot.quantity - matched,
                price=lot.price,
                enter_date=lot.enter_date,
                source_broker=lot.source_broker,
                source_account=lot.source_account,
                source_file=lot.source_file,
                source_row=lot.source_row,
            )
            index += 1
    return consumed


def _inventory_key(
    broker: str,
    account_id: str,
    isin: str | None,
    symbol: str | None,
) -> tuple[str, str, str, str]:
    return (broker, account_id, isin or "", symbol or "")


def _find_inventory(
    inventories: dict[tuple[str, str, str, str], list[TransferInFifoLot]],
    event: _TransferEvent | _TransferredLotConsumption,
) -> list[TransferInFifoLot]:
    exact_key = _inventory_key(event.broker, event.account_id, event.isin, event.symbol)
    if exact_key in inventories:
        return inventories[exact_key]
    symbol_matches = [
        inventory
        for (broker, account_id, _isin, symbol), inventory in inventories.items()
        if broker == event.broker and account_id == event.account_id and symbol == (event.symbol or "")
    ]
    return symbol_matches[0] if len(symbol_matches) == 1 else []


def _require_workbook_paths(
    accounts: Sequence[DiscoveredAccount],
    results: Mapping[tuple[str, str], AccountPipelineResult],
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for account in accounts:
        path = results[(account.broker, account.account_id)].workbook_path
        if path is None:
            raise RuntimeError(f"Account pipeline did not create an audit for {account.broker}:{account.account_id}")
        paths.append(path)
    return tuple(paths)


def _write_missing_diagnostic(
    path: Path,
    client_id: str,
    missing: Sequence[MissingTransferBasis],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "client_id": client_id,
        "missing_transfer_basis": [
            {
                **asdict(item),
                "transfer_date": item.transfer_date.isoformat() if item.transfer_date else None,
                "quantity": str(item.quantity),
            }
            for item in missing
        ],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _missing_basis(event: _TransferEvent, reason: str) -> MissingTransferBasis:
    return MissingTransferBasis(
        transfer_date=event.event_date,
        symbol=event.symbol,
        isin=event.isin,
        quantity=abs(event.quantity),
        currency=event.currency,
        destination_broker=event.broker,
        destination_account=event.account_id,
        reason=reason,
    )


def _event_sort_key(event: _TransferEvent | _TransferredLotConsumption) -> tuple[Any, ...]:
    if isinstance(event, _TransferredLotConsumption):
        priority = 2
    else:
        priority = 0 if event.direction == "out" else 1
    return (
        event.event_date or date.min,
        priority,
        event.broker,
        event.account_id,
        event.ordinal,
    )


def _missing_sort_key(item: MissingTransferBasis) -> tuple[Any, ...]:
    return (
        item.transfer_date or date.min,
        item.symbol or "",
        item.isin or "",
        item.destination_broker,
        item.destination_account,
        item.reason,
    )


def _request_key(request: TransferInRequest) -> tuple[Any, ...]:
    return (
        request.transfer_date,
        request.symbol,
        request.isin,
        abs(request.quantity),
        request.currency,
        request.asset_type,
        request.source_report,
        request.counterparty,
    )


def _path_sort_key(path: Path) -> tuple[str, str]:
    return (path.name.casefold(), str(path).casefold())


def _resolve_configured_account_ids(
    accounts: Sequence[DiscoveredAccount],
    requested_ids: Sequence[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    """Map user-facing account numbers to the exact discovered account IDs.

    IB's CSV sometimes renders the account as ``U123 (Custom Consolidated)``.
    That suffix describes the report type, not the brokerage account number, so
    client configuration may always use the plain ``U123`` value.
    """

    aliases: dict[str, set[str]] = defaultdict(set)
    for account in accounts:
        aliases[account.account_id].add(account.account_id)
        if account.broker == "ib":
            aliases[_ib_account_number(account.account_id)].add(account.account_id)

    resolved: list[str] = []
    unknown: list[str] = []
    ambiguous: list[str] = []
    for requested_id in requested_ids:
        matches = aliases.get(requested_id, set())
        if not matches:
            unknown.append(requested_id)
        elif len(matches) > 1:
            ambiguous.append(requested_id)
        else:
            resolved.append(next(iter(matches)))

    if unknown:
        raise ValueError(f"Unknown {field_name} account IDs: {', '.join(sorted(unknown))}")
    if ambiguous:
        raise ValueError(f"Ambiguous {field_name} account IDs: {', '.join(sorted(ambiguous))}")
    if len(set(resolved)) != len(resolved):
        raise ValueError(f"{field_name} contains duplicate account IDs")
    return tuple(resolved)


def _ib_account_number(account_id: str) -> str:
    return account_id.removesuffix(" (Custom Consolidated)").strip()


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        parsed_date = _parse_date(text)
        return datetime(parsed_date.year, parsed_date.month, parsed_date.day) if parsed_date else None


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except Exception:
        return Decimal("0")


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()
