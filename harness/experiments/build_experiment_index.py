from __future__ import annotations

import csv
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BASE_DIR / "runs"
OUTPUT_CSV = BASE_DIR / "experiments" / "experiment_index.csv"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_run_row(run_dir: Path) -> dict | None:
    manifest_path = run_dir / "run_manifest.json"
    summary_path = run_dir / "summary.json"
    results_path = run_dir / "results.csv"

    if not manifest_path.exists():
        print(f"[SKIP] Missing manifest: {run_dir}")
        return None

    if not summary_path.exists():
        print(f"[SKIP] Missing summary: {run_dir}")
        return None

    if not results_path.exists():
        print(f"[SKIP] Missing results.csv: {run_dir}")
        return None

    manifest = load_json(manifest_path)
    summary = load_json(summary_path)

    extra = manifest.get("extra_metadata", {})
    subject = manifest.get("subject", {})
    target = manifest.get("target", {})
    overall = summary.get("overall", {})

    return {
        "run_name": run_dir.name,
        "run_dir": str(run_dir),
        "created_at_utc": manifest.get("created_at_utc"),
        "started_at_utc": manifest.get("started_at_utc"),
        "completed_at_utc": manifest.get("completed_at_utc"),
        "run_mode": manifest.get("run_mode"),
        "run_status": manifest.get("status", summary.get("run_status")),
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
        "parse_failed": extra.get("parse_failed"),
        "parse_error_message": extra.get("parse_error_message"),
        "rej_invalid_json_response": extra.get("rej_invalid_json_response"),
    }


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
