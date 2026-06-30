#!/usr/bin/env python3
"""Compute fault coupling metrics between LLM mutants and real Defects4J bugs.

Inputs:
  kill_matrix_long.csv        -- (target_id, model_name, run_name, mutant_id, test_name) sparse killed=1
  mutant_summary.csv          -- (target_id, model_name, run_name, mutant_id, killed, ...) all executable mutants
  defects4j_triggering_tests.csv -- (target_id, subject_id, project, bug_id, triggering_test)
  experiment_index_evaluable.csv -- 176-target authoritative evaluable set

Outputs:
  fault_coupling_results.csv       -- one row per model, 6 metric columns
  fault_coupling_mutant_level.csv  -- one row per (target_id, model, mutant_id)

Metrics (all pooled: sum/sum, never mean-of-ratios):
  exact_match  : killing_tests == trigger_tests  (both non-empty)
  coupled      : killing_tests ∩ trigger_tests ≠ ∅

  Real Bug Detection Rate @ target level: targets with ≥1 coupled mutant / 176
  Real Bug Detection Rate @ bug level   : bugs with ≥1 coupled mutant in any target / 88
  Coupling Rate @ mutant level          : coupled mutants / total executable mutants
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = REPO_ROOT / "harness/executions/java/llm"

KILL_MATRIX_LONG   = BASE / "kill_matrix_long.csv"
MUTANT_SUMMARY     = BASE / "mutant_summary.csv"
TRIGGERING_TESTS   = BASE / "defects4j_triggering_tests.csv"
EVALUABLE_INDEX    = BASE / "experiment_index_evaluable.csv"

OUT_RESULTS        = BASE / "fault_coupling_results.csv"
OUT_MUTANT_LEVEL   = BASE / "fault_coupling_mutant_level.csv"

MODELS = [
    "codegeex4:9b",
    "codellama:13b",
    "deepseek-coder-v2:16b",
    "llama3.1:8b",
    "qwen3:14b",
    "qwen2.5-coder:14b",
]


# ---------------------------------------------------------------------------
# 1. Load evaluable set (176 targets → subject_id mapping)
# ---------------------------------------------------------------------------
def load_evaluable() -> tuple[set[str], dict[str, str]]:
    """Returns (set of target_ids, {target_id: subject_id})."""
    tids: set[str] = set()
    tid_to_sid: dict[str, str] = {}
    with EVALUABLE_INDEX.open(newline="") as f:
        for row in csv.DictReader(f):
            tids.add(row["target_id"])
            tid_to_sid[row["target_id"]] = row["subject_id"]
    return tids, tid_to_sid


# ---------------------------------------------------------------------------
# 2. Load triggering tests: {target_id: frozenset(tests)}
# ---------------------------------------------------------------------------
def load_triggering_tests() -> dict[str, frozenset]:
    trigger: dict[str, set] = defaultdict(set)
    with TRIGGERING_TESTS.open(newline="") as f:
        for row in csv.DictReader(f):
            trigger[row["target_id"]].add(row["triggering_test"])
    return {tid: frozenset(tests) for tid, tests in trigger.items()}


# ---------------------------------------------------------------------------
# 3. Load killing tests: {(target_id, model_name, mutant_id): set(tests)}
# ---------------------------------------------------------------------------
def load_killing_tests() -> dict[tuple, set]:
    killing: dict[tuple, set] = defaultdict(set)
    with KILL_MATRIX_LONG.open(newline="") as f:
        for row in csv.DictReader(f):
            key = (row["target_id"], row["model_name"], row["mutant_id"])
            killing[key].add(row["test_name"])
    return dict(killing)


# ---------------------------------------------------------------------------
# 4. Load mutant universe from mutant_summary
# ---------------------------------------------------------------------------
def load_mutant_universe() -> list[dict]:
    rows = []
    with MUTANT_SUMMARY.open(newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 5. Core computation
# ---------------------------------------------------------------------------
def compute_coupling(
    mutants: list[dict],
    killing: dict[tuple, set],
    trigger: dict[str, frozenset],
) -> list[dict]:
    """Return per-mutant rows with exact_match and coupled columns."""
    results = []
    for m in mutants:
        tid = m["target_id"]
        model = m["model_name"]
        mid = m["mutant_id"]

        killing_tests: frozenset = frozenset(killing.get((tid, model, mid), set()))
        trigger_tests: frozenset = trigger.get(tid, frozenset())

        if killing_tests and trigger_tests:
            exact_match = killing_tests == trigger_tests
        else:
            exact_match = False

        coupled = bool(killing_tests & trigger_tests) if trigger_tests else False

        results.append({
            "target_id":       tid,
            "model_name":      model,
            "mutant_id":       mid,
            "killed":          m["killed"],
            "n_killing_tests": m["n_killing_tests"],
            "killing_tests":   "|".join(sorted(killing_tests)),
            "trigger_tests":   "|".join(sorted(trigger_tests)),
            "exact_match":     exact_match,
            "coupled":         coupled,
        })
    return results


# ---------------------------------------------------------------------------
# 6. Aggregate metrics per model
# ---------------------------------------------------------------------------
def aggregate(
    mutant_rows: list[dict],
    evaluable_tids: set[str],
    tid_to_sid: dict[str, str],
) -> list[dict]:
    # Unique bugs in evaluable set
    all_sids = set(tid_to_sid[tid] for tid in evaluable_tids)
    n_targets = len(evaluable_tids)  # 176
    n_bugs = len(all_sids)           # 88

    results = []
    for model in MODELS:
        model_rows = [r for r in mutant_rows if r["model_name"] == model]

        # --- Coupling Rate (mutant level) ---
        n_executable = len(model_rows)
        n_exact_mutant = sum(1 for r in model_rows if r["exact_match"])
        n_coupled_mutant = sum(1 for r in model_rows if r["coupled"])

        cr_exact  = n_exact_mutant  / n_executable if n_executable else 0.0
        cr_coupled = n_coupled_mutant / n_executable if n_executable else 0.0

        # --- RBDR @ target level ---
        # For each target_id in evaluable set, check if ≥1 mutant couples for this model
        model_by_target: dict[str, list] = defaultdict(list)
        for r in model_rows:
            model_by_target[r["target_id"]].append(r)

        targets_exact  = sum(
            1 for tid in evaluable_tids
            if any(r["exact_match"] for r in model_by_target.get(tid, []))
        )
        targets_coupled = sum(
            1 for tid in evaluable_tids
            if any(r["coupled"] for r in model_by_target.get(tid, []))
        )

        rbdr_target_exact   = targets_exact  / n_targets
        rbdr_target_coupled = targets_coupled / n_targets

        # --- RBDR @ bug level ---
        # Group targets by subject_id, bug detected if ≥1 mutant in any target couples
        sid_exact: dict[str, bool] = defaultdict(bool)
        sid_coupled: dict[str, bool] = defaultdict(bool)
        for r in model_rows:
            sid = tid_to_sid.get(r["target_id"])
            if sid:
                if r["exact_match"]:
                    sid_exact[sid] = True
                if r["coupled"]:
                    sid_coupled[sid] = True

        bugs_exact   = sum(1 for sid in all_sids if sid_exact.get(sid, False))
        bugs_coupled = sum(1 for sid in all_sids if sid_coupled.get(sid, False))

        rbdr_bug_exact   = bugs_exact  / n_bugs
        rbdr_bug_coupled = bugs_coupled / n_bugs

        results.append({
            "model":                  model,
            # RBDR @ target level (176 units)
            "rbdr_target_exact":      round(rbdr_target_exact, 6),
            "rbdr_target_coupled":    round(rbdr_target_coupled, 6),
            "n_targets_exact":        targets_exact,
            "n_targets_coupled":      targets_coupled,
            "n_targets_total":        n_targets,
            # RBDR @ bug level (88 units)
            "rbdr_bug_exact":         round(rbdr_bug_exact, 6),
            "rbdr_bug_coupled":       round(rbdr_bug_coupled, 6),
            "n_bugs_exact":           bugs_exact,
            "n_bugs_coupled":         bugs_coupled,
            "n_bugs_total":           n_bugs,
            # Coupling Rate @ mutant level
            "coupling_rate_exact":    round(cr_exact, 6),
            "coupling_rate_coupled":  round(cr_coupled, 6),
            "n_mutants_exact":        n_exact_mutant,
            "n_mutants_coupled":      n_coupled_mutant,
            "n_mutants_executable":   n_executable,
        })
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("Loading data ...", flush=True)
    evaluable_tids, tid_to_sid = load_evaluable()
    trigger = load_triggering_tests()
    killing = load_killing_tests()
    mutants = load_mutant_universe()

    print(f"  Evaluable targets:    {len(evaluable_tids)}")
    print(f"  Unique bugs:          {len(set(tid_to_sid.values()))}")
    print(f"  kill_matrix entries:  {len(killing)}")
    print(f"  mutant_summary rows:  {len(mutants)}")
    print(f"  trigger test entries: {sum(len(v) for v in trigger.values())} (across {len(trigger)} targets)")
    print()

    # Targets with no triggering tests (should be 0)
    missing_trigger = [tid for tid in evaluable_tids if tid not in trigger]
    if missing_trigger:
        print(f"WARNING: {len(missing_trigger)} evaluable targets have no triggering tests:")
        for tid in sorted(missing_trigger):
            print(f"  {tid}")
    print()

    print("Computing coupling ...", flush=True)
    mutant_rows = compute_coupling(mutants, killing, trigger)

    print("Aggregating by model ...", flush=True)
    model_results = aggregate(mutant_rows, evaluable_tids, tid_to_sid)

    # Write mutant-level detail
    mutant_fieldnames = [
        "target_id", "model_name", "mutant_id", "killed", "n_killing_tests",
        "killing_tests", "trigger_tests", "exact_match", "coupled",
    ]
    with OUT_MUTANT_LEVEL.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=mutant_fieldnames)
        writer.writeheader()
        writer.writerows(mutant_rows)

    # Write results
    result_fieldnames = [
        "model",
        "rbdr_target_exact", "rbdr_target_coupled",
        "n_targets_exact", "n_targets_coupled", "n_targets_total",
        "rbdr_bug_exact", "rbdr_bug_coupled",
        "n_bugs_exact", "n_bugs_coupled", "n_bugs_total",
        "coupling_rate_exact", "coupling_rate_coupled",
        "n_mutants_exact", "n_mutants_coupled", "n_mutants_executable",
    ]
    with OUT_RESULTS.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=result_fieldnames)
        writer.writeheader()
        writer.writerows(model_results)

    # Print summary table
    print()
    print("=" * 110)
    print(f"{'Model':<28} | {'RBDR@target':^19} | {'RBDR@bug':^19} | {'Coupling Rate':^19} | {'#exec mutants'}")
    print(f"{'':28} | {'exact':>9} {'coupled':>9} | {'exact':>9} {'coupled':>9} | {'exact':>9} {'coupled':>9} |")
    print("-" * 110)
    for r in model_results:
        print(
            f"{r['model']:<28} | "
            f"{r['rbdr_target_exact']:>9.4f} {r['rbdr_target_coupled']:>9.4f} | "
            f"{r['rbdr_bug_exact']:>9.4f} {r['rbdr_bug_coupled']:>9.4f} | "
            f"{r['coupling_rate_exact']:>9.4f} {r['coupling_rate_coupled']:>9.4f} | "
            f"{r['n_mutants_executable']:>5}"
        )
    print("=" * 110)
    print()
    print(f"Outputs:")
    print(f"  {OUT_RESULTS}")
    print(f"  {OUT_MUTANT_LEVEL}")
    print()

    # Exception list: evaluable targets with 0 executable mutants (won't appear in mutant_summary)
    mutant_tids_by_model: dict[str, set] = defaultdict(set)
    for m in mutants:
        mutant_tids_by_model[m["model_name"]].add(m["target_id"])

    print("Evaluable targets with 0 executable mutants (all models):")
    zero_exec = sorted(
        tid for tid in evaluable_tids
        if all(tid not in mutant_tids_by_model[model] for model in MODELS)
    )
    if zero_exec:
        for tid in zero_exec:
            print(f"  {tid}")
    else:
        print("  (none)")


if __name__ == "__main__":
    main()
