#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.targets.test_coverage_templates import combine_catalog_target_tests
from harness.storage.layout import target_test_catalogs_dir


DEFAULT_PLAN = (
    REPO_ROOT / "configs" / "debugging" / "defects4j_final_parallel_coverage_plan.json"
)


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_batch_index(plan: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for idx, batch in enumerate(plan["parallel_batches"], start=1):
        index[batch["batch_id"]] = batch
        index[f"batch{idx}"] = batch
        index[f"coverage{idx:02d}"] = batch
    return index


def rerun_root(plan_path: Path) -> Path:
    return REPO_ROOT / "tmp" / "coverage_reruns" / plan_path.stem


def batch_output_path(plan_path: Path, batch_id: str) -> Path:
    return rerun_root(plan_path) / "outputs" / f"{batch_id}.csv"


def batch_work_root(plan_path: Path, batch_id: str) -> Path:
    return rerun_root(plan_path) / "workroots" / batch_id


def run_batch(plan_path: Path, batch: dict, *, dry_run: bool) -> int:
    batch_id = batch["batch_id"]
    output_path = batch_output_path(plan_path, batch_id)
    work_root = batch_work_root(plan_path, batch_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_root.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "harness.targets.test_coverage_collection",
        load_plan(plan_path)["catalog"],
        "--output",
        str(output_path.relative_to(REPO_ROOT)),
        "--work-root",
        str(work_root.relative_to(REPO_ROOT)),
    ]
    for target_id in batch["targets"]:
        cmd.extend(["--target-id", target_id])

    print(f"[parallel-rerun] {batch_id}: {len(batch['targets'])} target(s)")
    print(" ".join(cmd))
    if dry_run:
        return 0

    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


def merge_outputs(plan_path: Path, *, cleanup: bool) -> None:
    plan = load_plan(plan_path)
    coverage_csv = REPO_ROOT / plan["coverage_csv"]
    backup_csv = REPO_ROOT / plan["backup_csv"]
    batches = plan["parallel_batches"]

    with coverage_csv.open("r", encoding="utf-8", newline="") as handle:
        final_rows = list(csv.DictReader(handle))
        fieldnames = list(final_rows[0].keys()) if final_rows else []

    if not fieldnames:
        raise RuntimeError(f"No rows found in {coverage_csv}")

    merged_rows = list(final_rows)
    merged_targets: set[str] = set()

    for batch in batches:
        batch_id = batch["batch_id"]
        output_path = batch_output_path(plan_path, batch_id)
        if not output_path.exists():
            raise FileNotFoundError(f"Missing batch output: {output_path}")

        with output_path.open("r", encoding="utf-8", newline="") as handle:
            batch_rows = list(csv.DictReader(handle))

        if not batch_rows:
            continue

        batch_fieldnames = list(batch_rows[0].keys())
        if batch_fieldnames != fieldnames:
            raise RuntimeError(
                f"Field mismatch for {output_path}: {batch_fieldnames} != {fieldnames}"
            )

        replace_targets = {row["target_id"] for row in batch_rows}
        merged_rows = [row for row in merged_rows if row["target_id"] not in replace_targets]
        merged_rows.extend(batch_rows)
        merged_targets.update(replace_targets)
        print(
            f"[parallel-rerun] merged {batch_id}: "
            f"{len(batch_rows)} row(s), {len(replace_targets)} target(s)"
        )

    merged_rows.sort(
        key=lambda row: (
            row.get("catalog", ""),
            row.get("dataset", ""),
            row.get("subject_id", ""),
            row.get("version", ""),
            row.get("project", ""),
            row.get("target_id", ""),
            row.get("test_name", ""),
        )
    )

    with coverage_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged_rows)

    backup_csv.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(coverage_csv, backup_csv)

    combined_path = REPO_ROOT / "harness" / "datasets" / "coverage" / "global_target_tests.csv"
    combine_catalog_target_tests(target_test_catalogs_dir(), combined_path)

    print(
        f"[parallel-rerun] updated {coverage_csv} with {len(merged_targets)} target(s); "
        f"backup refreshed at {backup_csv}"
    )
    print(f"[parallel-rerun] recombined global coverage index at {combined_path}")

    if cleanup:
        outputs_root = rerun_root(plan_path)
        if outputs_root.exists():
            shutil.rmtree(outputs_root)
            print(f"[parallel-rerun] cleaned {outputs_root}")


def list_batches(plan_path: Path) -> None:
    plan = load_plan(plan_path)
    print(
        f"Plan: {plan_path.relative_to(REPO_ROOT)} | "
        f"missing_subjects={plan['total_missing_subjects']} "
        f"missing_targets={plan['total_missing_targets']}"
    )
    for batch in plan["parallel_batches"]:
        print(
            f"{batch['batch_id']}: subjects={len(batch['subjects'])} "
            f"targets={len(batch['targets'])}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run or merge parallel Defects4J coverage reruns safely."
    )
    parser.add_argument("--plan", default=str(DEFAULT_PLAN), help="Plan JSON path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List batches in the rerun plan.")

    run_parser = subparsers.add_parser("run", help="Run one batch from the rerun plan.")
    run_parser.add_argument(
        "batch_id",
        help="Coverage group id from the plan, e.g. coverage01. Legacy batch1 aliases still work.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the underlying collector command without executing it.",
    )

    merge_parser = subparsers.add_parser(
        "merge", help="Merge all finished batch outputs into target_tests.csv."
    )
    merge_parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove tmp/coverage_reruns/<plan> after a successful merge.",
    )

    args = parser.parse_args()
    plan_path = Path(args.plan).resolve()
    plan = load_plan(plan_path)
    batch_index = build_batch_index(plan)

    if args.command == "list":
        list_batches(plan_path)
        return

    if args.command == "run":
        batch = batch_index.get(args.batch_id)
        if batch is None:
            raise SystemExit(f"Unknown batch id: {args.batch_id}")
        raise SystemExit(run_batch(plan_path, batch, dry_run=args.dry_run))

    if args.command == "merge":
        merge_outputs(plan_path, cleanup=args.cleanup)
        return


if __name__ == "__main__":
    main()
