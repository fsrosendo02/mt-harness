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
import csv
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.models import Subject, Target
from harness.targets.target_tests import load_target_test_map, target_tests_for

from run_mull_subject import (
    run_mull_subject, DEFAULT_MUTATORS, DEFAULT_TIMEOUT_MS,
    IMPLEMENTED_EXECUTION_PROJECTS, PROJECT_EXECUTION_BLOCKERS,
    VALIDATED_EXECUTION_PROJECTS,
)
from prepare_mull_checkout import DEFAULT_BINARY_RELPATH
from normalize_mull_report import normalize_mull_report


def load_catalog_targets(catalog_file: Path) -> list[dict]:
    data = json.loads(catalog_file.read_text(encoding="utf-8"))
    return data["targets"]


def build_preflight_plan(catalog_file: Path) -> dict:
    """Audit catalog readiness without Docker, builds, or mutation execution."""
    targets = load_catalog_targets(catalog_file)
    test_map = load_target_test_map(catalog_file=catalog_file)
    mapping_path = (
        Path("harness/datasets/coverage/catalogs")
        / catalog_file.stem / "target_tests.csv"
    )
    oracle_by_target: dict[str, list[str]] = {}
    if mapping_path.exists():
        with mapping_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("match_mode") or "").lower() == "oracle":
                    oracle_by_target.setdefault(row["target_id"], []).append(row["test_name"])

    rows = []
    for target_data in targets:
        subject_id = target_data["subject"]
        project = subject_id.split("-", 1)[0]
        target = Target(
            file_path=target_data["file"], function_name=target_data["function"],
            start_line=target_data["start_line"], end_line=target_data["end_line"],
            language="c", target_id=target_data["target_id"],
        )
        subject = Subject(dataset="manybugs", subject_id=subject_id, language="c")
        eligible = target_tests_for(subject, target, test_map)
        dockerfile = Path(__file__).resolve().parent / f"Dockerfile.{project}"
        blockers = []
        if not eligible:
            blockers.append("missing_eligible_tests")
        if not oracle_by_target.get(target.target_id):
            blockers.append("missing_oracle_tests")
        if not dockerfile.exists():
            blockers.append("missing_toolchain_dockerfile")
        if project not in DEFAULT_BINARY_RELPATH:
            blockers.append("missing_binary_profile")
        if project not in IMPLEMENTED_EXECUTION_PROJECTS:
            blockers.append("missing_execution_adapter")
        elif project in PROJECT_EXECUTION_BLOCKERS:
            blockers.append(PROJECT_EXECUTION_BLOCKERS[project])
        elif project not in VALIDATED_EXECUTION_PROJECTS:
            blockers.append("unvalidated_execution_adapter")
        rows.append({
            "project": project,
            "subject_id": subject_id,
            "target_id": target.target_id,
            "target_file": target.file_path,
            "function_name": target.function_name,
            "eligible_tests": eligible,
            "oracle_tests": sorted(oracle_by_target.get(target.target_id, [])),
            "dockerfile": str(dockerfile) if dockerfile.exists() else None,
            "binary_profile": DEFAULT_BINARY_RELPATH.get(project),
            "execution_adapter": project if project in IMPLEMENTED_EXECUTION_PROJECTS else None,
            "execution_adapter_validated": project in VALIDATED_EXECUTION_PROJECTS,
            "ready": not blockers,
            "blockers": blockers,
        })

    projects = []
    for project in sorted({row["project"] for row in rows}):
        project_rows = [row for row in rows if row["project"] == project]
        blockers = sorted({item for row in project_rows for item in row["blockers"]})
        projects.append({
            "project": project,
            "n_targets": len(project_rows),
            "n_ready_targets": sum(row["ready"] for row in project_rows),
            "ready": not blockers,
            "blockers": blockers,
            "suggested_smoke_target": project_rows[0]["target_id"],
        })
    return {
        "catalog_file": str(catalog_file),
        "mapping_file": str(mapping_path),
        "n_targets": len(rows),
        "n_ready_targets": sum(row["ready"] for row in rows),
        "projects": projects,
        "targets": rows,
    }


def run_mull_catalog(
    *,
    catalog_file: Path,
    run_name: str,
    output_root: Path,
    mutators: list[str],
    timeout_ms: int,
    only_target_id: str | None = None,
    resume: bool = False,
) -> dict:
    targets = load_catalog_targets(catalog_file)
    selected_projects = {
        target["subject"].split("-", 1)[0] for target in targets
        if not only_target_id or target["target_id"] == only_target_id
    }
    blocked = sorted(selected_projects & set(PROJECT_EXECUTION_BLOCKERS))
    if blocked:
        reasons = ", ".join(
            f"{project}={PROJECT_EXECUTION_BLOCKERS[project]}" for project in blocked
        )
        raise ValueError(
            f"Mull execution refused for blocked project adapter(s): {reasons}"
        )
    unvalidated = sorted(selected_projects - VALIDATED_EXECUTION_PROJECTS)
    if unvalidated and not only_target_id:
        raise ValueError(
            "Catalog-wide Mull execution refused: project adapter(s) still require "
            f"a single-target smoke test: {', '.join(unvalidated)}. "
            "Use --only-target-id first."
        )
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

        if resume:
            required = [
                run_dir / "execution/results.csv",
                run_dir / "execution/test_results.csv",
                run_dir / "execution/summary.json",
                run_dir / "run_config.json",
            ]
            if all(path.exists() for path in required):
                try:
                    config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
                    summary = json.loads(
                        (run_dir / "execution/summary.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    config, summary = {}, {}
                if config.get("source_revision") == "fixed" and summary.get("overall"):
                    print(f"[resume] {target_id}: complete fixed-revision run already present")
                    per_target_results.append({
                        "target_id": target_id,
                        "status": "SKIPPED_COMPLETE",
                        "run_dir": str(run_dir),
                    })
                    continue

        print(f"\n=== {target_id} ({subject_id}) — tests: {eligible_tests} ===")
        try:
            subject_result = run_mull_subject(
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
            (run_dir / "run_config.json").write_text(json.dumps({
                "tool": "mull",
                "run_name": run_name,
                "target_id": target_id,
                "subject": subject_id,
                "source_revision": subject_result["source_revision"],
                "applied_fix_diffs": subject_result["applied_fix_diffs"],
                "binary_relpath": subject_result["binary_relpath"],
                "eligible_tests": eligible_tests,
                "mutators": mutators,
            }, indent=2), encoding="utf-8")
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
    parser.add_argument("--run-name", help="Required unless --preflight-only is used")
    parser.add_argument("--output-root", default="harness/executions/c/mull/execution")
    parser.add_argument("--mutators", nargs="*", default=DEFAULT_MUTATORS)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--only-target-id", help="Restrict to a single target_id (debugging)")
    parser.add_argument(
        "--preflight-only", action="store_true",
        help="Audit static catalog/project readiness without running Docker or Mull",
    )
    parser.add_argument("--plan-output", help="Write the preflight plan as JSON")
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip targets with complete fixed-revision artifacts; rerun incomplete targets",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preflight_only:
        plan = build_preflight_plan(Path(args.catalog))
        if args.plan_output:
            output = Path(args.plan_output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(json.dumps({
            "catalog_file": plan["catalog_file"],
            "n_targets": plan["n_targets"],
            "n_ready_targets": plan["n_ready_targets"],
            "projects": plan["projects"],
            "plan_output": args.plan_output,
        }, indent=2))
        return 0 if plan["n_ready_targets"] == plan["n_targets"] else 1
    if not args.run_name:
        raise SystemExit("--run-name is required unless --preflight-only is used")
    result = run_mull_catalog(
        catalog_file=Path(args.catalog),
        run_name=args.run_name,
        output_root=Path(args.output_root) / args.run_name,
        mutators=args.mutators,
        timeout_ms=args.timeout_ms,
        only_target_id=args.only_target_id,
        resume=args.resume,
    )
    campaign_dir = Path(args.output_root) / args.run_name
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign_manifest.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print("\n=== Catalog summary ===")
    for t in result["targets"]:
        print(f"  {t['target_id']}: {t['status']}"
              + (f" ({t.get('total_mutants')} mutants, score={t.get('mutation_score'):.2f})"
                 if t["status"] == "OK" else ""))
    ok = sum(
        1 for t in result["targets"]
        if t["status"] in {"OK", "SKIPPED_COMPLETE"}
    )
    print(f"\n{ok}/{len(result['targets'])} targets completed or safely resumed")
    return 0 if ok == len(result["targets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
