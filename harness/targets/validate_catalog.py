import json
import sys

REQUIRED_FIELDS = {
    "target_id",
    "dataset",
    "subject",
    "version",
    "language",
    "file",
    "function",
}


def validate_catalog(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON list")

    seen_ids = set()

    for i, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry {i} is not an object")

        missing = REQUIRED_FIELDS - set(entry.keys())
        if missing:
            raise ValueError(f"Entry {i} missing fields: {sorted(missing)}")

        tid = entry["target_id"]
        if tid in seen_ids:
            raise ValueError(f"Duplicate target_id: {tid}")
        seen_ids.add(tid)

    print(f"Catalog OK: {len(data)} targets")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 harness/targets/validate_catalog.py <catalog.json>")
        sys.exit(1)

    validate_catalog(sys.argv[1])