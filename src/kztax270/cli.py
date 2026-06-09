"""CLI for the Form 270 ETL pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from kztax270.brokers.registry import default_registry
from kztax270.config import AccountConfig, ProjectPaths, load_project_config
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
    if args.command == "run-client":
        config = load_project_config(args.config)
        clients = {client.client_id: client for client in config.clients}
        client = clients[args.client_id]
        output = ClientPipeline(config.paths).run_client(client, write_excel=not args.no_excel)
        print(output)
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
