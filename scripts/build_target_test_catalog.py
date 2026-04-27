#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any

from harness.targets.catalog import load_catalog_entries


TARGET_COVERAGE_DIR = Path("harness/experiments/target_coverage")
CATALOGS_DIR = TARGET_COVERAGE_DIR / "catalogs"
COMBINED_TARGET_TESTS = TARGET_COVERAGE_DIR / "target_tests.csv"

TARGET_TESTS_FIELDNAMES = [
    "catalog",
    "dataset",
    "subject_id",
    "version",
    "project",
    "target_id",
    "file_path",
    "start_line",
    "end_line",
    "test_name",
    "coverage_source",
]


def project_from_subject(subject_id: str) -> str:
    return subject_id.split("_", 1)[0] if subject_id else "unknown"


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "catalog"


def catalog_name(catalog_path: Path) -> str:
    return safe_name(catalog_path.stem)


def row_for_entry(catalog: str, entry: dict[str, Any]) -> dict[str, Any]:
    subject_id = entry.get("subject") or entry.get("subject_id") or ""
    return {
        "catalog": catalog,
        "dataset": entry.get("dataset", ""),
        "subject_id": subject_id,
        "version": entry.get("version", ""),
        "project": project_from_subject(subject_id),
        "target_id": entry.get("target_id", ""),
        "file_path": entry.get("file", ""),
        "start_line": entry.get("start_line", ""),
        "end_line": entry.get("end_line", ""),
        "test_name": "",
        "coverage_source": "",
    }


def write_catalog_template(catalog_path: Path, output_dir: Path, *, force: bool = False) -> Path:
    name = catalog_name(catalog_path)
    entries = load_catalog_entries(str(catalog_path))
    catalog_dir = output_dir / name
    catalog_dir.mkdir(parents=True, exist_ok=True)
    output_path = catalog_dir / "target_tests.csv"

    if output_path.exists() and not force:
        return output_path

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_TESTS_FIELDNAMES)
        writer.writeheader()
        for entry in entries:
            writer.writerow(row_for_entry(name, entry))

    return output_path


def combine_catalog_target_tests(catalogs_dir: Path, output_path: Path) -> Path:
    rows: list[dict[str, str]] = []
    for path in sorted(catalogs_dir.glob("*/target_tests.csv")):
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                test_name = (row.get("test_name") or "").strip()
                if not test_name:
                    continue
                rows.append({field: row.get(field, "") for field in TARGET_TESTS_FIELDNAMES})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_TESTS_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create per-catalog target test mapping files and optionally combine "
            "populated mappings into the target_coverage target_tests.csv."
        )
    )
    parser.add_argument(
        "catalogs",
        nargs="*",
        help="Catalog JSON files to initialize.",
    )
    parser.add_argument(
        "--catalog-glob",
        default="harness/targets/*.json",
        help="Glob used when no explicit catalog paths are provided.",
    )
    parser.add_argument("--output-dir", default=str(CATALOGS_DIR))
    parser.add_argument(
        "--combine",
        action="store_true",
        help="Combine populated per-catalog target_tests.csv files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing per-catalog target_tests.csv templates.",
    )
    parser.add_argument("--combined-output", default=str(COMBINED_TARGET_TESTS))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog_paths = [Path(path) for path in args.catalogs]
    if not catalog_paths:
        catalog_paths = sorted(Path().glob(args.catalog_glob))

    written = []
    for catalog_path in catalog_paths:
        if not catalog_path.exists():
            raise FileNotFoundError(f"Catalog not found: {catalog_path}")
        written.append(write_catalog_template(catalog_path, output_dir, force=args.force))

    for path in written:
        print(f"Wrote catalog target-test template: {path}")

    if args.combine:
        combined = combine_catalog_target_tests(output_dir, Path(args.combined_output))
        print(f"Combined populated target tests into: {combined}")


if __name__ == "__main__":
    main()
