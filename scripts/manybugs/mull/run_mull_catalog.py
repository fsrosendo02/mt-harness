#!/usr/bin/env python3
"""Fase 4 of the mull baseline pipeline (see mull_baseline_plan.md).

Runs the mull baseline (Fase 2 + Fase 3) for every target in a C catalog
(e.g. manybugs_gzip_pilot.json), one subject at a time — each target is a
different ManyBugs scenario/commit, so each needs its own
prepare_mull_checkout.py pull+build (no sharing across targets within a
family, unlike Major which recompiles once per Defects4J checkout too).

Writes per-target results.csv/test_results.csv under
harness/executions/c/mull/execution/<run-name>/<run-name>__<subject_id>__
<function_name>__<target_id>/execution/, mirroring the directory shape
already used by the C/LLM execution pipeline
(harness/executions/c/llm/<batch>/<batch>__<subject>__<function>__<target_id>/).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.models import Subject, Target
from harness.targets.target_tests import load_target_test_map, target_tests_for

from run_mull_subject import run_mull_subject, DEFAULT_MUTATORS, DEFAULT_TIMEOUT_MS
from normalize_mull_report import normalize_mull_report


def load_catalog_targets(catalog_file: Path) -> list[dict]:
    data = json.loads(catalog_file.read_text(encoding="utf-8"))
    return data["targets"]


def run_mull_catalog(
    *,
    catalog_file: Path,
    run_name: str,
    output_root: Path,
    mutators: list[str],
    timeout_ms: int,
    only_target_id: str | None = None,
) -> dict:
    targets = load_catalog_targets(catalog_file)
    test_map = load_target_test_map(catalog_file=catalog_file)

    per_target_results = []
    for t in targets:
        target_id = t["target_id"]
        if only_target_id and target_id != only_target_id:
            continue

        subject_id = t["subject"]
        function_name = t["function"]
        target_file = t["file"]
        start_line = t["start_line"]
        end_line = t["end_line"]

        subject = Subject(dataset="manybugs", subject_id=subject_id, language="c")
        target = Target(
            file_path=target_file, function_name=function_name,
            start_line=start_line, end_line=end_line, language="c", target_id=target_id,
        )
        eligible_tests = target_tests_for(subject, target, test_map)
        if not eligible_tests:
            print(f"[skip] {target_id}: no eligible tests in target_tests.csv")
            per_target_results.append({"target_id": target_id, "status": "SKIPPED_NO_TESTS"})
            continue

        run_dir = output_root / f"{run_name}__{subject_id}__{function_name}__{target_id}"
        raw_dir = run_dir / "raw"

        print(f"\n=== {target_id} ({subject_id}) — tests: {eligible_tests} ===")
        try:
            run_mull_subject(
                subject_id=subject_id,
                target_id=target_id,
                target_file=target_file,
                start_line=start_line,
                end_line=end_line,
                catalog_file=str(catalog_file),
                mutators=mutators,
                timeout_ms=timeout_ms,
                report_dir=raw_dir,
            )
            result = normalize_mull_report(
                report_dir=raw_dir,
                dataset="manybugs",
                subject_id=subject_id,
                target_id=target_id,
                function_name=function_name,
                target_file=target_file,
                start_line=start_line,
                end_line=end_line,
                eligible_tests=eligible_tests,
                run_name=run_name,
                run_dir=run_dir,
            )
            per_target_results.append({
                "target_id": target_id,
                "status": "OK",
                "total_mutants": result["total_mutants"],
                "mutation_score": result["summary"]["overall"]["mutation_score"],
                "run_dir": str(run_dir),
            })
        except Exception as exc:  # noqa: BLE001 — batch driver must not die on one bad target
            traceback.print_exc()
            per_target_results.append({
                "target_id": target_id, "status": "ERROR", "error": str(exc),
            })

    return {
        "catalog_file": str(catalog_file),
        "run_name": run_name,
        "targets": per_target_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--output-root", default="harness/executions/c/mull/execution")
    parser.add_argument("--mutators", nargs="*", default=DEFAULT_MUTATORS)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--only-target-id", help="Restrict to a single target_id (debugging)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_mull_catalog(
        catalog_file=Path(args.catalog),
        run_name=args.run_name,
        output_root=Path(args.output_root) / args.run_name,
        mutators=args.mutators,
        timeout_ms=args.timeout_ms,
        only_target_id=args.only_target_id,
    )
    print("\n=== Catalog summary ===")
    for t in result["targets"]:
        print(f"  {t['target_id']}: {t['status']}"
              + (f" ({t.get('total_mutants')} mutants, score={t.get('mutation_score'):.2f})"
                 if t["status"] == "OK" else ""))
    ok = sum(1 for t in result["targets"] if t["status"] == "OK")
    print(f"\n{ok}/{len(result['targets'])} targets completed OK")
    return 0 if ok == len(result["targets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
