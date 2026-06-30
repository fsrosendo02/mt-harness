#!/usr/bin/env python3
"""Compute Duplicate Mutation Rate (DMR) per model — Wang et al., Eq. 6.

Definition (Wang et al., "A Comprehensive Study on Large Language Models for
Mutation Testing", Table 4 / Eq. 6):
  A mutant candidate is "duplicate" if its generated code is syntactically
  identical (after minimal normalisation) to either:
    (a) the original source of the target function, or
    (b) another mutant candidate already generated for the same (model, target).

  DMR = |duplicate candidates| / |total generated candidates|

Implementation note:
  The harness filters duplicates during LLM response parsing (harness/llm/parsing.py).
  Reason flags are recorded in experiment_index.csv:
    - rej_unchanged_mutant   → criterion (a): identical to original
    - rej_duplicate_mutant   → criterion (b): identical to another candidate
  Both categories are filtered before execution, so they never appear in
  mutant_summary.csv. We read the per-run counts directly from the index.

  n_total_generated per run = n_accepted_mutants + n_rejected_mutants
  (all items returned by the LLM, regardless of rejection reason)

  Deduplication scope: intra-run only (seen_codes set is reset per LLM call).
  Since each of the 176 evaluable targets has exactly 1 run per model, this
  matches the Wang et al. definition for our dataset.

Output columns:
  model, n_total_generated, n_duplicates, n_duplicate_of_original,
  n_duplicate_of_other_mutant, dmr
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE = REPO_ROOT / "harness/executions/java/llm"

EVALUABLE_INDEX = BASE / "experiment_index_evaluable.csv"
OUTPUT_CSV      = BASE / "duplicate_mutation_rate.csv"

MODELS = [
    "codegeex4:9b",
    "codellama:13b",
    "deepseek-coder-v2:16b",
    "llama3.1:8b",
    "qwen3:14b",
    "qwen2.5-coder:14b",
]


def main() -> None:
    stats: dict[str, dict] = {
        m: {"n_total_generated": 0, "n_dup_original": 0, "n_dup_other": 0, "n_runs_ok": 0}
        for m in MODELS
    }

    skipped_runs = 0
    with EVALUABLE_INDEX.open(newline="") as f:
        for row in csv.DictReader(f):
            model = row["model_name"]
            if model not in stats:
                continue
            if row["run_status"] != "ok":
                skipped_runs += 1
                continue

            n_accepted  = int(row["n_accepted_mutants"]  or 0)
            n_rejected  = int(row["n_rejected_mutants"]  or 0)
            n_dup_oth   = int(row["rej_duplicate_mutant"] or 0)
            n_dup_orig  = int(row["rej_unchanged_mutant"]  or 0)

            stats[model]["n_total_generated"] += n_accepted + n_rejected
            stats[model]["n_dup_original"]    += n_dup_orig
            stats[model]["n_dup_other"]       += n_dup_oth
            stats[model]["n_runs_ok"]         += 1

    rows_out = []
    print()
    print(f"{'Model':<28} | {'n_gen':>6} | {'n_dup':>6} | {'n_dup_orig':>10} | {'n_dup_other':>11} | {'DMR':>8}")
    print("-" * 85)
    for model in MODELS:
        s = stats[model]
        n_dup   = s["n_dup_original"] + s["n_dup_other"]
        n_total = s["n_total_generated"]
        dmr     = n_dup / n_total if n_total else 0.0

        print(
            f"{model:<28} | {n_total:>6} | {n_dup:>6} | "
            f"{s['n_dup_original']:>10} | {s['n_dup_other']:>11} | {dmr:>8.4f}"
        )

        rows_out.append({
            "model":                    model,
            "n_total_generated":        n_total,
            "n_duplicates":             n_dup,
            "n_duplicate_of_original":  s["n_dup_original"],
            "n_duplicate_of_other_mutant": s["n_dup_other"],
            "dmr":                      round(dmr, 6),
        })

    print("-" * 85)
    print(f"(skipped {skipped_runs} non-ok rows from evaluable index)")
    print()

    fieldnames = [
        "model", "n_total_generated", "n_duplicates",
        "n_duplicate_of_original", "n_duplicate_of_other_mutant", "dmr",
    ]
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
