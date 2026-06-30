#!/usr/bin/env python3
"""Compute fault coupling, Ochiai, and DMR for the Major mutation testing baseline.

Major has completed execution for 149/176 evaluable targets (81/88 bugs).
The 27 remaining targets (7 bugs completely absent: Chart-20, Closure-1/44/46/47/125,
Time-22; plus Mockito-1 with 9/10 targets absent, Gson-2 with 3/6 absent, and
JacksonDatabind-80 with 1/2 absent) are treated as zero executable mutants — not
excluded. Major's inability to generate/execute mutants for those targets is part of
its measured performance, not a gap in data to work around.

Denominators: 176 targets / 88 bugs (same as LLM baseline, for direct comparison).

DMR: Major uses fixed syntactic operators with no pre-execution deduplication by code
identity. DMR = 0.0 by construction. Build failures (BASELINE_FAIL / FAIL, 20 mutants)
are not duplicates — they are non-compilable mutants, excluded from the executable
mutant count but distinct from the LLM concept of rejected-before-execution candidates.

Inputs (read-only):
  harness/executions/java/major/execution/major_full_catalog_try2_exec/results.csv
  harness/executions/java/major/execution/major_full_catalog_try2_exec/test_results.csv
  harness/executions/java/llm/experiment_index_evaluable.csv
  harness/executions/java/llm/defects4j_triggering_tests.csv
  harness/executions/java/llm/fault_coupling_results.csv   (LLM results, not modified)

Outputs (never overwrite LLM files):
  harness/executions/java/llm/fault_coupling_results_with_major.csv
  harness/executions/java/llm/fault_coupling_major_mutant_level.csv
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10**7)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = REPO_ROOT / "harness/executions/java/llm"
MAJOR_EXEC = (
    REPO_ROOT
    / "harness/executions/java/major/execution/major_full_catalog_try2_exec"
)

MAJOR_RESULTS      = MAJOR_EXEC / "results.csv"
MAJOR_TEST_RESULTS = MAJOR_EXEC / "test_results.csv"
EVALUABLE_INDEX    = BASE / "experiment_index_evaluable.csv"
TRIGGERING_TESTS   = BASE / "defects4j_triggering_tests.csv"
LLM_FC_RESULTS     = BASE / "fault_coupling_results.csv"

OUT_WITH_MAJOR   = BASE / "fault_coupling_results_with_major.csv"
OUT_MUTANT_LEVEL = BASE / "fault_coupling_major_mutant_level.csv"

MODEL_NAME      = "major"
N_TARGETS_TOTAL = 176
N_BUGS_TOTAL    = 88


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ochiai(killing: frozenset, trigger: frozenset) -> float:
    if not killing or not trigger:
        return 0.0
    return len(killing & trigger) / math.sqrt(len(killing) * len(trigger))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_major_attempted(evaluable_tids: set[str]) -> tuple[int, int, dict[str, str]]:
    """Count evaluable targets/bugs where Major had status=ok.

    Returns (n_targets_attempted, n_bugs_attempted, {target_id: subject_id for ok targets}).
    """
    tid_to_sid: dict[str, str] = {}
    with EVALUABLE_INDEX.open(newline="") as f:
        for row in csv.DictReader(f):
            tid_to_sid[row["target_id"]] = row["subject_id"]

    attempted_tids: set[str] = set()
    with (MAJOR_EXEC / "targets.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if row["status"] == "ok" and row["target_id"] in evaluable_tids:
                attempted_tids.add(row["target_id"])

    attempted_bugs = set(tid_to_sid[t] for t in attempted_tids)
    return len(attempted_tids), len(attempted_bugs)


def load_evaluable() -> tuple[set[str], dict[str, str]]:
    """Returns (set of 176 target_ids, {target_id: subject_id})."""
    tids: set[str] = set()
    tid_to_sid: dict[str, str] = {}
    with EVALUABLE_INDEX.open(newline="") as f:
        for row in csv.DictReader(f):
            tids.add(row["target_id"])
            tid_to_sid[row["target_id"]] = row["subject_id"]
    return tids, tid_to_sid


def load_triggering_tests() -> dict[str, frozenset]:
    trigger: dict[str, set] = defaultdict(set)
    with TRIGGERING_TESTS.open(newline="") as f:
        for row in csv.DictReader(f):
            trigger[row["target_id"]].add(row["triggering_test"])
    return {tid: frozenset(tests) for tid, tests in trigger.items()}


def load_major_executable_mutants(evaluable_tids: set[str]) -> list[dict]:
    """Load rows from results.csv that are executable and in the evaluable set."""
    rows = []
    with MAJOR_RESULTS.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["target_id"] in evaluable_tids and row["executable"] == "True":
                rows.append(row)
    return rows


def load_major_killing_tests(evaluable_tids: set[str]) -> dict[tuple, frozenset]:
    """Return {(target_id, mutant_id): frozenset(test_names that killed the mutant)}.

    A test kills a mutant when outcome == 'FAIL' and the mutant is executable.
    (executable=False rows always have outcome=NOT_RUN, but we filter both for clarity.)
    """
    killing: dict[tuple, set] = defaultdict(set)
    with MAJOR_TEST_RESULTS.open(newline="") as f:
        for row in csv.DictReader(f):
            if (
                row["target_id"] in evaluable_tids
                and row["executable"] == "True"
                and row["outcome"] == "FAIL"
            ):
                killing[(row["target_id"], row["mutant_id"])].add(row["test_name"])
    return {k: frozenset(v) for k, v in killing.items()}


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_coupling(
    mutants: list[dict],
    killing: dict[tuple, frozenset],
    trigger: dict[str, frozenset],
) -> list[dict]:
    results = []
    for m in mutants:
        tid = m["target_id"]
        mid = m["mutant_id"]
        killing_tests = killing.get((tid, mid), frozenset())
        trigger_tests = trigger.get(tid, frozenset())

        exact_match = bool(killing_tests and trigger_tests and killing_tests == trigger_tests)
        coupled = bool(killing_tests & trigger_tests) if trigger_tests else False
        oi = round(_ochiai(killing_tests, trigger_tests), 8)

        results.append({
            "target_id":       tid,
            "model_name":      MODEL_NAME,
            "mutant_id":       mid,
            "killed":          m["killed"],
            "n_killing_tests": len(killing_tests),
            "killing_tests":   "|".join(sorted(killing_tests)),
            "trigger_tests":   "|".join(sorted(trigger_tests)),
            "exact_match":     exact_match,
            "coupled":         coupled,
            "ochiai":          oi,
        })
    return results


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(
    mutant_rows: list[dict],
    evaluable_tids: set[str],
    tid_to_sid: dict[str, str],
) -> dict:
    all_sids = set(tid_to_sid[tid] for tid in evaluable_tids)  # 88 bugs

    # --- Coupling Rate (mutant level) ---
    n_exec   = len(mutant_rows)
    n_exact  = sum(1 for r in mutant_rows if r["exact_match"])
    n_coupled = sum(1 for r in mutant_rows if r["coupled"])
    cr_exact  = n_exact  / n_exec if n_exec else 0.0
    cr_coupled = n_coupled / n_exec if n_exec else 0.0

    # --- RBDR @ target level (176) ---
    by_target: dict[str, list] = defaultdict(list)
    for r in mutant_rows:
        by_target[r["target_id"]].append(r)

    targets_exact = sum(
        1 for tid in evaluable_tids
        if any(r["exact_match"] for r in by_target.get(tid, []))
    )
    targets_coupled = sum(
        1 for tid in evaluable_tids
        if any(r["coupled"] for r in by_target.get(tid, []))
    )

    # --- RBDR @ bug level (88) ---
    sid_exact: dict[str, bool]   = defaultdict(bool)
    sid_coupled: dict[str, bool] = defaultdict(bool)
    for r in mutant_rows:
        sid = tid_to_sid.get(r["target_id"])
        if sid:
            if r["exact_match"]:
                sid_exact[sid] = True
            if r["coupled"]:
                sid_coupled[sid] = True

    bugs_exact  = sum(1 for sid in all_sids if sid_exact.get(sid, False))
    bugs_coupled = sum(1 for sid in all_sids if sid_coupled.get(sid, False))

    # --- Ochiai --- avg over targets with data, then avg over bugs with data
    target_ochiai: dict[str, list] = defaultdict(list)
    for r in mutant_rows:
        target_ochiai[r["target_id"]].append(float(r["ochiai"]))

    target_means: list[float] = []
    for tid in evaluable_tids:
        vals = target_ochiai.get(tid, [])
        if vals:
            target_means.append(sum(vals) / len(vals))
    avg_ochiai_target = sum(target_means) / len(target_means) if target_means else 0.0

    bug_vals: dict[str, list] = defaultdict(list)
    for tid, vals in target_ochiai.items():
        sid = tid_to_sid.get(tid, "")
        if sid:
            bug_vals[sid].extend(vals)
    bug_means = [sum(v) / len(v) for v in bug_vals.values() if v]
    avg_ochiai_bug = sum(bug_means) / len(bug_means) if bug_means else 0.0

    return {
        "model":                   MODEL_NAME,
        # RBDR @ target level
        "rbdr_target_exact":       round(targets_exact  / N_TARGETS_TOTAL, 6),
        "rbdr_target_coupled":     round(targets_coupled / N_TARGETS_TOTAL, 6),
        "n_targets_exact":         targets_exact,
        "n_targets_coupled":       targets_coupled,
        "n_targets_total":         N_TARGETS_TOTAL,
        # RBDR @ bug level
        "rbdr_bug_exact":          round(bugs_exact  / N_BUGS_TOTAL, 6),
        "rbdr_bug_coupled":        round(bugs_coupled / N_BUGS_TOTAL, 6),
        "n_bugs_exact":            bugs_exact,
        "n_bugs_coupled":          bugs_coupled,
        "n_bugs_total":            N_BUGS_TOTAL,
        # Coupling Rate
        "coupling_rate_exact":     round(cr_exact,   6),
        "coupling_rate_coupled":   round(cr_coupled, 6),
        "n_mutants_exact":         n_exact,
        "n_mutants_coupled":       n_coupled,
        "n_mutants_executable":    n_exec,
        # Ochiai
        "avg_ochiai_target":       round(avg_ochiai_target, 6),
        "avg_ochiai_bug":          round(avg_ochiai_bug,    6),
        # DMR (0 by design — no pre-execution syntactic deduplication in Major)
        "dmr":                     0.0,
        "n_total_generated":       n_exec,
        "n_duplicates":            0,
        # Methodological note columns (filled by caller)
        "n_targets_with_data":     None,
        "n_bugs_with_data":        None,
    }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_summary(
    result: dict,
    n_attempted_targets: int,
    n_attempted_bugs: int,
    n_exec_targets: int,
    n_exec_bugs: int,
) -> None:
    w = 72
    print("=" * w)
    print("Major fault coupling — denominator: 176 targets / 88 bugs")
    print("-" * w)
    print(f"  Major campaign coverage:")
    print(f"    Attempted (status=ok):       {n_attempted_targets}/176 targets  "
          f"({n_attempted_bugs}/88 bugs)")
    print(f"    With ≥1 executable mutant:   {n_exec_targets}/176 targets  "
          f"({n_exec_bugs}/88 bugs)")
    print(f"    Zero-mutant (all sources):   "
          f"{N_TARGETS_TOTAL - n_exec_targets}/176 — counted as 0, not excluded")
    print(f"  Executable mutants total:  {result['n_mutants_executable']}")
    print()
    print(f"  RBDR @ target:  exact={result['rbdr_target_exact']:.4f}  "
          f"coupled={result['rbdr_target_coupled']:.4f}  "
          f"({result['n_targets_exact']}/{N_TARGETS_TOTAL}  "
          f"{result['n_targets_coupled']}/{N_TARGETS_TOTAL})")
    print(f"  RBDR @ bug:     exact={result['rbdr_bug_exact']:.4f}  "
          f"coupled={result['rbdr_bug_coupled']:.4f}  "
          f"({result['n_bugs_exact']}/{N_BUGS_TOTAL}  "
          f"{result['n_bugs_coupled']}/{N_BUGS_TOTAL})")
    print(f"  Coupling Rate:  exact={result['coupling_rate_exact']:.4f}  "
          f"coupled={result['coupling_rate_coupled']:.4f}")
    print(f"  Avg Ochiai:     target={result['avg_ochiai_target']:.4f}  "
          f"bug={result['avg_ochiai_bug']:.4f}")
    print(f"  DMR:            {result['dmr']:.4f}  (0 by construction)")
    print("=" * w)


def write_outputs(mutant_rows: list[dict], result: dict) -> None:
    # --- Mutant-level CSV ---
    mutant_fieldnames = [
        "target_id", "model_name", "mutant_id", "killed", "n_killing_tests",
        "killing_tests", "trigger_tests", "exact_match", "coupled", "ochiai",
    ]
    with OUT_MUTANT_LEVEL.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=mutant_fieldnames)
        writer.writeheader()
        writer.writerows(mutant_rows)
    print(f"  {OUT_MUTANT_LEVEL}")

    # --- Combined results CSV: LLM rows + Major row ---
    llm_rows: list[dict] = []
    llm_fieldnames: list[str] = []
    with LLM_FC_RESULTS.open(newline="") as f:
        reader = csv.DictReader(f)
        llm_fieldnames = list(reader.fieldnames or [])
        llm_rows = list(reader)

    # Extra columns present only for the Major row (methodological note + Ochiai + DMR)
    extra_cols = [
        "avg_ochiai_target", "avg_ochiai_bug",
        "dmr", "n_total_generated", "n_duplicates",
        "n_targets_with_data", "n_bugs_with_data",
    ]
    combined_fieldnames = llm_fieldnames + [
        c for c in extra_cols if c not in llm_fieldnames
    ]

    with OUT_WITH_MAJOR.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=combined_fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in llm_rows:
            writer.writerow(row)
        writer.writerow({k: result.get(k, "") for k in combined_fieldnames})
    print(f"  {OUT_WITH_MAJOR}  ({len(llm_rows)} LLM rows + 1 Major row)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading evaluable set ...", flush=True)
    evaluable_tids, tid_to_sid = load_evaluable()
    print(f"  {len(evaluable_tids)} targets, {len(set(tid_to_sid.values()))} bugs")

    print("Loading triggering tests ...", flush=True)
    trigger = load_triggering_tests()
    print(f"  {sum(len(v) for v in trigger.values())} pairs across {len(trigger)} targets")

    print("Loading Major executable mutants ...", flush=True)
    mutants = load_major_executable_mutants(evaluable_tids)
    tids_with_exec = set(m["target_id"] for m in mutants)
    sids_with_exec = set(tid_to_sid[t] for t in tids_with_exec)
    n_attempted_targets, n_attempted_bugs = load_major_attempted(evaluable_tids)
    print(f"  {len(mutants)} executable mutants")
    print(f"  Attempted (status=ok): {n_attempted_targets}/176 targets  ({n_attempted_bugs}/88 bugs)")
    print(f"  With ≥1 exec mutant:   {len(tids_with_exec)}/176 targets  ({len(sids_with_exec)}/88 bugs)")
    print(f"  Zero-mutant targets:   {N_TARGETS_TOTAL - len(tids_with_exec)} — treated as zero, not excluded")

    print("Loading Major killing tests ...", flush=True)
    killing = load_major_killing_tests(evaluable_tids)
    print(f"  {len(killing)} (target, mutant) pairs with ≥1 killing test")

    missing_trigger = [tid for tid in evaluable_tids if tid not in trigger]
    if missing_trigger:
        print(f"WARNING: {len(missing_trigger)} evaluable targets have no triggering tests")
        for tid in sorted(missing_trigger):
            print(f"  {tid}")

    print("Computing coupling ...", flush=True)
    mutant_rows = compute_coupling(mutants, killing, trigger)

    print("Aggregating ...", flush=True)
    result = aggregate(mutant_rows, evaluable_tids, tid_to_sid)
    result["n_targets_with_data"] = n_attempted_targets
    result["n_bugs_with_data"]    = n_attempted_bugs

    print()
    print_summary(
        result,
        n_attempted_targets=n_attempted_targets,
        n_attempted_bugs=n_attempted_bugs,
        n_exec_targets=len(tids_with_exec),
        n_exec_bugs=len(sids_with_exec),
    )

    print("\nOutputs:")
    write_outputs(mutant_rows, result)


if __name__ == "__main__":
    main()
