from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.targets.ids import format_target_id

REQUIRED_FIELDS = {
    "target_id",
    "dataset",
    "subject",
    "version",
    "language",
    "file",
    "function",
}
CATALOG_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*_catalog$")


def validate_catalog(path: str | Path) -> list[dict]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    top_level_dataset = None
    filename_stem = path.stem

    if isinstance(data, dict):
        catalog_name = str(data.get("catalog_name") or "").strip()
        if not catalog_name:
            raise ValueError("Catalog object must define a non-empty 'catalog_name'")
        if not CATALOG_NAME_PATTERN.match(catalog_name):
            raise ValueError(
                "catalog_name must match '<dataset>_<name>_catalog' using lowercase snake_case"
            )
        if filename_stem != catalog_name:
            raise ValueError(
                f"Catalog filename stem {filename_stem!r} must match catalog_name {catalog_name!r}"
            )

        top_level_dataset = str(data.get("dataset") or "").strip()
        if not top_level_dataset:
            raise ValueError("Catalog object must define a non-empty top-level 'dataset'")
        if not catalog_name.startswith(f"{top_level_dataset.lower()}_"):
            raise ValueError(
                f"catalog_name {catalog_name!r} must start with dataset prefix {top_level_dataset.lower()!r}"
            )

        data = data.get("targets", [])

    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON list or an object with a 'targets' list")

    seen_ids = set()

    for i, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry {i} is not an object")

        missing = REQUIRED_FIELDS - set(entry.keys())
        if missing:
            raise ValueError(f"Entry {i} missing fields: {sorted(missing)}")

        if top_level_dataset and str(entry.get("dataset") or "").strip() != top_level_dataset:
            raise ValueError(
                f"Entry {i} dataset {entry.get('dataset')!r} does not match top-level dataset {top_level_dataset!r}"
            )

        if ("start_line" in entry) ^ ("end_line" in entry):
            raise ValueError(
                f"Entry {i} must define both start_line and end_line, or neither"
            )

        if "start_line" in entry:
            if not isinstance(entry["start_line"], int) or not isinstance(entry["end_line"], int):
                raise ValueError(f"Entry {i} start_line/end_line must be integers")
            if entry["start_line"] < 1 or entry["end_line"] < entry["start_line"]:
                raise ValueError(f"Entry {i} has invalid line bounds")
            expected_target_id = format_target_id(
                subject=entry["subject"],
                version=entry["version"],
                function=entry["function"],
                start_line=entry["start_line"],
                end_line=entry["end_line"],
            )
            if entry["target_id"] != expected_target_id:
                raise ValueError(
                    f"Entry {i} has target_id={entry['target_id']!r}, "
                    f"expected {expected_target_id!r}"
                )

        tid = entry["target_id"]
        if tid in seen_ids:
            raise ValueError(f"Duplicate target_id: {tid}")
        seen_ids.add(tid)

    return data


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 -m harness.targets.validation <catalog.json>")
        raise SystemExit(1)

    entries = validate_catalog(sys.argv[1])
    print(f"Catalog OK: {len(entries)} targets")


if __name__ == "__main__":
    main()
