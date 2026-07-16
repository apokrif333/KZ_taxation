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


@dataclass(frozen=True, slots=True)
class Form270OwnerConfig:
    fio1: str
    fio2: str
    fio3: str
    iin: str


@dataclass(frozen=True, slots=True)
class Form270BankConfig:
    code: str
    name: str
    country: str


@dataclass(frozen=True, slots=True)
class Form270DefaultsConfig:
    tax_year: int | None = None
    joint_account: bool = False
    civ_servant: bool = False
    phone: str | None = None
    email: str | None = None
    ogd_residence: str | None = None
    ogd_location: str | None = None


@dataclass(frozen=True, slots=True)
class Form270FillConfig:
    broker: str
    account_id: str
    owner: Form270OwnerConfig
    tax_year: int | None = None
    workbook: Path | None = None
    workbooks: tuple[Path, ...] = ()
    second_owner: Form270OwnerConfig | None = None
    joint_account: bool | None = None
    civ_servant: bool | None = None
    phone: str | None = None
    email: str | None = None
    ogd_residence: str | None = None
    ogd_location: str | None = None
    bank: Form270BankConfig | None = None


@dataclass(frozen=True, slots=True)
class Form270RunConfig:
    paths: ProjectPaths
    defaults: Form270DefaultsConfig
    banks: dict[str, Form270BankConfig]
    forms: tuple[Form270FillConfig, ...] = ()
    jobs: tuple["Form270JobConfig", ...] = ()


@dataclass(frozen=True, slots=True)
class Form270JobConfig:
    """One config-driven task executed by ``kztax270 run-270``."""

    mode: str
    broker: str | None = None
    account_id: str | None = None
    owner: Form270OwnerConfig | None = None
    tax_year: int | None = None
    workbook: Path | None = None
    workbooks: tuple[Path, ...] = ()
    second_owner: Form270OwnerConfig | None = None
    joint_account: bool | None = None
    civ_servant: bool | None = None
    phone: str | None = None
    email: str | None = None
    ogd_residence: str | None = None
    ogd_location: str | None = None
    bank: Form270BankConfig | None = None
    job_id: str | None = None


def load_project_config(path: Path) -> ProjectConfig:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    paths = _load_paths(data.get("paths", {}))
    clients = tuple(_load_client(client) for client in data.get("clients", []))
    return ProjectConfig(paths=paths, clients=clients)


def load_form270_run_config(path: Path = Path("configs/form270.toml")) -> Form270RunConfig:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    paths = _load_paths(data.get("paths", {}))
    section = data.get("form270", {})
    if not section:
        raise ValueError("Config must contain [form270] section")
    defaults = _load_form270_defaults(section)
    banks = {
        str(broker): _load_form270_bank(bank)
        for broker, bank in section.get("banks", {}).items()
    }
    forms = tuple(_load_form270_fill(form) for form in section.get("forms", []))
    jobs = tuple(_load_form270_job(job) for job in section.get("jobs", []))
    if forms and jobs:
        raise ValueError("Use either [[form270.jobs]] or legacy [[form270.forms]], not both")
    if not forms and not jobs:
        raise ValueError("Config must contain at least one [[form270.jobs]] entry")
    return Form270RunConfig(paths=paths, defaults=defaults, banks=banks, forms=forms, jobs=jobs)


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


def _load_form270_defaults(data: dict[str, Any]) -> Form270DefaultsConfig:
    return Form270DefaultsConfig(
        tax_year=int(data["tax_year"]) if data.get("tax_year") is not None else None,
        joint_account=_load_bool(data, ("joint_account", "split_joint"), default=False),
        civ_servant=bool(data.get("civ_servant", False)),
        phone=data.get("phone"),
        email=data.get("email"),
        ogd_residence=data.get("ogd_residence"),
        ogd_location=data.get("ogd_location"),
    )


def _load_form270_fill(data: dict[str, Any]) -> Form270FillConfig:
    workbook = Path(data["workbook"]) if data.get("workbook") else None
    workbooks = _load_workbook_list(data.get("workbooks"))
    if workbook is not None and workbooks:
        raise ValueError("Form270 entry cannot contain both workbook and workbooks")
    return Form270FillConfig(
        broker=str(data["broker"]),
        account_id=str(data["account_id"]),
        owner=_load_form270_owner(data, prefix=""),
        tax_year=int(data["tax_year"]) if data.get("tax_year") is not None else None,
        workbook=workbook,
        workbooks=workbooks,
        second_owner=_load_form270_owner(data, prefix="second_") if data.get("second_iin") else None,
        joint_account=_load_optional_bool(data, ("joint_account", "split_joint")),
        civ_servant=bool(data["civ_servant"]) if data.get("civ_servant") is not None else None,
        phone=data.get("phone"),
        email=data.get("email"),
        ogd_residence=data.get("ogd_residence"),
        ogd_location=data.get("ogd_location"),
        bank=_load_form270_bank(data, prefix="bank_") if data.get("bank_code") else None,
    )


def _load_form270_job(data: dict[str, Any]) -> Form270JobConfig:
    job_id = str(data.get("id", "")).strip()
    if not job_id:
        raise ValueError("Each [[form270.jobs]] entry requires id")
    mode, joint_account_mode = _job_mode_from_id(job_id)
    broker = str(data["broker"]) if data.get("broker") else None
    account_id = str(data["account_id"]) if data.get("account_id") else None
    workbook = Path(data["file_name"]) if data.get("file_name") else None
    workbooks = _load_workbook_list(data.get("workbooks"))
    if workbook is not None and workbooks:
        raise ValueError("Form270 job cannot contain both file_name and workbooks")

    requires_account = mode == "excel"
    requires_owner = mode in {"merge_excel", "json"}
    requires_workbooks = mode == "merge_excel"
    if requires_account and (not broker or not account_id):
        raise ValueError(f"form270 job id={job_id} requires broker and account_id")
    if requires_workbooks and not workbooks:
        raise ValueError(f"form270 job id={job_id} requires workbooks")
    if mode == "json" and workbook is None:
        raise ValueError(f"form270 job id={job_id} requires file_name")
    if mode == "excel" and (workbook is not None or workbooks):
        raise ValueError("form270 job id=excel reads raw reports and does not accept file_name/workbooks")
    if mode == "json" and workbooks:
        raise ValueError("form270 JSON jobs accept one file_name; run merge_excel before them")
    if mode == "merge_excel" and workbook is not None:
        raise ValueError("form270 job id=merge_excel accepts workbooks, not file_name")

    owner = (
        _load_form270_owner(data, prefix="", require_iin=mode != "merge_excel")
        if requires_owner
        else None
    )
    return Form270JobConfig(
        mode=mode,
        broker=broker,
        account_id=account_id,
        owner=owner,
        tax_year=int(data["tax_year"]) if data.get("tax_year") is not None else None,
        workbook=workbook,
        workbooks=workbooks,
        second_owner=_load_form270_owner(data, prefix="second_") if data.get("second_iin") else None,
        joint_account=joint_account_mode,
        civ_servant=bool(data["civ_servant"]) if data.get("civ_servant") is not None else None,
        phone=data.get("phone"),
        email=data.get("email"),
        ogd_residence=data.get("ogd_residence"),
        ogd_location=data.get("ogd_location"),
        bank=_load_form270_bank(data, prefix="bank_") if data.get("bank_code") else None,
        job_id=job_id,
    )


def _job_mode_from_id(job_id: str) -> tuple[str, bool]:
    modes = {
        "excel": ("excel", False),
        "merge_excel": ("merge_excel", False),
        "270_json": ("json", False),
        "270_joint_json": ("json", True),
    }
    try:
        return modes[job_id]
    except KeyError as exc:
        raise ValueError(
            "form270 job id must be one of: excel, merge_excel, 270_json, 270_joint_json"
        ) from exc


def _load_workbook_list(value: Any) -> tuple[Path, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        raise ValueError("workbooks must be a TOML list or a comma-separated string")
    paths = tuple(Path(item) for item in items if item)
    if paths and len(paths) < 2:
        raise ValueError("workbooks must contain at least two Excel files")
    return paths


def _load_form270_owner(
    data: dict[str, Any], *, prefix: str, require_iin: bool = True
) -> Form270OwnerConfig:
    fields = [f"{prefix}fio1", f"{prefix}fio2"]
    if require_iin:
        fields.append(f"{prefix}iin")
    missing = [key for key in fields if not data.get(key)]
    if missing:
        raise ValueError(f"Missing Form270 owner fields: {', '.join(missing)}")
    return Form270OwnerConfig(
        fio1=str(data[f"{prefix}fio1"]),
        fio2=str(data[f"{prefix}fio2"]),
        fio3=str(data.get(f"{prefix}fio3", "")),
        iin=str(data.get(f"{prefix}iin", "")),
    )


def _load_form270_bank(data: dict[str, Any], prefix: str = "") -> Form270BankConfig:
    missing = [key for key in (f"{prefix}code", f"{prefix}name", f"{prefix}country") if not data.get(key)]
    if missing:
        raise ValueError(f"Missing Form270 bank fields: {', '.join(missing)}")
    return Form270BankConfig(
        code=str(data[f"{prefix}code"]),
        name=str(data[f"{prefix}name"]),
        country=str(data[f"{prefix}country"]),
    )


def _load_bool(data: dict[str, Any], keys: tuple[str, ...], *, default: bool) -> bool:
    value = _load_optional_bool(data, keys)
    return default if value is None else value


def _load_optional_bool(data: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key in data and data[key] is not None:
            return bool(data[key])
    return None
