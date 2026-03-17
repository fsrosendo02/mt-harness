from pathlib import Path
import csv
import json
import sys


REQUIRED_FILES = [
    "results.csv",
    "summary.json",
    "run_manifest.json",
]


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def warn(msg):
    print(f"[WARN] {msg}")


def ok(msg):
    print(f"[OK] {msg}")


def validate_files(run_dir: Path):
    for f in REQUIRED_FILES:
        path = run_dir / f
        if not path.exists():
            fail(f"Missing required file: {f}")
        ok(f"Found {f}")


def validate_csv(run_dir: Path):
    csv_path = run_dir / "results.csv"

    with csv_path.open("r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    if not reader:
        fail("results.csv is empty")

    ok(f"CSV has {len(reader)} mutants")

    return reader


def validate_summary(run_dir: Path, rows):
    summary_path = run_dir / "summary.json"

    data = json.loads(summary_path.read_text())

    total = data["overall"]["total_mutants"]

    if total != len(rows):
        fail(f"Summary total ({total}) != CSV rows ({len(rows)})")

    ok("Summary matches CSV row count")


def validate_manifest(run_dir: Path):
    manifest_path = run_dir / "run_manifest.json"

    data = json.loads(manifest_path.read_text())

    required_fields = [
        "created_at_utc",
        "run_mode",
        "subject",
        "target",
        "requested_mutant_count",
    ]

    for field in required_fields:
        if field not in data:
            fail(f"Missing manifest field: {field}")

    ok("Manifest structure valid")


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 validate_run.py <run_dir>")
        sys.exit(1)

    run_dir = Path(sys.argv[1])

    if not run_dir.exists():
        fail(f"Run directory does not exist: {run_dir}")

    print(f"Validating run: {run_dir}\n")

    validate_files(run_dir)
    rows = validate_csv(run_dir)
    validate_summary(run_dir, rows)
    validate_manifest(run_dir)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
