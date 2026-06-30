#!/usr/bin/env python3
"""Compute DMR, kill matrix, and dominance summary for the Major baseline run.

Produces:
  harness/executions/java/major/major_dmr_detail.csv
  harness/executions/java/major/kill_matrix_long_major.csv
  harness/executions/java/major/mutant_summary_major.csv
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
MAJOR_EXEC = (
    REPO_ROOT
    / "harness/executions/java/major/execution/major_full_catalog_try2_exec"
)
OUT_DIR = REPO_ROOT / "harness/executions/java/major"


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

results = pd.read_csv(MAJOR_EXEC / "results.csv")
test_results = pd.read_csv(MAJOR_EXEC / "test_results.csv")

executable = results[(results["build_status"] == "SUCCESS") & (results["executable"] == True)].copy()

print(f"Loaded {len(results)} mutant rows, {len(executable)} executable")
print(f"Loaded {len(test_results)} test_result rows")


# ===========================================================================
# Task 1 — Duplicate Mutation Rate (DMR)
# ===========================================================================

groups = (
    executable.groupby(["target_id", "mutant_hash"])["mutant_id"]
    .apply(list)
    .reset_index()
    .rename(columns={"mutant_id": "mutant_ids"})
)
groups["group_size"] = groups["mutant_ids"].str.len()
groups["excess_duplicates"] = (groups["group_size"] - 1).clip(lower=0)
groups["mutant_ids"] = groups["mutant_ids"].apply(lambda ids: ";".join(sorted(ids)))

total_executable = len(executable)
total_excess = groups["excess_duplicates"].sum()
dmr = total_excess / total_executable

print()
print("=" * 60)
print("Task 1 — Duplicate Mutation Rate (DMR)")
print("=" * 60)
print(f"  Total executable mutants : {total_executable}")
print(f"  Total excess duplicates  : {total_excess}")
print(f"  DMR                      : {dmr:.6f}  ({dmr * 100:.2f}%)")

detail_path = OUT_DIR / "major_dmr_detail.csv"
groups[["target_id", "mutant_hash", "mutant_ids", "group_size", "excess_duplicates"]].to_csv(
    detail_path, index=False
)
print(f"\n  Wrote: {detail_path}")


# ===========================================================================
# Task 2 — Kill matrix and dominance
# ===========================================================================

# Build kill sets from test_results: eligible=True, executed=True, outcome=FAIL
kill_rows = test_results[
    (test_results["eligible"] == True)
    & (test_results["executed"] == True)
    & (test_results["outcome"] == "FAIL")
][["target_id", "mutant_id", "test_name"]].drop_duplicates()

# --- kill_matrix_long_major.csv ---
km_long = kill_rows.copy()
km_long.insert(1, "model_name", "Major")
km_path = OUT_DIR / "kill_matrix_long_major.csv"
km_long[["target_id", "model_name", "mutant_id", "test_name"]].to_csv(km_path, index=False)
print()
print("=" * 60)
print("Task 2 — Kill matrix")
print("=" * 60)
print(f"  Kill matrix rows (killed pairs) : {len(km_long)}")
print(f"  Wrote: {km_path}")


# --- Build kill-set index ---
kill_sets: dict[tuple[str, str], frozenset[str]] = {}
for (tid, mid), grp in kill_rows.groupby(["target_id", "mutant_id"]):
    kill_sets[(tid, mid)] = frozenset(grp["test_name"])

# All executable mutants (include unkilled ones — Kill = {})
hash_map = executable.set_index(["target_id", "mutant_id"])["mutant_hash"].to_dict()

for (tid, mid) in executable[["target_id", "mutant_id"]].itertuples(index=False, name=None):
    if (tid, mid) not in kill_sets:
        kill_sets[(tid, mid)] = frozenset()


# --- Compute dominance per target ---
# Group executable mutants by target_id
target_mutants: dict[str, list[str]] = {}
for (tid, mid) in executable[["target_id", "mutant_id"]].itertuples(index=False, name=None):
    target_mutants.setdefault(tid, []).append(mid)

is_dominator: dict[tuple[str, str], bool] = {}
is_indistinguishable: dict[tuple[str, str], bool] = {}

for tid, mids in target_mutants.items():
    sets = {mid: kill_sets[(tid, mid)] for mid in mids}

    # Indistinguishable: exists another mutant with same kill set
    # Build frequency map of frozensets (not directly hashable as dict key —
    # frozensets ARE hashable, so this works)
    set_counts: dict[frozenset, int] = {}
    for mid in mids:
        k = sets[mid]
        set_counts[k] = set_counts.get(k, 0) + 1

    for mid in mids:
        is_indistinguishable[(tid, mid)] = set_counts[sets[mid]] > 1

    # Dominator: not strictly subsumed by any other mutant in same target
    # m is dominated by m1 iff Kill(m) ⊂ Kill(m1)  (strict subset)
    for mid in mids:
        km = sets[mid]
        dominated = any(
            km < sets[other]  # strict subset
            for other in mids
            if other != mid
        )
        is_dominator[(tid, mid)] = not dominated


# --- Build mutant_summary_major.csv ---
rows = []
for (tid, mid), h in sorted(hash_map.items()):
    ks = kill_sets[(tid, mid)]
    rows.append(
        {
            "target_id": tid,
            "model_name": "Major",
            "mutant_id": mid,
            "mutant_hash": h,
            "n_tests_killed": len(ks),
            "is_dominator": is_dominator[(tid, mid)],
            "is_indistinguishable": is_indistinguishable[(tid, mid)],
        }
    )

summary = pd.DataFrame(rows)
ms_path = OUT_DIR / "mutant_summary_major.csv"
summary.to_csv(ms_path, index=False)

total_exec = len(summary)
total_killed = (summary["n_tests_killed"] > 0).sum()
total_dom = summary["is_dominator"].sum()
total_non_dom = (~summary["is_dominator"]).sum()
# Count indistinguishable pairs — pairs of mutants within same target that share kill set
# Recompute pair count properly
pair_count = 0
for tid, mids in target_mutants.items():
    sets = {mid: kill_sets[(tid, mid)] for mid in mids}
    set_counts: dict[frozenset, int] = {}
    for mid in mids:
        k = sets[mid]
        set_counts[k] = set_counts.get(k, 0) + 1
    for cnt in set_counts.values():
        if cnt >= 2:
            pair_count += cnt * (cnt - 1) // 2

print()
print("=" * 60)
print("Task 2 — Dominance summary")
print("=" * 60)
print(f"  Total executable mutants     : {total_exec}")
print(f"  Total killed (Kill(m) != {{}}) : {total_killed}")
print(f"  Total dominators             : {total_dom}")
print(f"  Total non-dominators         : {total_non_dom}")
print(f"  Total indistinguishable pairs: {pair_count}")
print(f"\n  Wrote: {ms_path}")


# ===========================================================================
# Task 3 — Triviality: pending
# ===========================================================================

print()
print("=" * 60)
print("Task 3 — Trivial mutants: PENDING")
print("=" * 60)
print(
    "  Cannot be computed from current data. failure_type is always\n"
    "  TEST_FAILURE and message carries no stack-trace detail.\n"
    "  Requires a re-run with Surefire XML / D4J failing_tests capture\n"
    "  (analogous to scripts/rerun_triviality.py for the LLM track)."
)
