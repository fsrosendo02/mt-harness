#!/usr/bin/env python3
"""Generate, but never execute, the staged ManyBugs Mull operation plan."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_mull_catalog import build_preflight_plan


REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_DIR = Path("harness/datasets/catalogs")
EXECUTION_ROOT = Path("harness/executions/c/mull/execution")
REPORT_ROOT = Path("harness/reports/c/fault_coupling")


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def catalog_for_project(project: str) -> Path:
    return CATALOG_DIR / f"manybugs_{project}_pilot.json"


def campaign_command(project: str, *, smoke_target: str | None = None) -> str:
    run_name = f"mull_{project}_pilot_fixed_v1"
    args = [
        "python3", "scripts/manybugs/mull/run_mull_catalog.py",
        "--catalog", str(catalog_for_project(project)),
        "--run-name", run_name,
        "--resume",
    ]
    if smoke_target:
        args.extend(["--only-target-id", smoke_target])
    return shell_join(args)


def coupling_command(project: str) -> str:
    run_name = f"mull_{project}_pilot_fixed_v1"
    return shell_join([
        "python3", "scripts/manybugs/compute_fault_coupling.py",
        "--catalog", str(catalog_for_project(project)),
        "--llm-root", "harness/executions/c/llm",
        "--mull-root", str(EXECUTION_ROOT / run_name),
        "--output-dir", str(REPORT_ROOT / f"{project}_pilot_fixed_v1"),
    ])


def build_execution_plan(catalog_file: Path) -> dict:
    preflight = build_preflight_plan(catalog_file)
    stages = []
    for project in preflight["projects"]:
        name = project["project"]
        blockers = project["blockers"]
        if project["ready"]:
            stages.append({
                "project": name,
                "state": "campaign_ready",
                "campaign_command": campaign_command(name),
                "coupling_command": coupling_command(name),
                "notes": "Run campaign with resume, audit it, then compute coupling.",
            })
        elif blockers == ["unvalidated_execution_adapter"]:
            stages.append({
                "project": name,
                "state": "smoke_required",
                "smoke_command": campaign_command(
                    name, smoke_target=project["suggested_smoke_target"]
                ),
                "promotion_required": (
                    "Audit baseline, embedded mutants, SQLite integrity and normalized "
                    "test vectors; only then add project to VALIDATED_EXECUTION_PROJECTS."
                ),
                "future_campaign_command": campaign_command(name),
                "future_coupling_command": coupling_command(name),
            })
        else:
            stages.append({
                "project": name,
                "state": "blocked",
                "blockers": blockers,
                "command": None,
                "notes": "No execution command emitted for a blocked project.",
            })
    return {
        "catalog_file": str(catalog_file),
        "execution_policy": {
            "automatic_execution": False,
            "source_revision_required": "fixed",
            "resume": True,
            "coupling_after_successful_audit_only": True,
        },
        "preflight": preflight,
        "stages": stages,
    }


def render_shell(plan: dict) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated plan. Review and run each command individually.",
        "# Smoke-required and blocked projects are intentionally not executable here.",
        "",
    ]
    for stage in plan["stages"]:
        lines.append(f"# {stage['project']}: {stage['state']}")
        if stage["state"] == "campaign_ready":
            lines.append(stage["campaign_command"])
            lines.append(stage["coupling_command"])
        elif stage["state"] == "smoke_required":
            lines.append(f"# SMOKE: {stage['smoke_command']}")
            lines.append("# Promote only after audit; full campaign intentionally omitted.")
        else:
            lines.append(f"# BLOCKED: {', '.join(stage['blockers'])}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", default="harness/datasets/catalogs/manybugs_all_pilots.json"
    )
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--shell-output", required=True)
    args = parser.parse_args()
    plan = build_execution_plan(Path(args.catalog))
    json_path = Path(args.json_output)
    shell_path = Path(args.shell_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    shell_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    shell_path.write_text(render_shell(plan), encoding="utf-8")
    print(f"Plan JSON: {json_path}")
    print(f"Review-only shell plan: {shell_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
