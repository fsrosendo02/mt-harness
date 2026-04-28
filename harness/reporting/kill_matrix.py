#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.storage.layout import (
    LEGACY_KILL_MATRICES_DIR,
    LEGACY_RUNS_DIR,
    LEGACY_TARGET_TESTS_CSV,
    execution_results_path,
    execution_test_results_path,
    global_target_tests_csv_path,
    kill_matrices_dir,
    manifest_path,
    runs_root,
)

RUNS_DIR = runs_root() if runs_root().exists() else LEGACY_RUNS_DIR
OUTPUT_DIR = kill_matrices_dir() if kill_matrices_dir().exists() else LEGACY_KILL_MATRICES_DIR
TARGET_TESTS_CSV = (
    global_target_tests_csv_path() if global_target_tests_csv_path().exists() else LEGACY_TARGET_TESTS_CSV
)
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
    "match_mode",
]
KILL_OUTCOMES = {"FAIL", "ERROR"}


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def project_from_subject(subject_id: str) -> str:
    return subject_id.split("_", 1)[0] if subject_id else "unknown"


def group_key(record: dict[str, Any], group_by: str) -> str:
    subject_id = str(record.get("subject_id") or "")
    if group_by == "project":
        return project_from_subject(subject_id)
    if group_by == "subject":
        return subject_id or "unknown"
    if group_by == "run":
        return str(record.get("run_name") or "unknown")
    raise ValueError(f"Unsupported group-by: {group_by}")


def load_target_tests(path: Path | None) -> dict[tuple[str, str, str], list[str]]:
    if path is None or not path.exists():
        return {}

    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            test_name = str(row.get("test_name") or "").strip()
            if not test_name:
                continue
            key = (
                str(row.get("dataset") or "").strip(),
                str(row.get("subject_id") or "").strip(),
                str(row.get("target_id") or "").strip(),
            )
            grouped[key].add(test_name)
    return {key: sorted(values) for key, values in grouped.items()}


def write_target_tests_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_TESTS_FIELDNAMES)
        writer.writeheader()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run_manifest(run_dir: Path) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if not path.exists():
        return {}
    return load_json(path)


def legacy_failing_tests(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    marker = "=== MUTANT TEST ==="
    if marker not in log_text:
        return []

    section = log_text.split(marker, 1)[1]
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


def _record_counts(observations: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "target_test_count": sum(1 for obs in observations if parse_bool(obs.get("eligible"))),
        "executed_test_count": sum(1 for obs in observations if parse_bool(obs.get("executed"))),
        "failing_test_count": sum(1 for obs in observations if obs.get("outcome") == "FAIL"),
        "error_test_count": sum(1 for obs in observations if obs.get("outcome") == "ERROR"),
        "skipped_test_count": sum(1 for obs in observations if obs.get("outcome") == "SKIPPED"),
        "not_run_test_count": sum(1 for obs in observations if obs.get("outcome") == "NOT_RUN"),
    }


def _killed_from_observations(observations: list[dict[str, Any]]) -> bool:
    return any(
        parse_bool(obs.get("eligible")) and str(obs.get("outcome") or "") in KILL_OUTCOMES
        for obs in observations
    )


def collect_records_from_test_results(
    runs_dir: Path,
    group_by: str,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    structured_runs: set[str] = set()

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue

        manifest = run_manifest(run_dir)
        manifest_subject = manifest.get("subject", {})
        manifest_target = manifest.get("target", {})
        results_rows = read_csv_rows(execution_results_path(run_dir))
        test_rows = read_csv_rows(execution_test_results_path(run_dir))
        if not test_rows:
            continue

        structured_runs.add(run_dir.name)
        mutant_rows = {
            (
                str(row.get("run_name") or run_dir.name),
                str(row.get("target_id") or manifest_target.get("target_id") or ""),
                str(row.get("mutant_id") or ""),
            ): row
            for row in results_rows
        }

        by_mutant: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in test_rows:
            key = (
                str(row.get("run_name") or run_dir.name),
                str(row.get("target_id") or manifest_target.get("target_id") or ""),
                str(row.get("mutant_id") or ""),
            )
            by_mutant[key].append(row)

        for key, observations in sorted(by_mutant.items()):
            run_name, target_id, mutant_id = key
            mutant_row = mutant_rows.get(key, {})
            counts = _record_counts(observations)
            record = {
                "dataset": observations[0].get("dataset") or manifest_subject.get("dataset") or "",
                "subject_id": observations[0].get("subject_id") or manifest_subject.get("subject_id") or "",
                "project": project_from_subject(
                    observations[0].get("subject_id") or manifest_subject.get("subject_id") or ""
                ),
                "run_name": run_name,
                "target_id": target_id,
                "function_name": manifest_target.get("function_name") or mutant_row.get("function_name") or "",
                "mutant_id": mutant_id,
                "mutant_hash": mutant_row.get("mutant_hash", ""),
                "build_status": mutant_row.get("build_status", ""),
                "test_status": mutant_row.get("test_status", ""),
                "executable": parse_bool(mutant_row.get("executable", "")),
                "killed": _killed_from_observations(observations),
                "stored_killed": parse_bool(mutant_row.get("killed", "")),
                "log_path": mutant_row.get("log_path", observations[0].get("log_path", "")),
                "test_results_source": "test_results.csv",
                "observations": sorted(
                    observations,
                    key=lambda row: (
                        int(row.get("execution_index") or 0),
                        str(row.get("test_name") or ""),
                    ),
                ),
                **counts,
            }
            by_group[group_key(record, group_by)].append(record)

    return by_group, structured_runs


def collect_records_legacy(
    runs_dir: Path,
    group_by: str,
    target_tests: dict[tuple[str, str, str], list[str]],
    *,
    skip_run_names: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skip_run_names = skip_run_names or set()

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        if run_dir.name in skip_run_names:
            continue

        manifest = run_manifest(run_dir)
        manifest_subject = manifest.get("subject", {})
        manifest_target = manifest.get("target", {})
        results_rows = read_csv_rows(execution_results_path(run_dir))
        if not results_rows:
            continue

        for row in results_rows:
            run_name = row.get("run_name") or run_dir.name
            target_id = row.get("target_id") or manifest_target.get("target_id") or ""
            key = (
                str(row.get("dataset") or manifest_subject.get("dataset") or ""),
                str(row.get("subject_id") or manifest_subject.get("subject_id") or ""),
                str(target_id),
            )
            eligible_tests = target_tests.get(key, [])

            log_path_text = row.get("log_path", "")
            log_path = Path(log_path_text)
            if log_path_text and not log_path.is_absolute():
                log_path = Path.cwd() / log_path
            failing_tests = set(legacy_failing_tests(log_path))

            observations = []
            for index, test_name in enumerate(eligible_tests, start=1):
                build_status = str(row.get("build_status") or "")
                test_status = str(row.get("test_status") or "")
                if build_status == "BASELINE_FAIL" or test_status == "BASELINE_FAIL":
                    outcome = "NOT_RUN"
                    executed = False
                    failure_type = "BASELINE_FAIL"
                elif build_status != "SUCCESS":
                    outcome = "NOT_RUN"
                    executed = False
                    failure_type = "BUILD_FAIL"
                else:
                    outcome = "FAIL" if test_name in failing_tests else "PASS"
                    executed = True
                    failure_type = "TEST_FAILURE" if test_name in failing_tests else None

                observations.append(
                    {
                        "run_name": run_name,
                        "dataset": row.get("dataset", ""),
                        "subject_id": row.get("subject_id", ""),
                        "target_id": target_id,
                        "mutant_id": row.get("mutant_id", ""),
                        "mutant_hash": row.get("mutant_hash", ""),
                        "test_name": test_name,
                        "eligible": True,
                        "executed": executed,
                        "outcome": outcome,
                        "duration_ms": "",
                        "failure_type": failure_type,
                        "message": "legacy reconstruction from results/logs",
                        "worker_id": "",
                        "execution_index": index,
                        "build_status": build_status,
                        "executable": row.get("executable", ""),
                        "log_path": row.get("log_path", ""),
                    }
                )

            counts = _record_counts(observations)
            record = {
                "dataset": row.get("dataset", ""),
                "subject_id": row.get("subject_id", ""),
                "project": project_from_subject(row.get("subject_id", "")),
                "run_name": run_name,
                "target_id": target_id,
                "function_name": row.get("function_name", "") or manifest_target.get("function_name", ""),
                "mutant_id": row.get("mutant_id", ""),
                "mutant_hash": row.get("mutant_hash", ""),
                "build_status": row.get("build_status", ""),
                "test_status": row.get("test_status", ""),
                "executable": parse_bool(row.get("executable", "")),
                "killed": _killed_from_observations(observations),
                "stored_killed": parse_bool(row.get("killed", "")),
                "log_path": row.get("log_path", ""),
                "test_results_source": "legacy_results_and_logs",
                "observations": observations,
                **counts,
            }
            by_group[group_key(record, group_by)].append(record)

    return by_group


def tests_for_group(records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(obs.get("test_name") or "")
            for record in records
            for obs in record.get("observations", [])
            if str(obs.get("test_name") or "")
        }
    )


def write_group_outputs(
    group: str,
    records: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[Path, Path]:
    tests = tests_for_group(records)
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
        "stored_killed",
        "executable",
        "target_test_count",
        "executed_test_count",
        "failing_test_count",
        "error_test_count",
        "skipped_test_count",
        "not_run_test_count",
        "test_results_source",
        "log_path",
    ]

    with wide_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=metadata_fields + tests)
        writer.writeheader()
        for record in records:
            row = {field: record.get(field, "") for field in metadata_fields}
            outcomes = {
                str(obs.get("test_name") or ""): str(obs.get("outcome") or "")
                for obs in record.get("observations", [])
            }
            for test in tests:
                row[test] = outcomes.get(test, "NOT_RUN")
            writer.writerow(row)

    with long_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = metadata_fields + [
            "test_name",
            "eligible",
            "executed",
            "outcome",
            "duration_ms",
            "failure_type",
            "message",
            "worker_id",
            "execution_index",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            base = {field: record.get(field, "") for field in metadata_fields}
            for obs in record.get("observations", []):
                row = dict(base)
                row.update(
                    {
                        "test_name": obs.get("test_name", ""),
                        "eligible": obs.get("eligible", ""),
                        "executed": obs.get("executed", ""),
                        "outcome": obs.get("outcome", ""),
                        "duration_ms": obs.get("duration_ms", ""),
                        "failure_type": obs.get("failure_type", ""),
                        "message": obs.get("message", ""),
                        "worker_id": obs.get("worker_id", ""),
                        "execution_index": obs.get("execution_index", ""),
                    }
                )
                writer.writerow(row)

    return wide_path, long_path


def write_summary(
    groups: dict[str, list[dict[str, Any]]],
    output_dir: Path,
    *,
    source_mode: str,
) -> Path:
    path = output_dir / "kill_matrix_summary.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "group",
            "mutants",
            "killed_mutants",
            "unique_target_tests",
            "unique_failing_tests",
            "unique_error_tests",
            "kill_events",
            "source_mode",
            "wide_csv",
            "long_csv",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for group, records in sorted(groups.items()):
            tests = {
                str(obs.get("test_name") or "")
                for record in records
                for obs in record.get("observations", [])
                if parse_bool(obs.get("eligible"))
            }
            failing = {
                str(obs.get("test_name") or "")
                for record in records
                for obs in record.get("observations", [])
                if str(obs.get("outcome") or "") == "FAIL"
            }
            errored = {
                str(obs.get("test_name") or "")
                for record in records
                for obs in record.get("observations", [])
                if str(obs.get("outcome") or "") == "ERROR"
            }
            safe_group = re.sub(r"[^A-Za-z0-9_.-]+", "_", group).strip("_") or "unknown"
            writer.writerow(
                {
                    "group": group,
                    "mutants": len(records),
                    "killed_mutants": sum(1 for record in records if record.get("killed")),
                    "unique_target_tests": len(tests),
                    "unique_failing_tests": len(failing),
                    "unique_error_tests": len(errored),
                    "kill_events": sum(
                        1
                        for record in records
                        for obs in record.get("observations", [])
                        if str(obs.get("outcome") or "") in KILL_OUTCOMES
                    ),
                    "source_mode": source_mode,
                    "wide_csv": str(output_dir / f"{safe_group}_kill_matrix.csv"),
                    "long_csv": str(output_dir / f"{safe_group}_kill_matrix_long.csv"),
                }
            )
    return path


def build_kill_matrices(
    *,
    runs_dir: str | Path = RUNS_DIR,
    output_dir: str | Path = OUTPUT_DIR,
    target_tests: str | Path | None = TARGET_TESTS_CSV,
    group_by: str = "project",
) -> Path:
    runs_dir = Path(runs_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_tests_path = Path(target_tests) if target_tests else None
    if target_tests_path is not None:
        write_target_tests_template(target_tests_path)

    groups, structured_runs = collect_records_from_test_results(runs_dir, group_by)
    target_tests_map = load_target_tests(target_tests_path)
    legacy_groups = collect_records_legacy(
        runs_dir,
        group_by,
        target_tests_map,
        skip_run_names=structured_runs,
    )
    for group, records in legacy_groups.items():
        groups[group].extend(records)

    if structured_runs and legacy_groups:
        source_mode = "mixed_structured_and_legacy"
    elif structured_runs:
        source_mode = "test_results.csv"
    else:
        source_mode = "legacy_results_and_logs"

    for group, records in sorted(groups.items()):
        write_group_outputs(group, records, output_dir)

    summary_path = write_summary(groups, output_dir, source_mode=source_mode)
    print(f"Built kill matrices for {len(groups)} {group_by} group(s)")
    print(f"Source mode: {source_mode}")
    if target_tests_path is not None:
        print(f"Target tests CSV: {target_tests_path}")
    print(f"Summary written to: {summary_path}")
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build kill matrices from structured harness execution data."
    )
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument(
        "--target-tests",
        default=str(TARGET_TESTS_CSV),
        help="CSV used only as a legacy fallback for runs without execution/test_results.csv.",
    )
    parser.add_argument(
        "--group-by",
        choices=["project", "subject", "run"],
        default="project",
        help="How to split output matrices.",
    )
    args = parser.parse_args()

    build_kill_matrices(
        runs_dir=args.runs_dir,
        output_dir=args.output_dir,
        target_tests=args.target_tests,
        group_by=args.group_by,
    )


if __name__ == "__main__":
    main()
