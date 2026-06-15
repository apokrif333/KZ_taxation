"""Broker adapter registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import BrokerParser
from .exante import ExanteParser
from .freedom import FreedomParser
from .ib import InteractiveBrokersParser
from .tsifra import TsifraParser
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.transfers import TransferInFifoResolver
from .legacy_adapters import (
    ExanteLegacyAdapter,
    InteractiveBrokersLegacyAdapter,
    TsifraLegacyAdapter,
)


@dataclass(slots=True)
class BrokerRegistry:
    adapters: dict[str, BrokerParser] = field(default_factory=dict)

    def register(self, adapter: BrokerParser) -> None:
        self.adapters[adapter.broker_code] = adapter

    def get(self, broker_code: str) -> BrokerParser:
        try:
            return self.adapters[broker_code]
        except KeyError as exc:
            known = ", ".join(sorted(self.adapters))
            raise KeyError(f"Unknown broker {broker_code!r}. Registered brokers: {known}") from exc

    def broker_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.adapters))


def default_registry(
    fx_provider: AnnualFxRateProvider | None = None,
    transfer_in_resolver: TransferInFifoResolver | None = None,
) -> BrokerRegistry:
    registry = BrokerRegistry()
    registry.register(InteractiveBrokersParser(fx_provider=fx_provider, transfer_in_resolver=transfer_in_resolver))
    registry.register(FreedomParser(fx_provider=fx_provider, transfer_in_resolver=transfer_in_resolver))
    registry.register(ExanteParser(fx_provider=fx_provider, transfer_in_resolver=transfer_in_resolver))
    exante_legacy = ExanteLegacyAdapter()
    exante_legacy.broker_code = "exante_legacy"
    registry.register(exante_legacy)
    registry.register(TsifraParser(fx_provider=fx_provider, transfer_in_resolver=transfer_in_resolver))
    tsifra_legacy = TsifraLegacyAdapter()
    tsifra_legacy.broker_code = "tsifra_legacy"
    registry.register(tsifra_legacy)
    ib_legacy = InteractiveBrokersLegacyAdapter()
    ib_legacy.broker_code = "ib_legacy"
    registry.register(ib_legacy)
    return registry
