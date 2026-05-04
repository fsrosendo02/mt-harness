#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_RUNS_DIR = Path("harness/executions/runs")
DEFAULT_BATCHES_DIR = Path("harness/executions/batches")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def slug(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w\-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def infer_batch_id(run_name: str, manifest: dict[str, Any]) -> str | None:
    extra = manifest.get("extra_metadata") or {}
    batch_id = extra.get("batch_id")
    if batch_id:
        return str(batch_id)

    if "__" in run_name:
        return run_name.split("__", 1)[0]

    return None


def infer_run_pipeline_mode(run_dir: Path) -> str:
    generation_dir = run_dir / "generation"
    execution_dir = run_dir / "execution"

    has_generation = generation_dir.exists()
    has_execution = execution_dir.exists() and any(execution_dir.iterdir())

    if has_generation and has_execution:
        return "full"
    if has_generation:
        return "generate_only"
    if has_execution:
        return "execute_only"
    return "unknown"


def infer_batch_pipeline_mode(modes: list[str]) -> str:
    if "full" in modes:
        return "full"
    if "generate_only" in modes:
        return "generate_only"
    if "execute_only" in modes:
        return "execute_only"
    return "unknown"


def common_value(values: list[Any]) -> Any:
    normalized = [value for value in values if value not in (None, "")]
    if not normalized:
        return None
    unique = []
    for value in normalized:
        if value not in unique:
            unique.append(value)
    return unique[0] if len(unique) == 1 else None


def parse_prompt_file(notes: Any) -> str | None:
    if not isinstance(notes, str):
        return None
    match = re.search(r"Prompt file:\s*([^;]+)", notes)
    if not match:
        return None
    return match.group(1).strip()


def sort_key(run_row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(run_row.get("started_at_utc") or run_row.get("created_at_utc") or ""),
        str(run_row.get("run_name") or ""),
    )


def collect_run_rows(runs_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for manifest_path in sorted(runs_dir.glob("*/run_manifest.json")):
        manifest = load_json(manifest_path)
        run_dir = manifest_path.parent
        run_name = run_dir.name
        extra = manifest.get("extra_metadata") or {}
        subject = manifest.get("subject") or {}
        target = manifest.get("target") or {}
        batch_id = infer_batch_id(run_name, manifest)

        if not batch_id:
            continue

        rows.append(
            {
                "batch_id": batch_id,
                "run_name": run_name,
                "run_dir": str(run_dir),
                "created_at_utc": manifest.get("created_at_utc"),
                "started_at_utc": manifest.get("started_at_utc"),
                "completed_at_utc": manifest.get("completed_at_utc"),
                "run_mode": manifest.get("run_mode"),
                "status": manifest.get("status"),
                "failure_reason": manifest.get("failure_reason"),
                "failure_message": manifest.get("failure_message"),
                "requested_mutant_count": manifest.get("requested_mutant_count"),
                "workdir_base": manifest.get("workdir_base"),
                "dataset": subject.get("dataset"),
                "subject": subject.get("subject_id"),
                "version": subject.get("version"),
                "target_id": first_non_empty(target.get("target_id"), extra.get("target_id")),
                "function": target.get("function_name"),
                "file_path": target.get("file_path"),
                "start_line": target.get("start_line"),
                "end_line": target.get("end_line"),
                "language": target.get("language"),
                "model": extra.get("model_name"),
                "model_provider": extra.get("model_provider"),
                "prompt_name": extra.get("prompt_name"),
                "prompt_version": extra.get("prompt_version"),
                "prompt_file": parse_prompt_file(extra.get("notes")),
                "temperature": extra.get("temperature"),
                "num_mutants": first_non_empty(
                    extra.get("n_requested_mutants"),
                    manifest.get("requested_mutant_count"),
                ),
                "generation_mode": extra.get("generation_mode"),
                "run_pipeline_mode": infer_run_pipeline_mode(run_dir),
            }
        )

    return rows


def build_batch_manifest(batch_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(rows, key=sort_key)
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_target[str(row.get("target_id") or row["run_name"])].append(row)

    targets: list[str] = []
    run_entries: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    failure_reason_counts: Counter[str] = Counter()
    created_candidates: list[str] = []

    for target_id, target_rows in by_target.items():
        target_rows.sort(key=sort_key)
        runs_per_target = len(target_rows)

        if target_id and target_id not in targets:
            targets.append(target_id)

        for run_index_for_target, row in enumerate(target_rows, start=1):
            status = str(row.get("status") or "unknown")
            status_counts[status] += 1
            failure_reason = row.get("failure_reason")
            if status == "failure" and failure_reason:
                failure_reason_counts[str(failure_reason)] += 1

            created_candidates.extend(
                [
                    str(row.get("started_at_utc") or ""),
                    str(row.get("created_at_utc") or ""),
                    str(row.get("completed_at_utc") or ""),
                ]
            )

            run_entries.append(
                {
                    "target_id": row.get("target_id"),
                    "subject": row.get("subject"),
                    "function": row.get("function"),
                    "run_group_id": f"{batch_id}__{slug(str(row.get('target_id') or row['run_name']))}",
                    "run_index_for_target": run_index_for_target,
                    "runs_per_target": runs_per_target,
                    "run_name": row["run_name"],
                    "run_dir": row["run_dir"],
                    "return_code": None,
                    "status": row.get("status"),
                    "failure_reason": row.get("failure_reason"),
                }
            )

    pipeline_mode = infer_batch_pipeline_mode([str(row.get("run_pipeline_mode")) for row in rows])
    created_candidates = sorted(value for value in created_candidates if value)

    return {
        "batch_id": batch_id,
        "created_at": created_candidates[0] if created_candidates else None,
        "pipeline_mode": pipeline_mode,
        "catalog_file": None,
        "base_config_file": None,
        "source_batch_id": None,
        "source_batch_manifest": None,
        "model": common_value([row.get("model") for row in rows]),
        "prompt_file": common_value([row.get("prompt_file") for row in rows]),
        "num_mutants": common_value([row.get("num_mutants") for row in rows]),
        "timeout": None,
        "batch_timeout": None,
        "runs_per_target": common_value([entry["runs_per_target"] for entry in run_entries]),
        "temperature": common_value([row.get("temperature") for row in rows]),
        "targets": targets,
        "runs": run_entries,
        "summary": {
            "ok": status_counts.get("ok", 0),
            "failure": status_counts.get("failure", 0),
            "no_coverage": status_counts.get("no_coverage", 0),
            "no_valid_mutants": status_counts.get("no_valid_mutants", 0),
            "generated": status_counts.get("generated", 0),
            "total": len(run_entries),
            "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        },
        "reconstructed_from_run_manifests": True,
        "reconstruction_notes": {
            "model_provider": common_value([row.get("model_provider") for row in rows]),
            "prompt_name": common_value([row.get("prompt_name") for row in rows]),
            "prompt_version": common_value([row.get("prompt_version") for row in rows]),
            "generation_mode": common_value([row.get("generation_mode") for row in rows]),
        },
    }


def rebuild_batch_manifests(
    runs_dir: Path,
    batches_dir: Path,
    selected_batch_ids: set[str] | None,
    overwrite: bool,
) -> list[Path]:
    run_rows = collect_run_rows(runs_dir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in run_rows:
        if selected_batch_ids and row["batch_id"] not in selected_batch_ids:
            continue
        grouped[row["batch_id"]].append(row)

    written_paths: list[Path] = []
    for batch_id in sorted(grouped):
        out_path = batches_dir / f"{batch_id}.json"
        if out_path.exists() and not overwrite:
            continue
        manifest = build_batch_manifest(batch_id, grouped[batch_id])
        save_json(out_path, manifest)
        written_paths.append(out_path)

    return written_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild batch manifests from existing run manifests."
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help=f"Directory containing run folders (default: {DEFAULT_RUNS_DIR})",
    )
    parser.add_argument(
        "--batches-dir",
        type=Path,
        default=DEFAULT_BATCHES_DIR,
        help=f"Output directory for batch manifests (default: {DEFAULT_BATCHES_DIR})",
    )
    parser.add_argument(
        "--batch-id",
        action="append",
        dest="batch_ids",
        help="Rebuild only the given batch id. Can be passed multiple times.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing batch manifests if they already exist.",
    )
    args = parser.parse_args()

    written = rebuild_batch_manifests(
        runs_dir=args.runs_dir,
        batches_dir=args.batches_dir,
        selected_batch_ids=set(args.batch_ids or []),
        overwrite=args.overwrite,
    )

    if not written:
        print("No batch manifests written.")
        return

    print(f"Wrote {len(written)} batch manifest(s):")
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
