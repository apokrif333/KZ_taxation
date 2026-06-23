"""CLI for the Form 270 ETL pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from kztax270.brokers.registry import default_registry
from kztax270.config import (
    AccountConfig,
    Form270BankConfig,
    Form270FillConfig,
    Form270OwnerConfig,
    Form270RunConfig,
    ProjectPaths,
    load_form270_run_config,
    load_project_config,
)
from kztax270.form270.json_builder import BrokerBankInfo, Form270JsonBuilder, Form270Owner
from kztax270.reference.nbk import ensure_nbk_rates_current, upsert_nbk_average_annual_rates_xlsx
from kztax270.reference.repositories import ReferenceDataStore
from kztax270.transfers import InteractiveTransferInFifoResolver

from .pipeline import AccountPipeline, ClientPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kztax270")
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="List raw reports for one broker account")
    discover.add_argument("broker")
    discover.add_argument("account_id")
    discover.add_argument("--raw-root", default="data/raw")

    init_ref = sub.add_parser("init-reference", help="Create reference CSV files with headers")
    init_ref.add_argument("--root", default="reference")
    init_ref.add_argument("--nbk-xlsx", default=None, help="Import NBK average annual FX rates from xlsx.")

    update_nbk = sub.add_parser("update-nbk-rates", help="Update data/nb_rates.xlsx from NBK if previous year is missing")
    update_nbk.add_argument("--path", default="data/nb_rates.xlsx")

    run_account = sub.add_parser("run-account", help="Run one broker account pipeline")
    run_account.add_argument("broker")
    run_account.add_argument("account_id")
    run_account.add_argument("tax_year", type=int, nargs="?", help="Deprecated positional Form270 year.")
    run_account.add_argument("--form-year", type=int, default=None, help="Form270 JSON year. Not needed for Excel-only audit.")
    run_account.add_argument("--raw-root", default="data/raw")
    run_account.add_argument("--processed-root", default="data/processed")
    run_account.add_argument("--output-root", default="data/output")
    run_account.add_argument("--nbk-rates", default="data/nb_rates.xlsx")
    run_account.add_argument("--reference-root", default="reference")
    run_account.add_argument("--template", default="data/templates/270 new template.json")
    run_account.add_argument("--taxpayer-code", default=None)
    run_account.add_argument("--no-excel", action="store_true")
    run_account.add_argument("--no-json", action="store_true", help="Generate only the account audit dataset/workbook.")

    fill_270 = sub.add_parser("fill-270", help="Fill Form270 JSON from data/processed audit workbook")
    fill_270.add_argument("broker")
    fill_270.add_argument("account_id")
    fill_270.add_argument("--form-year", type=int, required=True)
    fill_270.add_argument("--processed-root", default="data/processed")
    fill_270.add_argument("--output-root", default="data/output")
    fill_270.add_argument("--template", default="data/templates/270 new template.json")
    fill_270.add_argument("--workbook", type=Path, default=None)
    fill_270.add_argument("--fio1", required=True, help="Фамилия владельца")
    fill_270.add_argument("--fio2", required=True, help="Имя владельца")
    fill_270.add_argument("--fio3", default="", help="Отчество владельца")
    fill_270.add_argument("--iin", required=True)
    fill_270.add_argument(
        "--joint-account",
        "--split-joint",
        dest="split_joint",
        action="store_true",
        help="Create two 50/50 forms for a joint account",
    )
    fill_270.add_argument("--second-fio1", default=None, help="Фамилия второго владельца")
    fill_270.add_argument("--second-fio2", default=None, help="Имя второго владельца")
    fill_270.add_argument("--second-fio3", default="", help="Отчество второго владельца")
    fill_270.add_argument("--second-iin", default=None)
    fill_270.add_argument("--civ-servant", action="store_true", help="Fill application_05 instead of application_04.B trades")
    fill_270.add_argument("--phone", default=None)
    fill_270.add_argument("--email", default=None)
    fill_270.add_argument("--ogd-residence", default=None)
    fill_270.add_argument("--ogd-location", default=None)
    fill_270.add_argument("--bank-code", default=None, help="Foreign bank/broker institution identifier for application_04.C")
    fill_270.add_argument("--bank-name", default=None)
    fill_270.add_argument("--bank-country", default=None)

    for command_name in ("run", "run-270"):
        run_270 = sub.add_parser(command_name, help="Fill Form270 JSONs from configs/form270.toml")
        run_270.add_argument("config", type=Path, nargs="?", default=Path("configs/form270.toml"))
        run_270.add_argument("--only", action="append", default=None, help="Optional broker:account_id or account_id filter")

    run = sub.add_parser("run-client", help="Run configured client pipeline")
    run.add_argument("config", type=Path)
    run.add_argument("client_id")
    run.add_argument("--no-excel", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        registry = default_registry()
        adapter = registry.get(args.broker)
        for report in adapter.discover_reports(Path(args.raw_root), args.account_id):
            print(report.path)
        return 0
    if args.command == "init-reference":
        store = ReferenceDataStore(Path(args.root))
        store.ensure_all()
        if args.nbk_xlsx:
            changed = upsert_nbk_average_annual_rates_xlsx(Path(args.nbk_xlsx), store)
            print(f"Imported NBK FX rate rows changed={changed}")
        print(f"Reference CSV files are ready under {args.root}")
        return 0
    if args.command == "update-nbk-rates":
        updated = ensure_nbk_rates_current(Path(args.path))
        print(f"nbk_rates={args.path}")
        print(f"updated={updated}")
        return 0
    if args.command == "run-account":
        form_year = args.form_year if args.form_year is not None else args.tax_year
        if not args.no_json and form_year is None:
            raise SystemExit("--form-year is required unless --no-json is used")
        paths = ProjectPaths(
            raw_data=Path(args.raw_root),
            processed_data=Path(args.processed_root),
            output_data=Path(args.output_root),
            nbk_rates=Path(args.nbk_rates),
            reference_data=Path(args.reference_root),
            form270_template=Path(args.template),
        )
        taxpayer = {"taxpayerCode": args.taxpayer_code} if args.taxpayer_code else None
        result = AccountPipeline(
            paths,
            transfer_in_resolver=InteractiveTransferInFifoResolver(paths.processed_data, raw_root=paths.raw_data),
        ).run_account(
            AccountConfig(broker=args.broker, account_id=args.account_id),
            tax_year=form_year,
            taxpayer=taxpayer,
            write_excel=not args.no_excel,
            write_json=not args.no_json,
        )
        if result.workbook_path:
            print(f"workbook={result.workbook_path}")
        for owner, path in result.form_paths.items():
            print(f"form[{owner}]={path}")
        print(f"reconciliation_rows={len(result.dataset.tables.get('Reconciliation', []))}")
        print(f"reconciliation_errors={result.reconciliation_error_count}")
        return 0
    if args.command == "fill-270":
        workbook_path = args.workbook or Path(args.processed_root) / f"{args.broker}_{args.account_id}_audit.xlsx"
        output_root = Path(args.output_root)
        builder = Form270JsonBuilder(Path(args.template))
        bank_info = _bank_info_from_args(args)
        first_owner = Form270Owner(args.fio1, args.fio2, args.fio3, args.iin)
        owners = [(first_owner, args.second_iin)]
        if args.split_joint:
            if not args.second_fio1 or not args.second_fio2 or not args.second_iin:
                raise SystemExit("--joint-account requires --second-fio1, --second-fio2 and --second-iin")
            second_owner = Form270Owner(args.second_fio1, args.second_fio2, args.second_fio3, args.second_iin)
            owners = [(first_owner, second_owner.iin), (second_owner, first_owner.iin)]

        for owner, spouse_iin in owners:
            taxpayer = _taxpayer_payload_from_args(args, owner, spouse_iin=spouse_iin if args.split_joint else None)
            draft = builder.build_processed_workbook_draft(
                workbook_path,
                tax_year=args.form_year,
                taxpayer=taxpayer,
                broker=args.broker,
                account_id=args.account_id,
                split=args.split_joint,
                civ_servant=args.civ_servant,
                bank_info=bank_info,
            )
            output_path = output_root / _form270_output_name(args.form_year, args.broker, args.account_id, owner)
            builder.save(draft, output_path)
            print(f"form[{owner.iin}]={output_path}")
        return 0
    if args.command in {"run", "run-270"}:
        config = load_form270_run_config(args.config)
        return _run_form270_config(config, only=args.only)
    if args.command == "run-client":
        config = load_project_config(args.config)
        clients = {client.client_id: client for client in config.clients}
        client = clients[args.client_id]
        output = ClientPipeline(config.paths).run_client(client, write_excel=not args.no_excel)
        print(output)
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def _run_form270_config(config: Form270RunConfig, *, only: list[str] | None = None) -> int:
    builder = Form270JsonBuilder(config.paths.form270_template)
    selected = set(only or [])
    written = 0
    for form in config.forms:
        if selected and not _form270_filter_matches(form, selected):
            continue
        bank_info = _bank_info_from_config(form.bank or config.banks.get(form.broker))
        tax_year = form.tax_year or config.defaults.tax_year
        joint_account = config.defaults.joint_account if form.joint_account is None else form.joint_account
        civ_servant = config.defaults.civ_servant if form.civ_servant is None else form.civ_servant
        workbook_path = form.workbook or config.paths.processed_data / f"{form.broker}_{form.account_id}_audit.xlsx"

        owners: list[tuple[Form270OwnerConfig, str | None]] = [(form.owner, None)]
        if joint_account:
            if form.second_owner is None:
                raise SystemExit(f"{form.broker}:{form.account_id} has joint_account=true but no second owner")
            owners = [(form.owner, form.second_owner.iin), (form.second_owner, form.owner.iin)]

        for owner_config, spouse_iin in owners:
            owner = _owner_from_config(owner_config)
            taxpayer = _taxpayer_payload_from_config(config, form, owner_config, spouse_iin=spouse_iin)
            draft = builder.build_processed_workbook_draft(
                workbook_path,
                tax_year=tax_year,
                taxpayer=taxpayer,
                broker=form.broker,
                account_id=form.account_id,
                split=joint_account,
                civ_servant=civ_servant,
                bank_info=bank_info,
            )
            output_path = config.paths.output_data / _form270_output_name(tax_year, form.broker, form.account_id, owner)
            builder.save(draft, output_path)
            written += 1
            print(f"form[{owner.iin}]={output_path}")
    if selected and written == 0:
        raise SystemExit(f"No form270 entries matched --only={', '.join(sorted(selected))}")
    print(f"forms_written={written}")
    return 0


def _form270_filter_matches(form: Form270FillConfig, selected: set[str]) -> bool:
    return form.account_id in selected or f"{form.broker}:{form.account_id}" in selected


def _bank_info_from_config(bank: Form270BankConfig | None) -> BrokerBankInfo | None:
    if bank is None:
        return None
    return BrokerBankInfo(code=bank.code, name=bank.name, country=bank.country)


def _owner_from_config(owner: Form270OwnerConfig) -> Form270Owner:
    return Form270Owner(owner.fio1, owner.fio2, owner.fio3, owner.iin)


def _taxpayer_payload_from_config(
    config: Form270RunConfig,
    form: Form270FillConfig,
    owner: Form270OwnerConfig,
    *,
    spouse_iin: str | None,
) -> dict[str, object]:
    return {
        "fio1": owner.fio1,
        "fio2": owner.fio2,
        "fio3": owner.fio3,
        "iin": owner.iin,
        "phone": form.phone if form.phone is not None else config.defaults.phone,
        "email": form.email if form.email is not None else config.defaults.email,
        "spouse_iin": spouse_iin,
        "ogdCodeByResidence": (
            form.ogd_residence if form.ogd_residence is not None else config.defaults.ogd_residence
        ),
        "ogdCodeByLocation": (
            form.ogd_location if form.ogd_location is not None else config.defaults.ogd_location
        ),
    }


def _bank_info_from_args(args: argparse.Namespace) -> BrokerBankInfo | None:
    supplied = [args.bank_code, args.bank_name, args.bank_country]
    if not any(supplied):
        return None
    if not all(supplied):
        raise SystemExit("--bank-code, --bank-name and --bank-country must be supplied together")
    return BrokerBankInfo(code=args.bank_code, name=args.bank_name, country=args.bank_country)


def _taxpayer_payload_from_args(args: argparse.Namespace, owner: Form270Owner, *, spouse_iin: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "fio1": owner.fio1,
        "fio2": owner.fio2,
        "fio3": owner.fio3,
        "iin": owner.iin,
        "phone": args.phone,
        "email": args.email,
        "spouse_iin": spouse_iin,
        "ogdCodeByResidence": args.ogd_residence,
        "ogdCodeByLocation": args.ogd_location,
    }
    return payload


def _form270_output_name(tax_year: int, broker: str, account_id: str, owner: Form270Owner) -> str:
    parts = ["270", str(tax_year), account_id, broker, owner.fio1, owner.fio2, owner.fio3, "filled"]
    return "_".join(_safe_filename_part(part) for part in parts if part) + ".json"


def _safe_filename_part(value: str) -> str:
    return "".join("_" if char in '<>:"/\\|?*' else char for char in str(value))


if __name__ == "__main__":
    raise SystemExit(main())
