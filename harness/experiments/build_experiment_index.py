from __future__ import annotations

import csv
import json
from pathlib import Path

from harness.storage.layout import manifest_path, resolve_results_path, resolve_summary_path

BASE_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BASE_DIR / "runs"
OUTPUT_CSV = BASE_DIR / "experiments" / "experiment_index.csv"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def count_rejected_artifacts(run_dir: Path) -> int:
    rejected_dir = run_dir / "rejected"
    if not rejected_dir.exists():
        return 0
    return len(list(rejected_dir.glob("rej*.json")))


def execution_metrics_for_index(run_status: str, summary: dict, overall: dict) -> dict:
    if run_status != "ok":
        return {
            "indexed_input_row_count": "",
            "indexed_used_row_count": "",
            "deduplicated": "",
            "total_mutants": "",
            "build_successes": "",
            "executable_mutants": "",
            "killed_mutants": "",
            "survived_mutants": "",
            "baseline_failures": "",
            "build_success_rate": "",
            "executable_yield": "",
            "mutation_score": "",
        }

    return {
        "indexed_input_row_count": summary.get("input_row_count"),
        "indexed_used_row_count": summary.get("used_row_count"),
        "deduplicated": summary.get("deduplicated"),
        "total_mutants": overall.get("total_mutants"),
        "build_successes": overall.get("build_successes"),
        "executable_mutants": overall.get("executable_mutants"),
        "killed_mutants": overall.get("killed_mutants"),
        "survived_mutants": overall.get("survived_mutants"),
        "baseline_failures": overall.get("baseline_failures"),
        "build_success_rate": overall.get("build_success_rate"),
        "executable_yield": overall.get("executable_yield"),
        "mutation_score": overall.get("mutation_score"),
    }


def collect_run_row(run_dir: Path) -> dict | None:
    manifest_file = manifest_path(run_dir)
    summary_file = resolve_summary_path(run_dir)
    results_file = resolve_results_path(run_dir)

    if not manifest_file.exists():
        print(f"[SKIP] Missing manifest: {run_dir}")
        return None

    if not summary_file.exists():
        print(f"[SKIP] Missing summary: {run_dir}")
        return None

    if not results_file.exists():
        print(f"[SKIP] Missing results.csv: {run_dir}")
        return None

    manifest = load_json(manifest_file)
    summary = load_json(summary_file)

    extra = manifest.get("extra_metadata", {})
    subject = manifest.get("subject", {})
    target = manifest.get("target", {})
    overall = summary.get("overall", {})
    run_status = manifest.get("status", summary.get("run_status"))
    rejected_artifact_count = count_rejected_artifacts(run_dir)
    expected_rejected_count = extra.get("n_rejected_mutants")
    execution_metrics = execution_metrics_for_index(run_status, summary, overall)

    if expected_rejected_count is not None and rejected_artifact_count not in (0, expected_rejected_count):
        raise ValueError(
            f"Rejected artifact count mismatch in {run_dir.name}: "
            f"expected {expected_rejected_count}, found {rejected_artifact_count}"
        )

    return {
        "run_name": run_dir.name,
        "run_dir": str(run_dir),
        "created_at_utc": manifest.get("created_at_utc"),
        "started_at_utc": manifest.get("started_at_utc"),
        "completed_at_utc": manifest.get("completed_at_utc"),
        "run_mode": manifest.get("run_mode"),
        "run_status": run_status,
        "failure_reason": manifest.get("failure_reason", summary.get("failure_reason")),
        "failure_message": manifest.get("failure_message", summary.get("failure_message")),
        "dataset": subject.get("dataset"),
        "subject_id": subject.get("subject_id"),
        "version": subject.get("version"),
        "target_id": target.get("target_id"),
        "target_id_meta": extra.get("target_id"),
        "batch_id": extra.get("batch_id"),
        "function_name": target.get("function_name"),
        "file_path": target.get("file_path"),
        "start_line": target.get("start_line"),
        "end_line": target.get("end_line"),
        "language": target.get("language"),
        "experiment_name": extra.get("experiment_name"),
        "mutant_source": extra.get("mutant_source"),
        "model_name": extra.get("model_name"),
        "model_provider": extra.get("model_provider"),
        "prompt_name": extra.get("prompt_name"),
        "prompt_version": extra.get("prompt_version"),
        "temperature": extra.get("temperature"),
        "n_requested_mutants": extra.get("n_requested_mutants"),
        "n_accepted_mutants": extra.get("n_accepted_mutants"),
        "n_rejected_mutants": extra.get("n_rejected_mutants"),
        "acceptance_rate": extra.get("acceptance_rate"),
        "rej_duplicate_mutant": extra.get("rej_duplicate_mutant"),
        "rej_unchanged_mutant": extra.get("rej_unchanged_mutant"),
        "rej_non_executable_change": extra.get("rej_non_executable_change"),
        "rej_non_executable_structural_change": extra.get("rej_non_executable_structural_change"),
        "rej_precode_not_found": extra.get("rej_precode_not_found"),
        "rej_ambiguous_precode_match": extra.get("rej_ambiguous_precode_match"),
        "generation_mode": extra.get("generation_mode"),
        "dataset_split": extra.get("dataset_split"),
        "notes": extra.get("notes"),
        "requested_mutant_count": manifest.get("requested_mutant_count"),
        **execution_metrics,
        "parse_failed": extra.get("parse_failed"),
        "parse_error_message": extra.get("parse_error_message"),
        "rej_invalid_json_response": extra.get("rej_invalid_json_response"),
        "indexed_rejected_artifact_count": rejected_artifact_count,
    }


def validate_index_rows(rows: list[dict]) -> None:
    seen_run_names: set[str] = set()
    seen_run_dirs: set[str] = set()

    for row in rows:
        run_name = str(row.get("run_name"))
        run_dir = str(row.get("run_dir"))

        if run_name in seen_run_names:
            raise ValueError(f"Duplicate run_name while building experiment index: {run_name}")
        if run_dir in seen_run_dirs:
            raise ValueError(f"Duplicate run_dir while building experiment index: {run_dir}")

        seen_run_names.add(run_name)
        seen_run_dirs.add(run_dir)


def build_experiment_index(
    runs_dir: Path | None = None,
    output_csv: Path | None = None,
    print_to_stdout: bool = True,
) -> Path:
    runs_dir = runs_dir or RUNS_DIR
    output_csv = output_csv or OUTPUT_CSV

    if not runs_dir.exists():
        raise FileNotFoundError(f"Runs directory not found: {runs_dir}")

    rows = []

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue

        row = collect_run_row(run_dir)
        if row is not None:
            rows.append(row)

    if not rows:
        if print_to_stdout:
            print("No valid runs found to index.")
        return output_csv

    validate_index_rows(rows)

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(rows[0].keys())
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if print_to_stdout:
        print(f"Indexed {len(rows)} runs")
        print(f"CSV written to: {output_csv}")

    return output_csv


def main():
    build_experiment_index()


if __name__ == "__main__":
    main()
