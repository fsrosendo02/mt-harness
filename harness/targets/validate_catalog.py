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

    if isinstance(data, dict):
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

        if ("start_line" in entry) ^ ("end_line" in entry):
            raise ValueError(
                f"Entry {i} must define both start_line and end_line, or neither"
            )

        if "start_line" in entry:
            if not isinstance(entry["start_line"], int) or not isinstance(entry["end_line"], int):
                raise ValueError(f"Entry {i} start_line/end_line must be integers")
            if entry["start_line"] < 1 or entry["end_line"] < entry["start_line"]:
                raise ValueError(f"Entry {i} has invalid line bounds")

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
