import json
from typing import Any, Dict, List


def _normalize_catalog(data: Any) -> List[dict]:
    if isinstance(data, dict):
        data = data.get("targets", [])

    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON list or an object with a 'targets' list")

    return data


def load_catalog_entries(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _normalize_catalog(data)


def load_catalog(path: str) -> Dict[str, dict]:
    return {entry["target_id"]: entry for entry in load_catalog_entries(path)}


def get_target_by_id(catalog_path, target_id):
    catalog = load_catalog(catalog_path)

    if target_id not in catalog:
        raise ValueError(f"Target '{target_id}' not found in catalog")

    return catalog[target_id]
