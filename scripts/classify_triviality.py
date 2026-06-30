#!/usr/bin/env python3
"""
Passos 5-7: Parse rerun logs → kill_cause_long_llm.csv + mutant_summary_with_triviality_llm.csv

Trivial (ISSTA'17): killed == True AND n_killing_tests == eligible_test_count
                    AND every kill is caused by a RUNTIME_EXCEPTION (not ASSERTION_FAILURE).

Outputs (written to harness/executions/java/llm/):
  kill_cause_long_llm.csv          — per (run_name, mutant_id, test_name) kill cause
  mutant_summary_with_triviality_llm.csv — per mutant with trivial in {True, False, unknown}
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LLM_DIR = REPO_ROOT / "harness" / "executions" / "java" / "llm"
RERUNS_DIR = LLM_DIR / "reruns_triviality"
KILL_MATRIX = LLM_DIR / "kill_matrix_long.csv"
MUTANT_SUMMARY = LLM_DIR / "mutant_summary.csv"
OUT_KILL_CAUSE = LLM_DIR / "kill_cause_long_llm.csv"
OUT_TRIVIALITY = LLM_DIR / "mutant_summary_with_triviality_llm.csv"

ASSERTION_CLASSES = {"junit.framework.AssertionFailedError", "junit.framework.ComparisonFailure"}


# ---------------------------------------------------------------------------
# Parser
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

    # Split on lines starting with "--- " (test block separator)
    blocks = re.split(r"\n--- ", ft_section)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        # First line: "ClassName::testMethod" (may have leading "--- " already stripped)
        header = lines[0].strip().lstrip("- ").strip()
        if "::" not in header:
            continue
        test_name = header

        # Find exception class: first non-empty line after header
        exception_class = None
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            # Exception line looks like "some.Class: message" or "some.Class"
            # Extract the class name (everything before first ':' or whitespace)
            exc = re.split(r"[:\s]", line)[0].strip()
            if exc and "." in exc:
                exception_class = exc
            break

        if exception_class is None:
            kill_cause = "RUNTIME_EXCEPTION"  # no exception line → treat as runtime
        elif exception_class in ASSERTION_CLASSES:
            kill_cause = "ASSERTION_FAILURE"
        else:
            kill_cause = "RUNTIME_EXCEPTION"

        results[test_name] = kill_cause

    return results if results else None


# ---------------------------------------------------------------------------
# Load reference data
# ---------------------------------------------------------------------------

def load_kill_matrix() -> dict[tuple[str, str], dict]:
    """Returns {(run_name, mutant_id): {target_id, model_name, tests: [...]}}"""
    data: dict[tuple[str, str], dict] = {}
    with open(KILL_MATRIX, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["run_name"], row["mutant_id"])
            if key not in data:
                data[key] = {
                    "target_id": row["target_id"],
                    "model_name": row["model_name"],
                    "tests": [],
                }
            data[key]["tests"].append(row["test_name"])
    return data


def load_mutant_summary() -> dict[tuple[str, str], dict]:
    """Returns {(run_name, mutant_id): row_dict}"""
    data = {}
    with open(MUTANT_SUMMARY, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["run_name"], row["mutant_id"])
            data[key] = row
    return data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Loading kill matrix from {KILL_MATRIX}")
    km = load_kill_matrix()
    print(f"  {len(km)} unique (run_name, mutant_id) pairs")

    print(f"Loading mutant summary from {MUTANT_SUMMARY}")
    ms = load_mutant_summary()
    print(f"  {len(ms)} mutant summary rows")

    # Parse all rerun logs
    print(f"\nParsing rerun logs from {RERUNS_DIR}")
    kill_cause_rows = []   # for kill_cause_long_llm.csv
    triviality: dict[tuple[str, str], str] = {}  # (run_name, mutant_id) → trivial

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
            model_name = km_entry["model_name"]
            eligible_tests = km_entry["tests"]

            if causes is None:
                # No D4J_FAILING_TESTS section → unknown kill cause
                stats["no_ft"] += 1
                no_ft_logs.append(str(log_path.relative_to(REPO_ROOT)))
                triviality[key] = "unknown"
                # Still emit rows for each killing test with unknown cause
                for test_name in eligible_tests:
                    kill_cause_rows.append({
                        "target_id": target_id,
                        "model_name": model_name,
                        "run_name": run_name,
                        "mutant_id": mutant_id,
                        "test_name": test_name,
                        "kill_cause": "unknown",
                    })
                continue

            stats["with_ft"] += 1

            # Emit kill_cause rows — use causes from failing_tests where available,
            # fall back to "unknown" for tests in km but not in failing_tests file
            # (can happen if a test was noted as failing in original run but not here)
            for test_name in eligible_tests:
                cause = causes.get(test_name, "unknown")
                kill_cause_rows.append({
                    "target_id": target_id,
                    "model_name": model_name,
                    "run_name": run_name,
                    "mutant_id": mutant_id,
                    "test_name": test_name,
                    "kill_cause": cause,
                })
                stats[f"cause_{cause}"] += 1

            # Classify triviality
            ms_row = ms.get(key)
            if ms_row is None:
                triviality[key] = "unknown"
                continue

            n_killing = int(ms_row["n_killing_tests"])
            eligible_count = int(ms_row["eligible_test_count"])

            all_causes = list(causes.values())
            all_runtime = all(c == "RUNTIME_EXCEPTION" for c in all_causes) if all_causes else False
            killed_by_all = n_killing == eligible_count

            if killed_by_all and all_runtime:
                triviality[key] = "True"
            else:
                triviality[key] = "False"

    # Write kill_cause_long_llm.csv
    print(f"\nWriting {OUT_KILL_CAUSE}")
    fieldnames = ["target_id", "model_name", "run_name", "mutant_id", "test_name", "kill_cause"]
    with open(OUT_KILL_CAUSE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kill_cause_rows)
    print(f"  {len(kill_cause_rows)} rows written")

    # Write mutant_summary_with_triviality_llm.csv
    print(f"\nWriting {OUT_TRIVIALITY}")
    out_rows = []
    ms_fields = list(next(iter(ms.values())).keys()) if ms else []
    for key, ms_row in sorted(ms.items()):
        row = dict(ms_row)
        row["trivial"] = triviality.get(key, "n/a")  # n/a for non-killed
        out_rows.append(row)

    fieldnames2 = ms_fields + ["trivial"]
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

    total_cause_rows = stats["cause_ASSERTION_FAILURE"] + stats["cause_RUNTIME_EXCEPTION"] + stats["cause_unknown"]
    if total_cause_rows:
        print(f"\nKill-cause breakdown (kill_matrix pairs):")
        print(f"  ASSERTION_FAILURE:  {stats['cause_ASSERTION_FAILURE']:>5}  ({100*stats['cause_ASSERTION_FAILURE']/total_cause_rows:.1f}%)")
        print(f"  RUNTIME_EXCEPTION:  {stats['cause_RUNTIME_EXCEPTION']:>5}  ({100*stats['cause_RUNTIME_EXCEPTION']/total_cause_rows:.1f}%)")
        print(f"  unknown:            {stats['cause_unknown']:>5}  ({100*stats['cause_unknown']/total_cause_rows:.1f}%)")

    triv_counts = defaultdict(int)
    for v in triviality.values():
        triv_counts[v] += 1
    total_triv = sum(triv_counts.values())
    print(f"\nTriviality classification (over {total_triv} killed mutants):")
    for label in ["True", "False", "unknown"]:
        n = triv_counts[label]
        print(f"  trivial={label:<8}: {n:>4}  ({100*n/total_triv:.1f}%)")

    # Per-model breakdown
    model_triv: dict[str, defaultdict] = defaultdict(lambda: defaultdict(int))
    for key, triv_val in triviality.items():
        ms_row = ms.get(key)
        if ms_row:
            model_triv[ms_row["model_name"]][triv_val] += 1

    print(f"\nTriviality by model:")
    print(f"  {'Model':<35} {'True':>6} {'False':>6} {'unk':>4}  trivial%")
    for model, counts in sorted(model_triv.items()):
        t = counts["True"]
        f_ = counts["False"]
        u = counts["unknown"]
        tot = t + f_ + u
        pct = 100 * t / tot if tot else 0
        print(f"  {model:<35} {t:>6} {f_:>6} {u:>4}  {pct:.1f}%")

    # Per-subject breakdown (top 15 by trivial count)
    subj_triv: dict[str, defaultdict] = defaultdict(lambda: defaultdict(int))
    for key, triv_val in triviality.items():
        ms_row = ms.get(key)
        if ms_row:
            # Extract subject from target_id pattern like "chart_1f_..."
            subj_triv[ms_row["target_id"].split("_")[0].capitalize()][triv_val] += 1

    print(f"\nTriviality by subject (top 15 by True count):")
    print(f"  {'Subject':<20} {'True':>6} {'False':>6} {'unk':>4}  trivial%")
    for subj, counts in sorted(subj_triv.items(), key=lambda x: -x[1]["True"])[:15]:
        t = counts["True"]
        f_ = counts["False"]
        u = counts["unknown"]
        tot = t + f_ + u
        pct = 100 * t / tot if tot else 0
        print(f"  {subj:<20} {t:>6} {f_:>6} {u:>4}  {pct:.1f}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
