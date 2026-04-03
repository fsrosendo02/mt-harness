from __future__ import annotations

import csv
import json
from pathlib import Path

from harness.reporting.summary import deduplicate_rows


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

    manifest_path = run_path / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_status = manifest.get("status", "unknown")

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

    if not rows:
        if run_status == "ok":
            raise ValueError(f"results.csv is empty for ok run: {csv_path}")

        return {
            "run_dir": str(run_path),
            "csv_row_count": 0,
            "summary_total_mutants": 0,
            "manifest_ok": True,
            "run_status": run_status,
        }

    summary_path = run_path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    summary_total = summary["overall"]["total_mutants"]
    summary_deduplicated = bool(summary.get("deduplicated", False))
    summary_used_row_count = summary.get("used_row_count")
    expected_total = (
        summary_used_row_count
        if summary_used_row_count is not None
        else len(deduplicate_rows(rows)) if summary_deduplicated else len(rows)
    )

    if summary_total != expected_total:
        raise ValueError(
            f"Summary total_mutants ({summary_total}) != effective CSV row count ({expected_total})"
        )

    return {
        "run_dir": str(run_path),
        "csv_row_count": len(rows),
        "effective_csv_row_count": expected_total,
        "summary_total_mutants": summary_total,
        "manifest_ok": True,
        "run_status": run_status,
    }
