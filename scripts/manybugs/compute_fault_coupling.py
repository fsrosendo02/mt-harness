#!/usr/bin/env python3
"""Compute catalog-scoped fault coupling for ManyBugs LLM and Mull runs.

Oracle tests are read from match_mode=oracle rows in the catalog-specific
target_tests.csv. Results are kept separate by tool, model, requested mutant
count, campaign, run, target, and mutant ID, so future Mull catalog campaigns
and repeated LLM conditions cannot collide.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.reporting.c_fault_coupling import build_fault_coupling


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="ManyBugs catalog JSON")
    parser.add_argument("--target-tests", help="Override catalog-specific target_tests.csv")
    parser.add_argument(
        "--llm-root", action="append", default=[],
        help="Root to scan recursively for LLM execution/results.csv (repeatable)",
    )
    parser.add_argument(
        "--mull-root", action="append", default=[],
        help="Root to scan recursively for Mull execution/results.csv (repeatable)",
    )
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_fault_coupling(
        catalog_file=Path(args.catalog),
        target_tests_file=Path(args.target_tests) if args.target_tests else None,
        llm_roots=[Path(path) for path in args.llm_root],
        mull_roots=[Path(path) for path in args.mull_root],
        output_dir=Path(args.output_dir),
    )
    audit = result["audit"]
    print(f"Catalog targets: {audit['n_catalog_targets']}")
    print(f"LLM runs: {audit['n_llm_runs']}")
    print(f"Mull runs: {audit['n_mull_runs']}")
    print(f"Executable mutants included: {audit['n_executable_mutants_included']}")
    print(f"Audit issues: {audit['n_issues']}")
    print(f"Outputs: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
