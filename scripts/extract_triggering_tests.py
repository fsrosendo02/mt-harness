#!/usr/bin/env python3
"""Extract Defects4J triggering tests for all 176 evaluable targets.

For each unique bug (subject_id) in experiment_index_evaluable.csv:
  1. Checkout the buggy version (e.g. Chart-1b)
  2. Run `defects4j export -p tests.trigger`
  3. Record (target_id, project, bug_id, triggering_test) for every target that maps to the bug
  4. Clean up the checkout directory

Output: harness/executions/java/llm/defects4j_triggering_tests.csv
        harness/executions/java/llm/triggering_tests_exceptions.csv
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALUABLE_INDEX = REPO_ROOT / "harness/executions/java/llm/experiment_index_evaluable.csv"
OUTPUT_CSV = REPO_ROOT / "harness/executions/java/llm/defects4j_triggering_tests.csv"
EXCEPTIONS_CSV = REPO_ROOT / "harness/executions/java/llm/triggering_tests_exceptions.csv"

CHECKOUT_TIMEOUT = 300   # seconds
EXPORT_TIMEOUT = 60


def load_bugs(index_path: Path) -> dict[str, dict]:
    """Return {subject_id: {project, bug_id, targets: set[target_id]}}."""
    bugs: dict[str, dict] = {}
    with index_path.open(newline="") as f:
        for row in csv.DictReader(f):
            sid = row["subject_id"]
            if sid not in bugs:
                project, bug_id = sid.split("_", 1)
                bugs[sid] = {"project": project, "bug_id": bug_id, "targets": set()}
            bugs[sid]["targets"].add(row["target_id"])
    return bugs


def checkout_bug(project: str, bug_id: str, workdir: Path) -> tuple[bool, str]:
    version = f"{bug_id}b"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["defects4j", "checkout", "-p", project, "-v", version, "-w", str(workdir)],
            capture_output=True, text=True, timeout=CHECKOUT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {CHECKOUT_TIMEOUT}s"
    if result.returncode != 0:
        return False, (result.stdout + "\n" + result.stderr).strip()
    return True, ""


def export_trigger_tests(workdir: Path) -> tuple[list[str], str]:
    try:
        result = subprocess.run(
            ["defects4j", "export", "-p", "tests.trigger"],
            cwd=workdir,
            capture_output=True, text=True, timeout=EXPORT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [], f"TIMEOUT after {EXPORT_TIMEOUT}s"
    if result.returncode != 0:
        return [], (result.stdout + "\n" + result.stderr).strip()
    tests = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
    return tests, ""


def main() -> None:
    bugs = load_bugs(EVALUABLE_INDEX)
    print(f"Unique bugs to process: {len(bugs)}", flush=True)

    output_rows: list[dict] = []
    exception_rows: list[dict] = []

    for i, (subject_id, info) in enumerate(sorted(bugs.items()), 1):
        project = info["project"]
        bug_id = info["bug_id"]
        targets = sorted(info["targets"])
        d4j_id = f"{project}-{bug_id}b"

        print(f"[{i:3d}/{len(bugs)}] {d4j_id} ({len(targets)} target(s)) ...", end=" ", flush=True)

        with tempfile.TemporaryDirectory(prefix=f"d4j_{subject_id}_") as tmp:
            workdir = Path(tmp) / subject_id

            ok, err = checkout_bug(project, bug_id, workdir)
            if not ok:
                print(f"CHECKOUT FAILED", flush=True)
                for tid in targets:
                    exception_rows.append({
                        "target_id": tid,
                        "subject_id": subject_id,
                        "project": project,
                        "bug_id": bug_id,
                        "stage": "checkout",
                        "error": err[:300],
                    })
                continue

            tests, err = export_trigger_tests(workdir)
            if err:
                print(f"EXPORT FAILED", flush=True)
                for tid in targets:
                    exception_rows.append({
                        "target_id": tid,
                        "subject_id": subject_id,
                        "project": project,
                        "bug_id": bug_id,
                        "stage": "export",
                        "error": err[:300],
                    })
                continue

            if not tests:
                print(f"WARNING: 0 triggering tests found", flush=True)
                for tid in targets:
                    exception_rows.append({
                        "target_id": tid,
                        "subject_id": subject_id,
                        "project": project,
                        "bug_id": bug_id,
                        "stage": "export",
                        "error": "0 triggering tests returned by defects4j export",
                    })
                continue

            print(f"OK ({len(tests)} trigger test(s))", flush=True)
            for tid in targets:
                for test in tests:
                    output_rows.append({
                        "target_id": tid,
                        "subject_id": subject_id,
                        "project": project,
                        "bug_id": bug_id,
                        "triggering_test": test,
                    })

    # Write output CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["target_id", "subject_id", "project", "bug_id", "triggering_test"])
        writer.writeheader()
        writer.writerows(output_rows)

    with EXCEPTIONS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["target_id", "subject_id", "project", "bug_id", "stage", "error"])
        writer.writeheader()
        writer.writerows(exception_rows)

    print()
    print("=" * 60)
    print(f"Done.")
    print(f"Output rows written: {len(output_rows)}")
    print(f"  -> {OUTPUT_CSV}")
    unique_tids = len({r['target_id'] for r in output_rows})
    unique_bugs = len({r['subject_id'] for r in output_rows})
    print(f"Targets with triggering tests: {unique_tids} / 176")
    print(f"Bugs with triggering tests:    {unique_bugs} / 88")
    print()
    if exception_rows:
        print(f"EXCEPTIONS ({len(exception_rows)} entries for {len({r['target_id'] for r in exception_rows})} targets):")
        for row in exception_rows:
            print(f"  [{row['stage']}] {row['target_id']}: {row['error'][:120]}")
        print(f"  -> {EXCEPTIONS_CSV}")
    else:
        print("No exceptions.")


if __name__ == "__main__":
    main()
