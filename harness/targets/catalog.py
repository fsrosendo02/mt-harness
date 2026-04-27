import json
from pathlib import Path
from typing import Any, Dict, List

from harness.storage.layout import LEGACY_DATASET_TARGETS_DIR, catalogs_root


def resolve_catalog_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.exists():
        return candidate

    try:
        relative_to_legacy = candidate.relative_to(LEGACY_DATASET_TARGETS_DIR)
    except ValueError:
        relative_to_legacy = None

    if relative_to_legacy is not None:
        migrated = catalogs_root() / relative_to_legacy
        if migrated.exists():
            return migrated

    fallback = catalogs_root() / candidate.name
    if fallback.exists():
        return fallback

    return candidate


def _normalize_catalog(data: Any) -> List[dict]:
    if isinstance(data, dict):
        data = data.get("targets", [])

    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON list or an object with a 'targets' list")

    return data


def load_catalog_entries(path: str) -> List[dict]:
    resolved = resolve_catalog_path(path)
    with open(resolved, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _normalize_catalog(data)


def load_catalog(path: str) -> Dict[str, dict]:
    return {entry["target_id"]: entry for entry in load_catalog_entries(path)}


def get_target_by_id(catalog_path, target_id):
    catalog = load_catalog(catalog_path)

    if target_id not in catalog:
        raise ValueError(f"Target '{target_id}' not found in catalog")

    return catalog[target_id]
