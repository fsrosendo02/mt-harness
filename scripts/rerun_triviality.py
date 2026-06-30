#!/usr/bin/env python3
"""
Re-run 2,625 killed LLM-Java mutants to capture per-test kill-cause data.

For each killed (run_name, mutant_id) pair from kill_matrix_long.csv:
  1. Checkout the subject (cached per subject+version — only 83 unique checkouts)
  2. Compile the base snapshot once
  3. For each mutant: copy base → apply mutant → compile → test → capture failing_tests
  4. Write log to reruns_triviality/{run_name}/execution/{mutant_id}.log

The log format matches the harness logs and includes an
=== D4J_FAILING_TESTS === section with full stack traces.

Safe to interrupt: existing logs are skipped (idempotent).

Usage:
    cd /home/francisco/mt-harness
    python3 scripts/rerun_triviality.py [--workers N]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from multiprocessing import Pool, current_process
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LLM_DIR = REPO_ROOT / "harness" / "executions" / "java" / "llm"
KILL_MATRIX = LLM_DIR / "kill_matrix_long.csv"
RERUNS_DIR = LLM_DIR / "reruns_triviality"
TMP_BASE = REPO_ROOT / "tmp" / "rerun_triviality"
BASES_DIR = TMP_BASE / "bases"
WORKERS_DIR = TMP_BASE / "workers"

BUILD_TIMEOUT = 120
TEST_TIMEOUT = 300
CHECKOUT_TIMEOUT = 360


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    pid = os.getpid()
    print(f"[{ts()}][pid={pid}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_work_items() -> list[tuple[str, list[str]]]:
    """Return sorted list of (run_name, [mutant_id, ...]) from kill_matrix_long.csv."""
    runs: dict[str, set[str]] = defaultdict(set)
    with open(KILL_MATRIX, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            runs[row["run_name"]].add(row["mutant_id"])
    return [(run_name, sorted(mutant_ids)) for run_name, mutant_ids in sorted(runs.items())]


def find_run_dir(run_name: str) -> Path:
    batch_id = run_name.split("__")[0]
    candidate = LLM_DIR / batch_id / "runs" / run_name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Run directory not found: {candidate}")


def load_mutant_meta(run_dir: Path, mutant_id: str) -> dict:
    return json.loads((run_dir / "execution" / f"{mutant_id}.json").read_text(encoding="utf-8"))


def load_mutant_code(run_dir: Path, mutant_id: str) -> str:
    return (run_dir / "execution" / f"{mutant_id}.mutant.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Defects4J operations
# ---------------------------------------------------------------------------

def d4j_checkout(project: str, version: str, workdir: Path) -> tuple[bool, str]:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["defects4j", "checkout", "-p", project, "-v", version, "-w", str(workdir)],
        capture_output=True, text=True, timeout=CHECKOUT_TIMEOUT,
        cwd=str(REPO_ROOT),
    )
    return result.returncode == 0, result.stdout + "\n" + result.stderr


def d4j_compile(workdir: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["defects4j", "compile"],
        cwd=str(workdir), capture_output=True, text=True, timeout=BUILD_TIMEOUT,
    )
    return result.returncode == 0, result.stdout + "\n" + result.stderr


def d4j_test_and_capture(workdir: Path) -> tuple[bool, str]:
    """Run defects4j test and append the raw failing_tests file content."""
    ft_path = workdir / "failing_tests"
    # d4j-test cleans failing_tests before running, but delete it ourselves
    # in case of leftover from a previous run in the same workdir.
    if ft_path.exists():
        ft_path.unlink()

    result = subprocess.run(
        ["defects4j", "test"],
        cwd=str(workdir), capture_output=True, text=True, timeout=TEST_TIMEOUT,
    )
    log_output = result.stdout + "\n" + result.stderr

    if ft_path.exists():
        try:
            raw = ft_path.read_text(encoding="utf-8", errors="replace")
            if raw.strip():
                log_output += "\n\n=== D4J_FAILING_TESTS ===\n" + raw
        except OSError:
            pass

    return result.returncode == 0, log_output


def apply_mutant(workdir: Path, file_path: str, start_line: int, end_line: int, mutant_code: str) -> None:
    target = workdir / file_path
    original = target.read_text(encoding="utf-8")
    lines = original.splitlines()
    new_lines = lines[: start_line - 1] + mutant_code.splitlines() + lines[end_line:]
    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Base snapshot management
# ---------------------------------------------------------------------------

def base_key(subject_id: str, version: str) -> str:
    return f"{subject_id}_{version}"


def base_dir_for(subject_id: str, version: str) -> Path:
    return BASES_DIR / base_key(subject_id, version)


def base_sentinel(subject_id: str, version: str) -> Path:
    return base_dir_for(subject_id, version) / ".compiled"


def is_base_ready(subject_id: str, version: str) -> bool:
    return base_sentinel(subject_id, version).exists()


def build_base_snapshot(args: tuple[str, str]) -> tuple[str, bool, str]:
    """Build one base snapshot. Called by worker pool in Phase 1."""
    subject_id, version = args
    key = base_key(subject_id, version)

    if is_base_ready(subject_id, version):
        log(f"[base] SKIP {key} (already built)")
        return key, True, "cached"

    project, bug_id = subject_id.split("_", 1)
    d4j_version = f"{bug_id}{version}"
    workdir = base_dir_for(subject_id, version)

    log(f"[base] Checkout {project} {d4j_version} -> {workdir}")
    try:
        ok, out = d4j_checkout(project, d4j_version, workdir)
    except subprocess.TimeoutExpired:
        log(f"[base] ERROR checkout timeout for {key}")
        return key, False, "checkout_timeout"
    except Exception as e:
        log(f"[base] ERROR checkout for {key}: {e}")
        return key, False, f"checkout_error: {e}"

    if not ok:
        log(f"[base] ERROR checkout failed for {key}")
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
        return key, False, "checkout_failed"

    log(f"[base] Compile {key}")
    try:
        ok, out = d4j_compile(workdir)
    except subprocess.TimeoutExpired:
        log(f"[base] ERROR compile timeout for {key}")
        shutil.rmtree(workdir, ignore_errors=True)
        return key, False, "compile_timeout"
    except Exception as e:
        log(f"[base] ERROR compile for {key}: {e}")
        shutil.rmtree(workdir, ignore_errors=True)
        return key, False, f"compile_error: {e}"

    if not ok:
        log(f"[base] ERROR compile failed for {key}")
        shutil.rmtree(workdir, ignore_errors=True)
        return key, False, "compile_failed"

    base_sentinel(subject_id, version).write_text("ok\n", encoding="utf-8")
    log(f"[base] DONE {key}")
    return key, True, "built"


# ---------------------------------------------------------------------------
# Run processing (Phase 2)
# ---------------------------------------------------------------------------

def output_log_path(run_name: str, mutant_id: str) -> Path:
    return RERUNS_DIR / run_name / "execution" / f"{mutant_id}.log"


def is_done(run_name: str, mutant_id: str) -> bool:
    return output_log_path(run_name, mutant_id).exists()


def process_run(args: tuple[str, list[str]]) -> tuple[str, int, int, int]:
    """Process all pending mutants in a run. Returns (run_name, n_total, n_done, n_errors)."""
    run_name, mutant_ids = args
    pending = [m for m in mutant_ids if not is_done(run_name, m)]

    if not pending:
        log(f"SKIP {run_name}: all {len(mutant_ids)} already done")
        return run_name, len(mutant_ids), 0, 0

    log(f"START {run_name}: {len(pending)}/{len(mutant_ids)} pending")

    try:
        run_dir = find_run_dir(run_name)
    except FileNotFoundError as e:
        log(f"ERROR {run_name}: {e}")
        return run_name, len(mutant_ids), 0, len(pending)

    try:
        first_meta = load_mutant_meta(run_dir, pending[0])
    except Exception as e:
        log(f"ERROR {run_name}: cannot load mutant meta: {e}")
        return run_name, len(mutant_ids), 0, len(pending)

    subject_id = first_meta["subject_id"]
    version = first_meta["version"]

    if not is_base_ready(subject_id, version):
        log(f"ERROR {run_name}: base snapshot not ready for {subject_id}_{version}")
        return run_name, len(mutant_ids), 0, len(pending)

    base_path = base_dir_for(subject_id, version)
    worker_dir = WORKERS_DIR / f"{current_process().pid}_{run_name}"

    done_count = 0
    error_count = 0

    try:
        for mutant_id in pending:
            if is_done(run_name, mutant_id):
                done_count += 1
                continue

            try:
                meta = load_mutant_meta(run_dir, mutant_id)
                code = load_mutant_code(run_dir, mutant_id)
            except Exception as e:
                log(f"ERROR {run_name}/{mutant_id}: load failed: {e}")
                error_count += 1
                continue

            # Copy base snapshot
            if worker_dir.exists():
                shutil.rmtree(worker_dir)
            try:
                shutil.copytree(base_path, worker_dir, symlinks=True, ignore_dangling_symlinks=True)
            except Exception as e:
                log(f"ERROR {run_name}/{mutant_id}: copytree failed: {e}")
                error_count += 1
                continue

            # Apply mutant
            try:
                apply_mutant(
                    worker_dir,
                    meta["file_path"],
                    meta["start_line"],
                    meta["end_line"],
                    code,
                )
            except Exception as e:
                log(f"ERROR {run_name}/{mutant_id}: apply_mutant failed: {e}")
                error_count += 1
                continue

            # Build mutant
            try:
                build_ok, build_log = d4j_compile(worker_dir)
            except subprocess.TimeoutExpired:
                build_ok, build_log = False, f"BUILD TIMEOUT after {BUILD_TIMEOUT}s"
            except Exception as e:
                build_ok, build_log = False, f"BUILD ERROR: {e}"

            if not build_ok:
                log_content = (
                    f"=== RERUN_TRIVIALITY run_name={run_name} mutant_id={mutant_id} ===\n\n"
                    f"=== MUTANT BUILD ===\n\nBUILD FAILED\n\n{build_log}"
                )
            else:
                # Test mutant and capture failing_tests
                try:
                    _, test_log = d4j_test_and_capture(worker_dir)
                except subprocess.TimeoutExpired:
                    test_log = f"TEST TIMEOUT after {TEST_TIMEOUT}s"
                except Exception as e:
                    test_log = f"TEST ERROR: {e}"

                log_content = (
                    f"=== RERUN_TRIVIALITY run_name={run_name} mutant_id={mutant_id} ===\n\n"
                    f"=== MUTANT BUILD ===\n\nBUILD OK\n\n{build_log}\n\n"
                    f"=== MUTANT TEST ===\n\n{test_log}"
                )

            out_path = output_log_path(run_name, mutant_id)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(log_content, encoding="utf-8")

            done_count += 1
            log(f"DONE {run_name}/{mutant_id} ({done_count}/{len(pending)})")

    finally:
        if worker_dir.exists():
            shutil.rmtree(worker_dir, ignore_errors=True)

    log(f"FINISHED {run_name}: {done_count} done, {error_count} errors")
    return run_name, len(mutant_ids), done_count, error_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", "-j", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--phase", choices=["1", "2", "all"], default="all",
                        help="Run only phase 1 (base snapshots), phase 2 (mutant tests), or all")
    parser.add_argument("--dry-run", action="store_true", help="Print work items without executing")
    args = parser.parse_args()

    log(f"HARNESS ROOT: {REPO_ROOT}")
    log(f"KILL MATRIX:  {KILL_MATRIX}")
    log(f"OUTPUT DIR:   {RERUNS_DIR}")
    log(f"TMP DIR:      {TMP_BASE}")
    log(f"WORKERS:      {args.workers}")

    if not KILL_MATRIX.exists():
        print(f"ERROR: {KILL_MATRIX} not found", file=sys.stderr)
        sys.exit(1)

    log("Loading work items...")
    work_items = load_work_items()
    total_runs = len(work_items)
    total_mutants = sum(len(mids) for _, mids in work_items)
    log(f"Found {total_runs} runs, {total_mutants} unique (run_name, mutant_id) pairs")

    # Collect unique (subject_id, version) pairs
    subject_versions: dict[tuple[str, str], None] = {}
    for run_name, mutant_ids in work_items:
        try:
            run_dir = find_run_dir(run_name)
            meta = load_mutant_meta(run_dir, mutant_ids[0])
            sv = (meta["subject_id"], meta["version"])
            subject_versions[sv] = None
        except Exception:
            pass

    unique_subjects = list(subject_versions.keys())
    log(f"Unique (subject, version) pairs: {len(unique_subjects)}")

    if args.dry_run:
        log("DRY RUN — exiting without execution")
        return

    TMP_BASE.mkdir(parents=True, exist_ok=True)
    BASES_DIR.mkdir(parents=True, exist_ok=True)
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    RERUNS_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Phase 1: Build base snapshots
    # -----------------------------------------------------------------------
    if args.phase in ("1", "all"):
        log(f"\n=== PHASE 1: Building {len(unique_subjects)} base snapshots with {args.workers} workers ===")
        phase1_start = time.time()
        phase1_errors: list[str] = []

        with Pool(processes=args.workers) as pool:
            for key, ok, reason in pool.imap_unordered(build_base_snapshot, unique_subjects):
                if not ok:
                    phase1_errors.append(f"{key}: {reason}")
                    log(f"[phase1] FAILED {key}: {reason}")
                else:
                    log(f"[phase1] OK {key} ({reason})")

        phase1_elapsed = time.time() - phase1_start
        log(f"Phase 1 done in {phase1_elapsed:.0f}s. Errors: {len(phase1_errors)}")
        for err in phase1_errors:
            log(f"  BASE ERROR: {err}")

    # -----------------------------------------------------------------------
    # Phase 2: Process mutants
    # -----------------------------------------------------------------------
    if args.phase in ("2", "all"):
        already_done = sum(
            1 for run_name, mids in work_items for m in mids if is_done(run_name, m)
        )
        log(f"\n=== PHASE 2: Processing {total_mutants} mutants ({already_done} already done) ===")
        log(f"Workers: {args.workers}")

        phase2_start = time.time()
        total_done = 0
        total_errors = 0
        completed_runs = 0

        with Pool(processes=args.workers) as pool:
            for run_name, n_total, n_done, n_errors in pool.imap_unordered(process_run, work_items):
                total_done += n_done
                total_errors += n_errors
                completed_runs += 1
                elapsed = time.time() - phase2_start
                pct = 100 * (already_done + total_done) / total_mutants
                log(
                    f"[progress] {completed_runs}/{total_runs} runs | "
                    f"{already_done + total_done}/{total_mutants} mutants ({pct:.1f}%) | "
                    f"errors={total_errors} | elapsed={elapsed:.0f}s"
                )

        phase2_elapsed = time.time() - phase2_start
        log(
            f"\nPhase 2 done in {phase2_elapsed:.0f}s. "
            f"Done: {total_done} new + {already_done} cached = {total_done + already_done}/{total_mutants}. "
            f"Errors: {total_errors}"
        )


if __name__ == "__main__":
    main()
