"""Merge multiple account-level Form 270 JSON drafts."""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping


def merge_form270_jsons(forms: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    iterator = iter(forms)
    try:
        merged = copy.deepcopy(dict(next(iterator)))
    except StopIteration as exc:
        raise ValueError("At least one Form270 JSON is required for merge") from exc

    sources = [copy.deepcopy(merged.get("_kztax270", {}))]
    for form in iterator:
        sources.append(copy.deepcopy(form.get("_kztax270", {})))
        merged = _merge_values(merged, dict(form))

    merged.setdefault("_kztax270", {})
    merged["_kztax270"]["status"] = "merged_draft"
    merged["_kztax270"]["merged_sources"] = sources
    return merged


def _merge_values(left: Any, right: Any) -> Any:
    if isinstance(left, dict) and isinstance(right, Mapping):
        output = copy.deepcopy(left)
        for key, value in right.items():
            if key in output:
                output[key] = _merge_values(output[key], value)
            else:
                output[key] = copy.deepcopy(value)
        return output
    if isinstance(left, list) and isinstance(right, list):
        return copy.deepcopy(left) + copy.deepcopy(right)
    if left in (None, "", [], {}):
        return copy.deepcopy(right)
    if right in (None, "", [], {}) or left == right:
        return copy.deepcopy(left)
    return copy.deepcopy(left)
