import csv
from pathlib import Path
from harness.models import MutantResult


def append_result_csv(csv_path: str, result: MutantResult) -> None:
    file_exists = Path(csv_path).exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "dataset",
                "subject_id",
                "function_name",
                "mutant_id",
                "build_status",
                "test_status",
                "killed",
                "executable",
                "log_path",
            ])

        writer.writerow([
            result.dataset,
            result.subject_id,
            result.function_name,
            result.mutant_id,
            result.build_status,
            result.test_status,
            result.killed,
            result.executable,
            result.log_path,
        ])
