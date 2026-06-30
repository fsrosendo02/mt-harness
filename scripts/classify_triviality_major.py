#!/usr/bin/env python3
"""
Parse rerun logs → kill_cause_long_major.csv + mutant_summary_with_triviality_major.csv

Trivial (ISSTA'17): killed == True AND n_killing_tests == eligible_test_count
                    AND every kill is caused by a RUNTIME_EXCEPTION (not ASSERTION_FAILURE).

Inputs:
  harness/executions/java/major/execution/major_full_catalog_try2_exec/results.csv
  harness/executions/java/major/execution/major_full_catalog_try2_exec/test_results.csv
  harness/executions/java/major/reruns_triviality_major/

Outputs (written to harness/executions/java/major/):
  kill_cause_long_major.csv          — per (run_name, mutant_id, test_name) kill cause
  mutant_summary_with_triviality_major.csv — per mutant with trivial in {True, False, unknown}
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAJOR_DIR = REPO_ROOT / "harness" / "executions" / "java" / "major"
MAJOR_EXEC = MAJOR_DIR / "execution" / "major_full_catalog_try2_exec"
RERUNS_DIR = MAJOR_DIR / "reruns_triviality_major"
OUT_KILL_CAUSE = MAJOR_DIR / "kill_cause_long_major.csv"
OUT_TRIVIALITY = MAJOR_DIR / "mutant_summary_with_triviality_major.csv"

ASSERTION_CLASSES = {"junit.framework.AssertionFailedError", "junit.framework.ComparisonFailure"}


# ---------------------------------------------------------------------------
# Parser (identical to classify_triviality.py)
# ---------------------------------------------------------------------------

def parse_kill_causes(log_text: str) -> dict[str, str] | None:
    """
    Parse the === D4J_FAILING_TESTS === section of a rerun log.
    Returns {test_name: kill_cause} or None if no section present.
    kill_cause is 'ASSERTION_FAILURE' or 'RUNTIME_EXCEPTION'.
    """
    marker = "=== D4J_FAILING_TESTS ==="
    idx = log_text.find(marker)
    if idx == -1:
        return None

    ft_section = log_text[idx + len(marker):]
    results: dict[str, str] = {}

    blocks = re.split(r"\n--- ", ft_section)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        header = lines[0].strip().lstrip("- ").strip()
        if "::" not in header:
            continue
        test_name = header

        exception_class = None
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            exc = re.split(r"[:\s]", line)[0].strip()
            if exc and "." in exc:
                exception_class = exc
            break

        if exception_class is None:
            kill_cause = "RUNTIME_EXCEPTION"
        elif exception_class in ASSERTION_CLASSES:
            kill_cause = "ASSERTION_FAILURE"
        else:
            kill_cause = "RUNTIME_EXCEPTION"

        results[test_name] = kill_cause

    return results if results else None


# ---------------------------------------------------------------------------
# Load reference data from Major execution CSVs
# ---------------------------------------------------------------------------

def load_kill_matrix() -> dict[tuple[str, str], dict]:
    """
    Returns {(run_name, mutant_id): {target_id, tests: [test_name, ...]}}
    from test_results.csv (eligible=True, executed=True, outcome=FAIL).
    """
    data: dict[tuple[str, str], dict] = {}
    with open(MAJOR_EXEC / "test_results.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (
                row["eligible"] == "True"
                and row["executed"] == "True"
                and row["outcome"] == "FAIL"
            ):
                key = (row["run_name"], row["mutant_id"])
                if key not in data:
                    data[key] = {"target_id": row["target_id"], "tests": []}
                data[key]["tests"].append(row["test_name"])
    return data


def load_mutant_summary() -> dict[tuple[str, str], dict]:
    """
    Returns {(run_name, mutant_id): {target_id, n_killing_tests, eligible_test_count, ...}}
    for killed executable mutants.

    eligible_test_count = number of eligible+executed tests for that mutant (from test_results).
    """
    # Count eligible tests per (run_name, mutant_id)
    eligible_counts: dict[tuple[str, str], int] = defaultdict(int)
    with open(MAJOR_EXEC / "test_results.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["eligible"] == "True" and row["executed"] == "True":
                key = (row["run_name"], row["mutant_id"])
                eligible_counts[key] += 1

    # Count killing tests per (run_name, mutant_id)
    killing_counts: dict[tuple[str, str], int] = defaultdict(int)
    with open(MAJOR_EXEC / "test_results.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (
                row["eligible"] == "True"
                and row["executed"] == "True"
                and row["outcome"] == "FAIL"
            ):
                key = (row["run_name"], row["mutant_id"])
                killing_counts[key] += 1

    # Load killed executable mutants from results.csv
    data: dict[tuple[str, str], dict] = {}
    with open(MAJOR_EXEC / "results.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (
                row["build_status"] == "SUCCESS"
                and row["executable"] == "True"
                and row["killed"] == "True"
            ):
                key = (row["run_name"], row["mutant_id"])
                data[key] = {
                    "target_id": row["target_id"],
                    "run_name": row["run_name"],
                    "mutant_id": row["mutant_id"],
                    "mutant_hash": row["mutant_hash"],
                    "n_killing_tests": killing_counts[key],
                    "eligible_test_count": eligible_counts[key],
                }
    return data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Loading kill matrix from {MAJOR_EXEC / 'test_results.csv'}")
    km = load_kill_matrix()
    print(f"  {len(km)} unique (run_name, mutant_id) killed pairs")

    print(f"Loading mutant summary from {MAJOR_EXEC / 'results.csv'}")
    ms = load_mutant_summary()
    print(f"  {len(ms)} killed executable mutant rows")

    if not RERUNS_DIR.exists():
        print(f"\nERROR: reruns directory not found: {RERUNS_DIR}", file=sys.stderr)
        print("Run scripts/rerun_triviality_major.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"\nParsing rerun logs from {RERUNS_DIR}")
    kill_cause_rows = []
    triviality: dict[tuple[str, str], str] = {}

    stats = defaultdict(int)
    no_ft_logs: list[str] = []

    for run_dir in sorted(RERUNS_DIR.iterdir()):
        run_name = run_dir.name
        exec_dir = run_dir / "execution"
        if not exec_dir.exists():
            continue

        for log_path in sorted(exec_dir.glob("*.log")):
            mutant_id = log_path.stem
            key = (run_name, mutant_id)

            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            causes = parse_kill_causes(log_text)

            km_entry = km.get(key)
            if km_entry is None:
                stats["key_not_in_km"] += 1
                continue

            target_id = km_entry["target_id"]
            eligible_tests = km_entry["tests"]

            if causes is None:
                stats["no_ft"] += 1
                no_ft_logs.append(str(log_path.relative_to(REPO_ROOT)))
                triviality[key] = "unknown"
                for test_name in eligible_tests:
                    kill_cause_rows.append({
                        "target_id": target_id,
                        "run_name": run_name,
                        "mutant_id": mutant_id,
                        "test_name": test_name,
                        "kill_cause": "unknown",
                    })
                continue

            stats["with_ft"] += 1

            for test_name in eligible_tests:
                cause = causes.get(test_name, "unknown")
                kill_cause_rows.append({
                    "target_id": target_id,
                    "run_name": run_name,
                    "mutant_id": mutant_id,
                    "test_name": test_name,
                    "kill_cause": cause,
                })
                stats[f"cause_{cause}"] += 1

            ms_row = ms.get(key)
            if ms_row is None:
                triviality[key] = "unknown"
                continue

            n_killing = ms_row["n_killing_tests"]
            eligible_count = ms_row["eligible_test_count"]

            all_causes = list(causes.values())
            all_runtime = all(c == "RUNTIME_EXCEPTION" for c in all_causes) if all_causes else False
            killed_by_all = n_killing == eligible_count

            if killed_by_all and all_runtime:
                triviality[key] = "True"
            else:
                triviality[key] = "False"

    # Write kill_cause_long_major.csv
    print(f"\nWriting {OUT_KILL_CAUSE}")
    fieldnames = ["target_id", "run_name", "mutant_id", "test_name", "kill_cause"]
    with open(OUT_KILL_CAUSE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kill_cause_rows)
    print(f"  {len(kill_cause_rows)} rows written")

    # Write mutant_summary_with_triviality_major.csv
    print(f"\nWriting {OUT_TRIVIALITY}")
    out_rows = []
    for key, ms_row in sorted(ms.items()):
        row = dict(ms_row)
        row["trivial"] = triviality.get(key, "n/a")
        out_rows.append(row)

    fieldnames2 = ["target_id", "run_name", "mutant_id", "mutant_hash",
                   "n_killing_tests", "eligible_test_count", "trivial"]
    with open(OUT_TRIVIALITY, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames2)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"  {len(out_rows)} rows written")

    # Sanity report
    print("\n=== SANITY REPORT ===")
    print(f"Logs parsed:           {stats['with_ft'] + stats['no_ft']}")
    print(f"  with D4J_FAILING_TESTS: {stats['with_ft']}")
    print(f"  without (unknown):      {stats['no_ft']}")
    if no_ft_logs:
        for p in no_ft_logs:
            print(f"    {p}")
    if stats["key_not_in_km"]:
        print(f"  keys not in kill matrix: {stats['key_not_in_km']}")

    total_cause_rows = stats["cause_ASSERTION_FAILURE"] + stats["cause_RUNTIME_EXCEPTION"] + stats["cause_unknown"]
    if total_cause_rows:
        print(f"\nKill-cause breakdown (killing test pairs):")
        print(f"  ASSERTION_FAILURE:  {stats['cause_ASSERTION_FAILURE']:>5}  ({100*stats['cause_ASSERTION_FAILURE']/total_cause_rows:.1f}%)")
        print(f"  RUNTIME_EXCEPTION:  {stats['cause_RUNTIME_EXCEPTION']:>5}  ({100*stats['cause_RUNTIME_EXCEPTION']/total_cause_rows:.1f}%)")
        print(f"  unknown:            {stats['cause_unknown']:>5}  ({100*stats['cause_unknown']/total_cause_rows:.1f}%)")

    triv_counts = defaultdict(int)
    for v in triviality.values():
        triv_counts[v] += 1
    total_triv = sum(triv_counts.values())
    if total_triv:
        print(f"\nTriviality classification (over {total_triv} classified killed mutants):")
        for label in ["True", "False", "unknown"]:
            n = triv_counts[label]
            pct = 100 * n / total_triv if total_triv else 0
            print(f"  trivial={label:<8}: {n:>4}  ({pct:.1f}%)")

    # Per-subject breakdown
    subj_triv: dict[str, defaultdict] = defaultdict(lambda: defaultdict(int))
    for key, triv_val in triviality.items():
        ms_row = ms.get(key)
        if ms_row:
            subj = ms_row["target_id"].split("_")[0].capitalize()
            subj_triv[subj][triv_val] += 1

    if subj_triv:
        print(f"\nTriviality by subject:")
        print(f"  {'Subject':<20} {'True':>6} {'False':>6} {'unk':>4}  trivial%")
        for subj, counts in sorted(subj_triv.items(), key=lambda x: -x[1]["True"]):
            t = counts["True"]
            f_ = counts["False"]
            u = counts["unknown"]
            tot = t + f_ + u
            pct = 100 * t / tot if tot else 0
            print(f"  {subj:<20} {t:>6} {f_:>6} {u:>4}  {pct:.1f}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
