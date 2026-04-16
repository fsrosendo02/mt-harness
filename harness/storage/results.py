import csv
from pathlib import Path
from harness.models import MutantResult


RESULT_FIELDNAMES = [
    "dataset",
    "subject_id",
    "target_id",
    "run_name",
    "function_name",
    "mutant_id",
    "mutant_hash",
    "build_status",
    "test_status",
    "killed",
    "executable",
    "log_path",
]


def ensure_results_csv_schema(csv_path: str | Path) -> None:
    path = Path(csv_path)
    if not path.exists():
        return

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        existing_fieldnames = reader.fieldnames or []
        rows = list(reader)

    if existing_fieldnames == RESULT_FIELDNAMES:
        return

    normalized_rows = []
    for row in rows:
        normalized_rows.append({field: row.get(field, "") for field in RESULT_FIELDNAMES})

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(normalized_rows)


def append_result_csv(csv_path: str, result: MutantResult) -> None:
    file_exists = Path(csv_path).exists()
    if file_exists:
        ensure_results_csv_schema(csv_path)

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(RESULT_FIELDNAMES)

        writer.writerow([
            result.dataset,
            result.subject_id,
            result.target_id,
            result.run_name,
            result.function_name,
            result.mutant_id,
            result.mutant_hash,
            result.build_status,
            result.test_status,
            result.killed,
            result.executable,
            result.log_path,
        ])
