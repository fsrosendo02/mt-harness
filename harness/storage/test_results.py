from __future__ import annotations

import csv
from pathlib import Path

from harness.models import MutantResult, TestObservation


TEST_RESULT_FIELDNAMES = [
    "run_name",
    "dataset",
    "subject_id",
    "target_id",
    "mutant_id",
    "mutant_hash",
    "test_name",
    "eligible",
    "executed",
    "outcome",
    "duration_ms",
    "failure_type",
    "message",
    "worker_id",
    "execution_index",
    "build_status",
    "executable",
    "log_path",
]


def test_results_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "execution" / "test_results.csv"


def ensure_test_results_csv_schema(csv_path: str | Path) -> None:
    path = Path(csv_path)
    if not path.exists():
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        existing_fieldnames = reader.fieldnames or []
        rows = list(reader)

    if existing_fieldnames == TEST_RESULT_FIELDNAMES:
        return

    normalized_rows = []
    for row in rows:
        normalized_rows.append({field: row.get(field, "") for field in TEST_RESULT_FIELDNAMES})

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TEST_RESULT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(normalized_rows)


def append_test_results_csv(
    csv_path: str | Path,
    result: MutantResult,
    observations: list[TestObservation],
) -> None:
    path = Path(csv_path)
    file_exists = path.exists()
    if file_exists:
        ensure_test_results_csv_schema(path)

    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TEST_RESULT_FIELDNAMES)
        if not file_exists:
            writer.writeheader()

        for observation in observations:
            writer.writerow(
                {
                    "run_name": result.run_name,
                    "dataset": result.dataset,
                    "subject_id": result.subject_id,
                    "target_id": result.target_id,
                    "mutant_id": result.mutant_id,
                    "mutant_hash": result.mutant_hash,
                    "test_name": observation.test_name,
                    "eligible": observation.eligible,
                    "executed": observation.executed,
                    "outcome": observation.outcome,
                    "duration_ms": observation.duration_ms,
                    "failure_type": observation.failure_type,
                    "message": observation.message,
                    "worker_id": observation.worker_id,
                    "execution_index": observation.execution_index,
                    "build_status": result.build_status,
                    "executable": result.executable,
                    "log_path": result.log_path,
                }
            )
