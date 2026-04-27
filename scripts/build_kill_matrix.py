#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


RUNS_DIR = Path("harness/runs")
ANALYSIS_DIR = Path("harness/experiments/target_coverage")
OUTPUT_DIR = ANALYSIS_DIR / "kill_matrices"
TARGET_TESTS_CSV = ANALYSIS_DIR / "target_tests.csv"
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


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def project_from_subject(subject_id: str) -> str:
    return subject_id.split("_", 1)[0] if subject_id else "unknown"


def group_key(row: dict[str, str], manifest: dict[str, Any], group_by: str) -> str:
    subject_id = row.get("subject_id", "")
    if group_by == "project":
        return project_from_subject(subject_id)
    if group_by == "subject":
        return subject_id or "unknown"
    if group_by == "run":
        return row.get("run_name") or manifest.get("run_name") or "unknown"
    raise ValueError(f"Unsupported group-by: {group_by}")


def mutant_test_section(log_text: str) -> str:
    marker = "=== MUTANT TEST ==="
    if marker not in log_text:
        return ""
    return log_text.split(marker, 1)[1]


def extract_failing_tests_from_log(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []

    section = mutant_test_section(log_path.read_text(encoding="utf-8", errors="replace"))
    if not section:
        return []

    failing_count_seen = False
    failing_tests: list[str] = []

    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("Failing tests:"):
            failing_count_seen = True
            continue

        if not failing_count_seen:
            continue

        match = re.match(r"^-\s+(.+?::.+?)\s*$", stripped)
        if match:
            failing_tests.append(match.group(1).strip())
            continue

        if failing_tests and stripped and not stripped.startswith("-"):
            break

    return failing_tests


def run_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run_manifest.json"
    if not path.exists():
        return {}
    return load_json(path)


def read_results(run_dir: Path) -> list[dict[str, str]]:
    path = run_dir / "execution" / "results.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_target_tests(path: Path | None) -> dict[tuple[str, str, str], set[str]]:
    if path is None or not path.exists():
        return {}

    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    target_tests: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        test_name = (row.get("test_name") or "").strip()
        if not test_name:
            continue

        key = (
            (row.get("dataset") or "").strip(),
            (row.get("subject_id") or "").strip(),
            (row.get("target_id") or "").strip(),
        )
        target_tests[key].add(test_name)

    return target_tests


def target_test_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(record.get("dataset") or "").strip(),
        str(record.get("subject_id") or "").strip(),
        str(record.get("target_id") or "").strip(),
    )


def target_tests_for(
    record: dict[str, Any],
    target_tests: dict[tuple[str, str, str], set[str]],
) -> set[str]:
    return target_tests.get(target_test_key(record), set())


def collect_records(
    runs_dir: Path,
    group_by: str,
    target_tests: dict[tuple[str, str, str], set[str]],
) -> dict[str, list[dict[str, Any]]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue

        manifest = run_manifest(run_dir)
        results = read_results(run_dir)
        if not results:
            continue

        target = manifest.get("target", {})

        for row in results:
            key = group_key(row, manifest, group_by)
            log_path_text = row.get("log_path", "")
            log_path = Path(log_path_text)
            if log_path_text and not log_path.is_absolute():
                log_path = Path.cwd() / log_path

            failing_tests = extract_failing_tests_from_log(log_path)
            target_id = row.get("target_id", "") or target.get("target_id", "")
            record_target_tests = target_tests.get(
                (
                    row.get("dataset", ""),
                    row.get("subject_id", ""),
                    target_id,
                ),
                set(),
            )
            by_group[key].append(
                {
                    "dataset": row.get("dataset", ""),
                    "subject_id": row.get("subject_id", ""),
                    "project": project_from_subject(row.get("subject_id", "")),
                    "run_name": row.get("run_name", "") or run_dir.name,
                    "target_id": target_id,
                    "function_name": row.get("function_name", ""),
                    "mutant_id": row.get("mutant_id", ""),
                    "mutant_hash": row.get("mutant_hash", ""),
                    "build_status": row.get("build_status", ""),
                    "test_status": row.get("test_status", ""),
                    "killed": parse_bool(row.get("killed", "")),
                    "executable": parse_bool(row.get("executable", "")),
                    "log_path": row.get("log_path", ""),
                    "failing_tests": sorted(set(failing_tests)),
                    "target_tests": sorted(record_target_tests),
                }
            )

    return by_group


def outcome_for(record: dict[str, Any], test_name: str) -> str:
    if record["build_status"] == "BASELINE_FAIL" or record["test_status"] == "BASELINE_FAIL":
        return "BASELINE_FAIL"
    if record["build_status"] != "SUCCESS":
        return "BUILD_FAIL"
    if record["test_status"] == "NOT_RUN":
        return "NOT_RUN"
    if test_name in record["failing_tests"]:
        return "FAIL"
    return "PASS"


def tests_for_group(records: list[dict[str, Any]], *, use_target_tests: bool) -> list[str]:
    if use_target_tests:
        tests = {test for record in records for test in record["target_tests"]}
        if tests:
            return sorted(tests)
    return sorted({test for record in records for test in record["failing_tests"]})


def write_group_outputs(
    group: str,
    records: list[dict[str, Any]],
    output_dir: Path,
    *,
    use_target_tests: bool,
) -> tuple[Path, Path]:
    tests = tests_for_group(records, use_target_tests=use_target_tests)
    safe_group = re.sub(r"[^A-Za-z0-9_.-]+", "_", group).strip("_") or "unknown"
    wide_path = output_dir / f"{safe_group}_kill_matrix.csv"
    long_path = output_dir / f"{safe_group}_kill_matrix_long.csv"

    metadata_fields = [
        "project",
        "subject_id",
        "run_name",
        "target_id",
        "function_name",
        "mutant_id",
        "mutant_hash",
        "build_status",
        "test_status",
        "killed",
        "executable",
        "target_test_count",
        "failing_test_count",
        "log_path",
    ]

    with wide_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=metadata_fields + tests)
        writer.writeheader()
        for record in records:
            row = {field: record.get(field, "") for field in metadata_fields}
            row["target_test_count"] = len(record["target_tests"])
            row["failing_test_count"] = len(record["failing_tests"])
            for test in tests:
                row[test] = outcome_for(record, test)
            writer.writerow(row)

    with long_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = metadata_fields + ["test_name", "outcome"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            base = {field: record.get(field, "") for field in metadata_fields}
            base["target_test_count"] = len(record["target_tests"])
            base["failing_test_count"] = len(record["failing_tests"])
            for test in tests:
                row = dict(base)
                row["test_name"] = test
                row["outcome"] = outcome_for(record, test)
                writer.writerow(row)

    return wide_path, long_path


def write_summary(
    groups: dict[str, list[dict[str, Any]]],
    output_dir: Path,
    *,
    use_target_tests: bool,
) -> Path:
    path = output_dir / "kill_matrix_summary.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "group",
            "mutants",
            "killed_mutants",
            "unique_target_tests",
            "unique_failing_tests",
            "kill_events",
            "test_universe",
            "wide_csv",
            "long_csv",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for group, records in sorted(groups.items()):
            target_tests = {test for record in records for test in record["target_tests"]}
            failing_tests = {test for record in records for test in record["failing_tests"]}
            safe_group = re.sub(r"[^A-Za-z0-9_.-]+", "_", group).strip("_") or "unknown"
            writer.writerow(
                {
                    "group": group,
                    "mutants": len(records),
                    "killed_mutants": sum(1 for record in records if record["killed"]),
                    "unique_target_tests": len(target_tests),
                    "unique_failing_tests": len(failing_tests),
                    "kill_events": sum(len(record["failing_tests"]) for record in records),
                    "test_universe": "target_tests" if use_target_tests else "observed_failing_tests",
                    "wide_csv": str(output_dir / f"{safe_group}_kill_matrix.csv"),
                    "long_csv": str(output_dir / f"{safe_group}_kill_matrix_long.csv"),
                }
            )
    return path


def write_target_tests_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_TESTS_FIELDNAMES)
        writer.writeheader()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build kill matrices from harness execution logs."
    )
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument(
        "--target-tests",
        default=str(TARGET_TESTS_CSV),
        help=(
            "CSV mapping target_id to tests that cover the target. "
            "If missing or empty, falls back to observed failing tests."
        ),
    )
    parser.add_argument(
        "--group-by",
        choices=["project", "subject", "run"],
        default="project",
        help="How to split output matrices.",
    )
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_tests_path = Path(args.target_tests) if args.target_tests else None
    if target_tests_path is not None:
        write_target_tests_template(target_tests_path)

    target_tests = load_target_tests(target_tests_path)
    use_target_tests = bool(target_tests)

    groups = collect_records(runs_dir, args.group_by, target_tests)
    for group, records in sorted(groups.items()):
        write_group_outputs(
            group,
            records,
            output_dir,
            use_target_tests=use_target_tests,
        )

    summary_path = write_summary(groups, output_dir, use_target_tests=use_target_tests)
    print(f"Built kill matrices for {len(groups)} {args.group_by} group(s)")
    print(
        "Test universe: "
        + ("target_tests" if use_target_tests else "observed_failing_tests")
    )
    if target_tests_path is not None:
        print(f"Target tests CSV: {target_tests_path}")
    print(f"Summary written to: {summary_path}")


if __name__ == "__main__":
    main()
