import json


def load_catalog(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("targets", [])

    return {entry["target_id"]: entry for entry in data}


def get_target_by_id(catalog_path, target_id):
    catalog = load_catalog(catalog_path)

    if target_id not in catalog:
        raise ValueError(f"Target '{target_id}' not found in catalog")

    return catalog[target_id]