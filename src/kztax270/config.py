"""Project configuration loader."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AccountConfig:
    broker: str
    account_id: str
    raw_folder: str | None = None
    joint_owners: dict[str, Decimal] = field(default_factory=dict)

    @property
    def is_joint(self) -> bool:
        return bool(self.joint_owners)


@dataclass(frozen=True, slots=True)
class ClientConfig:
    client_id: str
    tax_year: int
    taxpayer: dict[str, Any]
    accounts: tuple[AccountConfig, ...]


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    raw_data: Path = Path("data/raw")
    processed_data: Path = Path("data/processed")
    output_data: Path = Path("data/output")
    nbk_rates: Path = Path("data/nb_rates.xlsx")
    reference_data: Path = Path("reference")
    form270_template: Path = Path("data/templates/270 new template.json")


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    paths: ProjectPaths
    clients: tuple[ClientConfig, ...]


def load_project_config(path: Path) -> ProjectConfig:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    paths = _load_paths(data.get("paths", {}))
    clients = tuple(_load_client(client) for client in data.get("clients", []))
    return ProjectConfig(paths=paths, clients=clients)


def _load_paths(data: dict[str, Any]) -> ProjectPaths:
    defaults = ProjectPaths()
    return ProjectPaths(
        raw_data=Path(data.get("raw_data", defaults.raw_data)),
        processed_data=Path(data.get("processed_data", defaults.processed_data)),
        output_data=Path(data.get("output_data", defaults.output_data)),
        nbk_rates=Path(data.get("nbk_rates", defaults.nbk_rates)),
        reference_data=Path(data.get("reference_data", defaults.reference_data)),
        form270_template=Path(data.get("form270_template", defaults.form270_template)),
    )


def _load_client(data: dict[str, Any]) -> ClientConfig:
    accounts = tuple(_load_account(account) for account in data.get("accounts", []))
    return ClientConfig(
        client_id=str(data["client_id"]),
        tax_year=int(data["tax_year"]),
        taxpayer=dict(data.get("taxpayer", {})),
        accounts=accounts,
    )


def _load_account(data: dict[str, Any]) -> AccountConfig:
    return AccountConfig(
        broker=str(data["broker"]),
        account_id=str(data["account_id"]),
        raw_folder=data.get("raw_folder"),
        joint_owners={owner: Decimal(str(ratio)) for owner, ratio in data.get("joint_owners", {}).items()},
    )
