from __future__ import annotations

import csv
import json
from pathlib import Path


REQUIRED_FILES = (
    "results.csv",
    "summary.json",
    "run_manifest.json",
)


def validate_run_dir(run_dir: str | Path) -> dict:
    run_path = Path(run_dir)

    if not run_path.exists():
        raise FileNotFoundError(f"Run directory does not exist: {run_path}")

    for name in REQUIRED_FILES:
        path = run_path / name
        if not path.exists():
            raise FileNotFoundError(f"Missing required file: {path}")

    csv_path = run_path / "results.csv"
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError(f"results.csv is empty: {csv_path}")

    summary_path = run_path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    summary_total = summary["overall"]["total_mutants"]
    if summary_total != len(rows):
        raise ValueError(
            f"Summary total_mutants ({summary_total}) != CSV row count ({len(rows)})"
        )

    manifest_path = run_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_manifest_fields = (
        "created_at_utc",
        "run_mode",
        "subject",
        "target",
        "requested_mutant_count",
    )
    for field in required_manifest_fields:
        if field not in manifest:
            raise ValueError(f"Missing manifest field: {field}")

    return {
        "run_dir": str(run_path),
        "csv_row_count": len(rows),
        "summary_total_mutants": summary_total,
        "manifest_ok": True,
    }