"""End-to-end account and client pipeline orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kztax270.brokers.registry import BrokerRegistry, default_registry
from kztax270.calculations.tax_rules import TaxRuleEngine
from kztax270.canonical.schema import CanonicalDataset
from kztax270.excel.audit_workbook import ExcelAuditWorkbookWriter
from kztax270.form270.json_builder import Form270JsonBuilder
from kztax270.form270.merge import merge_form270_jsons
from kztax270.form270.split import split_form270_json
from kztax270.reference.fx import AnnualFxRateProvider
from kztax270.reference.nbk import ensure_nbk_rates_current
from kztax270.reference.securities import ensure_aix_instruments_current
from kztax270.reconciliation.engine import ReconciliationEngine
from kztax270.transfers import TransferInFifoResolver

from .config import AccountConfig, ClientConfig, ProjectPaths


@dataclass(slots=True)
class AccountPipelineResult:
    dataset: CanonicalDataset
    workbook_path: Path | None
    form_paths: dict[str, Path]
    reconciliation_error_count: int


class AccountPipeline:
    def __init__(
        self,
        paths: ProjectPaths,
        registry: BrokerRegistry | None = None,
        tax_engine: TaxRuleEngine | None = None,
        reconciliation_engine: ReconciliationEngine | None = None,
        workbook_writer: ExcelAuditWorkbookWriter | None = None,
        transfer_in_resolver: TransferInFifoResolver | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self.tax_engine = tax_engine or TaxRuleEngine()
        self.reconciliation_engine = reconciliation_engine or ReconciliationEngine()
        self.workbook_writer = workbook_writer or ExcelAuditWorkbookWriter()
        self.transfer_in_resolver = transfer_in_resolver

    def run_account(
        self,
        account: AccountConfig,
        *,
        tax_year: int | None = None,
        taxpayer: dict[str, object] | None = None,
        write_excel: bool = True,
        write_json: bool = True,
    ) -> AccountPipelineResult:
        adapter = self._registry_for_run().get(account.broker)
        reports = adapter.discover_reports(self.paths.raw_data, account.account_id)
        parse_result = adapter.parse_reports(reports, account.account_id)
        dataset = parse_result.dataset

        reconciliation_rows = [item.as_record() for item in self.reconciliation_engine.reconcile_dataset(dataset)]
        dataset.tables["Reconciliation"] = reconciliation_rows

        workbook_path = None
        if write_excel:
            workbook_path = self.paths.processed_data / f"{account.broker}_{account.account_id}_audit.xlsx"
            self.workbook_writer.write(dataset, workbook_path)

        form_paths: dict[str, Path] = {}
        if write_json:
            if tax_year is None:
                raise ValueError("tax_year is required when write_json=True")
            builder = Form270JsonBuilder(self.paths.form270_template)
            draft = builder.build_account_draft(dataset, tax_year=tax_year, taxpayer=taxpayer)
            if account.is_joint:
                for owner, form in split_form270_json(draft, account.joint_owners).items():
                    path = self.paths.output_data / f"270_{tax_year}_{account.broker}_{account.account_id}_{owner}.json"
                    builder.save(form, path)
                    form_paths[owner] = path
            else:
                path = self.paths.output_data / f"270_{tax_year}_{account.broker}_{account.account_id}.json"
                builder.save(draft, path)
                form_paths[account.account_id] = path

        error_count = sum(1 for row in reconciliation_rows if row.get("severity") == "error")
        return AccountPipelineResult(
            dataset=dataset,
            workbook_path=workbook_path,
            form_paths=form_paths,
            reconciliation_error_count=error_count,
        )

    def _registry_for_run(self) -> BrokerRegistry:
        ensure_nbk_rates_current(self.paths.nbk_rates)
        ensure_aix_instruments_current(self.paths.nbk_rates.parent / "aix_instruments.xlsx")
        if self.registry is not None:
            return self.registry
        fx_provider = AnnualFxRateProvider.from_nbk_rates_xlsx(self.paths.nbk_rates)
        return default_registry(fx_provider=fx_provider, transfer_in_resolver=self.transfer_in_resolver)


class ClientPipeline:
    def __init__(self, paths: ProjectPaths, account_pipeline: AccountPipeline | None = None) -> None:
        self.paths = paths
        self.account_pipeline = account_pipeline or AccountPipeline(paths)

    def run_client(self, client: ClientConfig, *, write_excel: bool = True) -> Path:
        forms = []
        for account in client.accounts:
            result = self.account_pipeline.run_account(
                account,
                tax_year=client.tax_year,
                taxpayer=client.taxpayer,
                write_excel=write_excel,
            )
            for path in result.form_paths.values():
                forms.append(_load_json(path))
        merged = merge_form270_jsons(forms)
        output_path = self.paths.output_data / f"270_{client.tax_year}_{client.client_id}_merged.json"
        Form270JsonBuilder(self.paths.form270_template).save(merged, output_path)
        return output_path


def _load_json(path: Path) -> dict[str, object]:
    import json

    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data
