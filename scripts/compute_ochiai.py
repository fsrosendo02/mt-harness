#!/usr/bin/env python3
"""Compute Ochiai coefficient per mutant and aggregate per model.

== Ochiai coefficient (Wang et al. / Khanfir et al.) ==
For mutant m and real bug b:
  ochiai(m, b) = |killing_tests(m) ∩ trigger_tests(b)| / sqrt(|killing_tests(m)| × |trigger_tests(b)|)
  ochiai = 0 when killing_tests(m) is empty (mutant not killed)
  ochiai = 0 when trigger_tests(b) is empty (no trigger tests known — not possible here)

Consistency assertion: ochiai == 1.0 iff exact_match == True  (proved:
  |A ∩ B| / sqrt(|A|×|B|) = 1 iff A == B, given A,B non-empty)

== Aggregation — "official" (no kill-signature deduplication) ==
  avg_ochiai_target: for each target_id (176 units), compute mean Ochiai over
    all executable mutants of that target for the model; then mean across targets
    that have ≥1 executable mutant. Targets with 0 executable mutants are excluded.
  avg_ochiai_bug: for each subject_id (88 units), pool all executable mutants
    of all targets belonging to that bug; compute mean Ochiai; then mean across
    bugs with ≥1 executable mutant.

== Aggregation — sensitivity analysis (kill-signature deduplication) ==
  This is NOT Wang et al. — it is an internal sensitivity analysis inspired by
  subsumption/redundancy concepts (Ammann, Delamaro, Offutt).
  Within each (model, target_id), mutants are grouped by their killing-test
  signature (frozenset of killing tests). For each distinct signature, only the
  first mutant is retained. Ochiai is then averaged over deduplicated mutants.
  avg_ochiai_target_killsig_dedup / avg_ochiai_bug_killsig_dedup use the same
  target/bug aggregation logic as the official metric, but on the deduplicated set.

Output: harness/executions/java/llm/ochiai_results.csv
Also appends 'ochiai' column to harness/executions/java/llm/fault_coupling_mutant_level.csv
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10**7)

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = REPO_ROOT / "harness/executions/java/llm"

MUTANT_LEVEL_CSV = BASE / "fault_coupling_mutant_level.csv"
EVALUABLE_INDEX  = BASE / "experiment_index_evaluable.csv"
OUTPUT_CSV       = BASE / "ochiai_results.csv"

MODELS = [
    "codegeex4:9b",
    "codellama:13b",
    "deepseek-coder-v2:16b",
    "llama3.1:8b",
    "qwen3:14b",
    "qwen2.5-coder:14b",
]

OCHIAI_HIGH_THRESHOLD = 0.8

REFERENCE = {
    "codegeex4:9b":         {"target": 0.0140, "bug": 0.0172},
    "codellama:13b":        {"target": 0.0154, "bug": 0.0197},
    "deepseek-coder-v2:16b":{"target": 0.0157, "bug": 0.0121},
    "llama3.1:8b":          {"target": 0.0167, "bug": 0.0219},
    "qwen2.5-coder:14b":    {"target": 0.0194, "bug": 0.0234},
    "qwen3:14b":            {"target": 0.0177, "bug": 0.0230},
}


def ochiai(killing: frozenset, trigger: frozenset) -> float:
    if not killing or not trigger:
        return 0.0
    intersection = len(killing & trigger)
    return intersection / math.sqrt(len(killing) * len(trigger))


def load_evaluable_map() -> dict[str, str]:
    """Return {target_id: subject_id}."""
    mapping: dict[str, str] = {}
    with EVALUABLE_INDEX.open(newline="") as f:
        for row in csv.DictReader(f):
            mapping[row["target_id"]] = row["subject_id"]
    return mapping


def load_and_enrich_mutants(tid_to_sid: dict[str, str]) -> list[dict]:
    """Load mutant_level rows and add ochiai; write back to CSV."""
    rows = []
    with MUTANT_LEVEL_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        orig_fieldnames = list(reader.fieldnames)
        for row in reader:
            killing = frozenset(t for t in row["killing_tests"].split("|") if t)
            trigger = frozenset(t for t in row["trigger_tests"].split("|") if t)
            oi = ochiai(killing, trigger)
            row["ochiai"] = round(oi, 8)
            row["subject_id"] = tid_to_sid.get(row["target_id"], "")
            rows.append(row)

    # Write back with ochiai column appended (idempotent: re-running replaces)
    new_fieldnames = orig_fieldnames if "ochiai" in orig_fieldnames else orig_fieldnames + ["ochiai"]
    with MUTANT_LEVEL_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=new_fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in new_fieldnames})

    return rows


def assert_ochiai_exact_match_consistency(rows: list[dict]) -> None:
    """ochiai == 1.0 iff exact_match == True (for all rows)."""
    violations = []
    for row in rows:
        oi = float(row["ochiai"])
        em = row["exact_match"] == "True"
        is_one = abs(oi - 1.0) < 1e-9
        if is_one != em:
            violations.append(
                f"  {row['model_name']} | {row['target_id']} | {row['mutant_id']}: "
                f"ochiai={oi}, exact_match={em}"
            )
    if violations:
        print("ASSERTION FAILED: ochiai==1.0 <=> exact_match==True violated:")
        for v in violations:
            print(v)
        sys.exit(1)
    print("Assertion passed: ochiai==1.0 <=> exact_match==True (all rows)")


def aggregate_official(
    rows: list[dict],
    tid_to_sid: dict[str, str],
) -> dict[str, dict]:
    """Official (no killsig dedup): avg_ochiai_target and avg_ochiai_bug per model."""
    # Build per (model, target) lists of ochiai values
    target_ochiai: dict[tuple, list] = defaultdict(list)
    for row in rows:
        key = (row["model_name"], row["target_id"])
        target_ochiai[key].append(float(row["ochiai"]))

    results: dict[str, dict] = {m: {} for m in MODELS}
    for model in MODELS:
        # --- target level ---
        target_means = []
        for tid in tid_to_sid:
            vals = target_ochiai.get((model, tid), [])
            if vals:
                target_means.append(sum(vals) / len(vals))
        avg_target = sum(target_means) / len(target_means) if target_means else 0.0

        # --- bug level ---
        bug_vals: dict[str, list] = defaultdict(list)
        for (m, tid), vals in target_ochiai.items():
            if m == model:
                sid = tid_to_sid.get(tid, "")
                bug_vals[sid].extend(vals)
        bug_means = [sum(v) / len(v) for v in bug_vals.values() if v]
        avg_bug = sum(bug_means) / len(bug_means) if bug_means else 0.0

        results[model]["avg_ochiai_target"] = avg_target
        results[model]["avg_ochiai_bug"]    = avg_bug

    return results


def aggregate_killsig_dedup(
    rows: list[dict],
    tid_to_sid: dict[str, str],
) -> dict[str, dict]:
    """Sensitivity analysis: dedup by killing-signature within (model, target).
    NOT Wang et al. — internal analysis. Column names make this explicit.
    """
    # For each (model, target), keep one representative per unique killing signature
    from collections import OrderedDict
    dedup_rows: dict[tuple, OrderedDict] = defaultdict(OrderedDict)
    n_unique_sigs: dict[str, int] = defaultdict(int)
    n_total: dict[str, int] = defaultdict(int)

    for row in rows:
        model = row["model_name"]
        tid   = row["target_id"]
        sig   = row["killing_tests"]  # pipe-separated sorted string = canonical key
        key   = (model, tid)
        n_total[model] += 1
        if sig not in dedup_rows[key]:
            dedup_rows[key][sig] = float(row["ochiai"])
            n_unique_sigs[model] += 1

    # Average using same target/bug logic as official
    target_ochiai_dedup: dict[tuple, list] = defaultdict(list)
    for (model, tid), sig_dict in dedup_rows.items():
        target_ochiai_dedup[(model, tid)] = list(sig_dict.values())

    results: dict[str, dict] = {m: {} for m in MODELS}
    for model in MODELS:
        target_means = []
        for tid in tid_to_sid:
            vals = target_ochiai_dedup.get((model, tid), [])
            if vals:
                target_means.append(sum(vals) / len(vals))
        avg_target_dd = sum(target_means) / len(target_means) if target_means else 0.0

        bug_vals: dict[str, list] = defaultdict(list)
        for (m, tid), vals in target_ochiai_dedup.items():
            if m == model:
                sid = tid_to_sid.get(tid, "")
                bug_vals[sid].extend(vals)
        bug_means = [sum(v) / len(v) for v in bug_vals.values() if v]
        avg_bug_dd = sum(bug_means) / len(bug_means) if bug_means else 0.0

        total = n_total[model]
        unique = n_unique_sigs[model]
        reduction_pct = (1 - unique / total) * 100 if total else 0.0

        results[model]["avg_ochiai_target_killsig_dedup"] = avg_target_dd
        results[model]["avg_ochiai_bug_killsig_dedup"]    = avg_bug_dd
        results[model]["n_mutants_total"]                  = total
        results[model]["n_unique_kill_signatures"]          = unique
        results[model]["killsig_dedup_reduction_pct"]       = round(reduction_pct, 2)

    return results


def count_high_ochiai(rows: list[dict], threshold: float) -> dict[tuple, int]:
    """Count mutants with ochiai >= threshold, per (model, level)."""
    counts: dict[tuple, int] = defaultdict(int)
    for row in rows:
        if float(row["ochiai"]) >= threshold:
            counts[(row["model_name"], "target")] += 1
    return counts


def main() -> None:
    print("Loading evaluable index ...", flush=True)
    tid_to_sid = load_evaluable_map()

    print("Loading and enriching mutant-level CSV ...", flush=True)
    rows = load_and_enrich_mutants(tid_to_sid)
    print(f"  {len(rows)} mutant rows processed")

    print("Running consistency assertion ...", flush=True)
    assert_ochiai_exact_match_consistency(rows)

    print("Aggregating (official, no killsig dedup) ...", flush=True)
    official = aggregate_official(rows, tid_to_sid)

    print("Aggregating (killsig dedup — sensitivity analysis) ...", flush=True)
    dedup = aggregate_killsig_dedup(rows, tid_to_sid)

    high_ochiai = count_high_ochiai(rows, OCHIAI_HIGH_THRESHOLD)

    # --- Print results ---
    print()
    print("=" * 100)
    print(f"{'Model':<28} | {'AvgOchiai':^19} | {'AvgOchiai_KillsigDedup':^23} | {'#exec':>5} | {'#unique_sig':>11} | {'dedup%':>6}")
    print(f"{'':28} | {'target':>9} {'bug':>9} | {'target':>11} {'bug':>11} | {'':5} | {'':11} |")
    print("-" * 100)

    rows_out = []
    for model in MODELS:
        of = official[model]
        dd = dedup[model]
        n_high_t = high_ochiai.get((model, "target"), 0)

        print(
            f"{model:<28} | "
            f"{of['avg_ochiai_target']:>9.4f} {of['avg_ochiai_bug']:>9.4f} | "
            f"{dd['avg_ochiai_target_killsig_dedup']:>11.4f} {dd['avg_ochiai_bug_killsig_dedup']:>11.4f} | "
            f"{dd['n_mutants_total']:>5} | "
            f"{dd['n_unique_kill_signatures']:>11} | "
            f"{dd['killsig_dedup_reduction_pct']:>5.1f}%"
        )

        rows_out.append({
            "model":                          model,
            "avg_ochiai_target":              round(of["avg_ochiai_target"], 6),
            "avg_ochiai_bug":                 round(of["avg_ochiai_bug"], 6),
            "avg_ochiai_target_killsig_dedup": round(dd["avg_ochiai_target_killsig_dedup"], 6),
            "avg_ochiai_bug_killsig_dedup":    round(dd["avg_ochiai_bug_killsig_dedup"], 6),
            "n_mutants_total":                 dd["n_mutants_total"],
            "n_unique_kill_signatures":        dd["n_unique_kill_signatures"],
            "killsig_dedup_reduction_pct":     dd["killsig_dedup_reduction_pct"],
            f"high_ochiai_count_target_gte{int(OCHIAI_HIGH_THRESHOLD*10)}": n_high_t,
        })

    print("=" * 100)

    # --- Reference check ---
    print()
    print("Reference check (avg_ochiai_target | avg_ochiai_bug):")
    all_match = True
    for model in MODELS:
        ref = REFERENCE.get(model, {})
        got_t = official[model]["avg_ochiai_target"]
        got_b = official[model]["avg_ochiai_bug"]
        ref_t = ref.get("target", float("nan"))
        ref_b = ref.get("bug", float("nan"))
        tol = 5e-4
        match_t = abs(got_t - ref_t) < tol
        match_b = abs(got_b - ref_b) < tol
        status = "OK" if (match_t and match_b) else "MISMATCH"
        if status == "MISMATCH":
            all_match = False
        print(f"  {model:<28}: target={got_t:.4f} (ref={ref_t:.4f}, {'ok' if match_t else 'FAIL'}) | "
              f"bug={got_b:.4f} (ref={ref_b:.4f}, {'ok' if match_b else 'FAIL'}) [{status}]")
    if all_match:
        print("  All reference values match within tolerance 5e-4.")
    else:
        print("  WARNING: some values differ from reference — check aggregation logic.")

    # Write output
    fieldnames = [
        "model",
        "avg_ochiai_target", "avg_ochiai_bug",
        "avg_ochiai_target_killsig_dedup", "avg_ochiai_bug_killsig_dedup",
        "n_mutants_total", "n_unique_kill_signatures", "killsig_dedup_reduction_pct",
        f"high_ochiai_count_target_gte{int(OCHIAI_HIGH_THRESHOLD*10)}",
    ]
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print()
    print(f"Outputs:")
    print(f"  {OUTPUT_CSV}")
    print(f"  {MUTANT_LEVEL_CSV}  (ochiai column appended/updated)")


if __name__ == "__main__":
    main()
