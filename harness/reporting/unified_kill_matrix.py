from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from harness.reporting.summary import deduplicate_rows, parse_bool
from harness.storage.layout import (
    experiment_index_path,
    execution_test_results_path,
    llm_root,
    resolve_results_path,
    resolve_run_dir,
)

KILL_OUTCOMES = {"FAIL", "ERROR"}
LONG_FIELDNAMES = ["target_id", "model_name", "run_name", "mutant_id", "test_name"]
SUMMARY_FIELDNAMES = [
    "target_id",
    "model_name",
    "run_name",
    "mutant_id",
    "killed",
    "n_killing_tests",
    "eligible_test_count",
]


def load_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_llm_ok_index_rows(index_path: Path) -> list[dict[str, str]]:
    rows = load_csv_rows(index_path)
    return [
        row
        for row in rows
        if row.get("mutant_source") == "llm" and row.get("run_status") == "ok"
    ]


def _killing_test_names(rows: Iterable[dict[str, str]]) -> list[str]:
    names: set[str] = set()
    for row in rows:
        if not parse_bool(row.get("executable", "")):
            continue
        outcome = str(row.get("outcome") or "").strip().upper()
        test_name = str(row.get("test_name") or "").strip()
        if outcome in KILL_OUTCOMES and test_name:
            names.add(test_name)
    return sorted(names)


def _results_by_mutant(run_dir: Path) -> dict[str, dict[str, str]]:
    result_rows = deduplicate_rows(load_csv_rows(resolve_results_path(run_dir)))
    return {
        str(row.get("mutant_id") or "").strip(): row
        for row in result_rows
        if str(row.get("mutant_id") or "").strip()
    }


def _test_rows_by_mutant(run_dir: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in load_csv_rows(execution_test_results_path(run_dir)):
        mutant_id = str(row.get("mutant_id") or "").strip()
        if mutant_id:
            grouped[mutant_id].append(row)
    return grouped


def build_unified_exports(
    *,
    index_path: Path,
    runs_base_dir: Path | None = None,
) -> tuple[list[dict[str, str | int]], list[dict[str, str | int]], list[str]]:
    long_rows: list[dict[str, str | int]] = []
    summary_rows: list[dict[str, str | int]] = []
    validation_errors: list[str] = []

    for index_row in load_llm_ok_index_rows(index_path):
        run_name = str(index_row.get("run_name") or "").strip()
        run_dir = resolve_run_dir(run_name)
        if runs_base_dir is not None:
            run_dir = runs_base_dir / run_name
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory not found for {run_name}: {run_dir}")

        result_by_mutant = _results_by_mutant(run_dir)
        test_rows_by_mutant = _test_rows_by_mutant(run_dir)

        executable_count = 0
        killed_count = 0

        for mutant_id, result_row in sorted(result_by_mutant.items()):
            if not parse_bool(result_row.get("executable", "")):
                continue

            executable_count += 1
            killing_tests = _killing_test_names(test_rows_by_mutant.get(mutant_id, []))
            derived_killed = int(bool(killing_tests))
            stored_killed = int(parse_bool(result_row.get("killed", "")))
            if derived_killed != stored_killed:
                validation_errors.append(
                    f"{run_name} mutant {mutant_id}: derived killed={derived_killed} "
                    f"but results.csv stores killed={stored_killed}"
                )

            killed_count += derived_killed
            summary_rows.append(
                {
                    "target_id": index_row["target_id"],
                    "model_name": index_row["model_name"],
                    "run_name": run_name,
                    "mutant_id": mutant_id,
                    "killed": derived_killed,
                    "n_killing_tests": len(killing_tests),
                    "eligible_test_count": index_row.get("eligible_test_count", ""),
                }
            )
            for test_name in killing_tests:
                long_rows.append(
                    {
                        "target_id": index_row["target_id"],
                        "model_name": index_row["model_name"],
                        "run_name": run_name,
                        "mutant_id": mutant_id,
                        "test_name": test_name,
                    }
                )

        expected_executable = int(index_row.get("executable_mutants") or 0)
        expected_killed = int(index_row.get("killed_mutants") or 0)
        if executable_count != expected_executable:
            validation_errors.append(
                f"{run_name}: executable_mutants mismatch "
                f"(derived={executable_count}, experiment_index={expected_executable})"
            )
        if killed_count != expected_killed:
            validation_errors.append(
                f"{run_name}: killed_mutants mismatch "
                f"(derived={killed_count}, experiment_index={expected_killed})"
            )

    return long_rows, summary_rows, validation_errors


def write_csv(output_path: Path, fieldnames: list[str], rows: list[dict[str, str | int]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_unified_kill_matrix(
    *,
    index_path: Path = experiment_index_path(),
    output_dir: Path = llm_root(),
    runs_base_dir: Path | None = None,
) -> tuple[Path, Path]:
    long_rows, summary_rows, validation_errors = build_unified_exports(
        index_path=index_path,
        runs_base_dir=runs_base_dir,
    )
    long_rows.sort(key=lambda row: tuple(str(row[key]) for key in LONG_FIELDNAMES))
    summary_rows.sort(key=lambda row: tuple(str(row[key]) for key in SUMMARY_FIELDNAMES[:4]))
    long_path = output_dir / "kill_matrix_long.csv"
    summary_path = output_dir / "mutant_summary.csv"
    write_csv(long_path, LONG_FIELDNAMES, long_rows)
    write_csv(summary_path, SUMMARY_FIELDNAMES, summary_rows)

    print(f"Wrote {len(long_rows)} sparse kill rows to {long_path}")
    print(f"Wrote {len(summary_rows)} executable mutant rows to {summary_path}")

    if validation_errors:
        print("Cross-validation failed:")
        for error in validation_errors:
            print(f"  - {error}")
        raise ValueError(f"Unified kill matrix validation failed with {len(validation_errors)} discrepancy(ies)")

    print(f"Cross-validation OK across {len(load_llm_ok_index_rows(index_path))} LLM runs with status=ok")
    return long_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export consolidated LLM kill matrix outputs for analytical workflows."
    )
    parser.add_argument("--index", default=str(experiment_index_path()))
    parser.add_argument("--output-dir", default=str(llm_root()))
    args = parser.parse_args()

    export_unified_kill_matrix(
        index_path=Path(args.index),
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
