#!/usr/bin/env python3
"""Compute DMR, kill matrix, and dominance summary for a mull baseline run.

Mirror of scripts/compute_major_baseline_analysis.py, adapted for the mull
(C/ManyBugs) execution layout: run_mull_catalog.py writes one
results.csv/test_results.csv PER TARGET (under
<run-root>/<run-name>__<subject>__<function>__<target_id>/execution/),
unlike Major's single combined results.csv for the whole catalog run — so
this script globs and concatenates across target subdirectories first,
then reuses the exact same DMR/kill-matrix/dominance algorithm.

Produces (mirroring the Major column shapes exactly, with model_name="mull"):
  <out-dir>/mull_dmr_detail.csv
  <out-dir>/kill_matrix_long_mull.csv
  <out-dir>/mutant_summary_mull.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

MODEL_NAME = "mull"


def load_run(run_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Concatenate results.csv/test_results.csv across all per-target
    subdirectories under run_root (each written by normalize_mull_report.py).
    """
    results_frames = []
    test_results_frames = []
    target_dirs = sorted(p for p in run_root.iterdir() if p.is_dir())
    if not target_dirs:
        raise FileNotFoundError(f"No target subdirectories found under {run_root}")

    for target_dir in target_dirs:
        results_csv = target_dir / "execution" / "results.csv"
        test_results_csv = target_dir / "execution" / "test_results.csv"
        if not results_csv.exists():
            print(f"  [skip] {target_dir.name}: no results.csv")
            continue
        results_frames.append(pd.read_csv(results_csv))
        test_results_frames.append(pd.read_csv(test_results_csv))

    if not results_frames:
        raise FileNotFoundError(f"No results.csv found under any subdirectory of {run_root}")

    return pd.concat(results_frames, ignore_index=True), pd.concat(test_results_frames, ignore_index=True)


def compute_baseline_analysis(run_root: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Loading mull run from {run_root}")
    results, test_results = load_run(run_root)

    executable = results[(results["build_status"] == "SUCCESS") & (results["executable"] == True)].copy()
    print(f"Loaded {len(results)} mutant rows, {len(executable)} executable")
    print(f"Loaded {len(test_results)} test_result rows")

    # =========================================================================
    # Task 1 — Duplicate Mutation Rate (DMR)
    # =========================================================================
    # mull mutants have no mutant_hash (see normalize_mull_report.py — left
    # blank on purpose, mutant_id is already the stable content-based key:
    # mutator+file+line+column). Group on mutant_id directly instead of
    # mutant_hash: for mull, two mutants sharing a mutant_id would be the
    # exact same mutation point, which normalize_mull_report.py already
    # deduplicates per target — so DMR is expected to be ~0 for mull unless
    # the same mutation point appears in more than one target (it can't,
    # target_id is part of the grouping key too). This section is kept for
    # schema parity with Major's output, not because duplicates are expected.
    groups = (
        executable.groupby(["target_id", "mutant_id"])["mutant_id"]
        .apply(list)
        .reset_index(drop=True)
        .to_frame("mutant_ids")
    )
    groups["group_size"] = groups["mutant_ids"].str.len()
    groups["excess_duplicates"] = (groups["group_size"] - 1).clip(lower=0)

    total_executable = len(executable)
    total_excess = int(groups["excess_duplicates"].sum())
    dmr = total_excess / total_executable if total_executable else 0.0

    print()
    print("=" * 60)
    print("Task 1 — Duplicate Mutation Rate (DMR)")
    print("=" * 60)
    print(f"  Total executable mutants : {total_executable}")
    print(f"  Total excess duplicates  : {total_excess}")
    print(f"  DMR                      : {dmr:.6f}  ({dmr * 100:.2f}%)")

    dmr_path = out_dir / f"{MODEL_NAME}_dmr_detail.csv"
    groups.to_csv(dmr_path, index=False)
    print(f"\n  Wrote: {dmr_path}")

    # =========================================================================
    # Task 2 — Kill matrix and dominance
    # =========================================================================
    kill_rows = test_results[
        (test_results["eligible"] == True)
        & (test_results["executed"] == True)
        & (test_results["outcome"] == "FAIL")
    ][["target_id", "mutant_id", "test_name"]].drop_duplicates()

    km_long = kill_rows.copy()
    km_long.insert(1, "model_name", MODEL_NAME)
    km_path = out_dir / f"kill_matrix_long_{MODEL_NAME}.csv"
    km_long[["target_id", "model_name", "mutant_id", "test_name"]].to_csv(km_path, index=False)
    print()
    print("=" * 60)
    print("Task 2 — Kill matrix")
    print("=" * 60)
    print(f"  Kill matrix rows (killed pairs) : {len(km_long)}")
    print(f"  Wrote: {km_path}")

    kill_sets: dict[tuple[str, str], frozenset[str]] = {}
    for (tid, mid), grp in kill_rows.groupby(["target_id", "mutant_id"]):
        kill_sets[(tid, mid)] = frozenset(grp["test_name"])

    hash_map = executable.set_index(["target_id", "mutant_id"])["mutant_hash"].to_dict()

    for (tid, mid) in executable[["target_id", "mutant_id"]].itertuples(index=False, name=None):
        if (tid, mid) not in kill_sets:
            kill_sets[(tid, mid)] = frozenset()

    target_mutants: dict[str, list[str]] = {}
    for (tid, mid) in executable[["target_id", "mutant_id"]].itertuples(index=False, name=None):
        target_mutants.setdefault(tid, []).append(mid)

    is_dominator: dict[tuple[str, str], bool] = {}
    is_indistinguishable: dict[tuple[str, str], bool] = {}

    for tid, mids in target_mutants.items():
        sets = {mid: kill_sets[(tid, mid)] for mid in mids}

        set_counts: dict[frozenset, int] = {}
        for mid in mids:
            k = sets[mid]
            set_counts[k] = set_counts.get(k, 0) + 1

        for mid in mids:
            is_indistinguishable[(tid, mid)] = set_counts[sets[mid]] > 1

        for mid in mids:
            km = sets[mid]
            dominated = any(km < sets[other] for other in mids if other != mid)
            is_dominator[(tid, mid)] = not dominated

    rows = []
    for (tid, mid), h in sorted(hash_map.items()):
        ks = kill_sets[(tid, mid)]
        rows.append({
            "target_id": tid,
            "model_name": MODEL_NAME,
            "mutant_id": mid,
            "mutant_hash": h,
            "n_tests_killed": len(ks),
            "is_dominator": is_dominator[(tid, mid)],
            "is_indistinguishable": is_indistinguishable[(tid, mid)],
        })

    summary = pd.DataFrame(rows)
    ms_path = out_dir / f"mutant_summary_{MODEL_NAME}.csv"
    summary.to_csv(ms_path, index=False)

    total_exec = len(summary)
    total_killed = int((summary["n_tests_killed"] > 0).sum())
    total_dom = int(summary["is_dominator"].sum())
    total_non_dom = int((~summary["is_dominator"]).sum())
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

    return {
        "dmr": dmr,
        "dmr_path": str(dmr_path),
        "kill_matrix_path": str(km_path),
        "mutant_summary_path": str(ms_path),
        "total_executable": total_exec,
        "total_killed": total_killed,
        "total_dominators": total_dom,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root", required=True,
        help="Dir containing <run-name>__<subject>__<function>__<target_id>/ subdirs "
             "(i.e. harness/executions/c/mull/execution/<run-name>/)",
    )
    parser.add_argument(
        "--out-dir", default="harness/executions/c/mull",
        help="Where to write mull_dmr_detail.csv / kill_matrix_long_mull.csv / mutant_summary_mull.csv",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compute_baseline_analysis(Path(args.run_root), Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
