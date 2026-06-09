"""Form 270 JSON draft builder."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kztax270.canonical.schema import CanonicalDataset


@dataclass(slots=True)
class Form270JsonBuilder:
    template_path: Path

    def load_template(self) -> dict[str, Any]:
        with self.template_path.open("r", encoding="utf-8-sig") as handle:
            template = json.load(handle)
        if not isinstance(template, dict):
            raise ValueError("Form 270 template must be a JSON object")
        return template

    def build_account_draft(
        self,
        dataset: CanonicalDataset,
        *,
        tax_year: int,
        taxpayer: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        draft = copy.deepcopy(self.load_template())
        draft["fnoYear"] = tax_year
        draft.setdefault("_kztax270", {})
        draft["_kztax270"].update(
            {
                "status": "draft",
                "broker": dataset.metadata.broker,
                "account_id": dataset.metadata.account_id,
                "ownership_ratio": str(dataset.metadata.ownership_ratio),
                "tax_engine": "stub",
            }
        )
        if taxpayer:
            draft.update({key: value for key, value in taxpayer.items() if value is not None})
        draft["_kztax270"]["years_results_rows"] = dataset.tables.get("Years_Results", [])
        draft["_kztax270"]["reconciliation_rows"] = dataset.tables.get("Reconciliation", [])
        return draft

    def save(self, form: Mapping[str, Any], output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(form, handle, ensure_ascii=False, indent=2)
        return output_path
