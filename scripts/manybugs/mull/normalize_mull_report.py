#!/usr/bin/env python3
"""Fase 3 of the mull baseline pipeline (see mull_baseline_plan.md).

Reads the per-test_id SQLite reports produced by run_mull_subject.py
(Fase 2), filters mutants to the target's [start_line, end_line], and
writes the exact same results.csv/test_results.csv schema the native
MutationRunner already produces for LLM mutants (reusing
harness.models.MutantResult/TestObservation and
harness.storage.results/test_results directly — no new schema invented,
per the plan's decision to keep the mull pipeline offline but
output-compatible).

mull's SQLite `mutant.status` column is a per-(mutant, test) execution
status, not a mutant-level verdict. Confirmed against mull 0.18.0 source
(include/mull/ExecutionResult.h — not documented in the rendered docs):

    0 Invalid   1 Failed   2 Passed   3 Timedout   4 Crashed
    5 AbnormalExit   6 DryRun   7 FailFast   8 NotCovered

Failed/Timedout/Crashed/AbnormalExit all count as that test having killed
the mutant (Timedout counting as a kill matches the existing convention
for Major in this harness). NotCovered means that particular test never
exercised the mutant — it must not be conflated with "survived": a mutant
NotCovered by test A can still be killed by test B.
"""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness.models import MutantResult, TestObservation
from harness.storage.layout import (
    execution_results_path,
    execution_test_results_path,
    execution_summary_path,
)
from harness.storage.results import append_result_csv
from harness.storage.test_results import append_test_results_csv
from harness.reporting.summary import summarize_results_csv

STATUS_NAMES = {
    0: "Invalid", 1: "Failed", 2: "Passed", 3: "Timedout",
    4: "Crashed", 5: "AbnormalExit", 6: "DryRun", 7: "FailFast", 8: "NotCovered",
}
# Statuses where this test is considered to have killed the mutant.
KILLING_STATUSES = {1, 3, 4, 5}  # Failed, Timedout, Crashed, AbnormalExit
NOT_RUN_STATUSES = {6, 7, 8}  # DryRun, FailFast, NotCovered


@dataclass
class MullMutant:
    mutant_id: str
    mutator: str
    filename: str
    line_number: int
    column_number: int
    # test_id -> (status, duration_ms)
    per_test: dict


def load_test_reports(report_dir: Path, target_id: str, eligible_tests: list[str]) -> dict[str, Path]:
    paths = {}
    for test_id in eligible_tests:
        p = report_dir / f"{target_id}__{test_id}.sqlite"
        if not p.exists():
            raise FileNotFoundError(f"Missing mull SQLite report for test_id={test_id}: {p}")
        paths[test_id] = p
    return paths


def aggregate_mutants(
    test_reports: dict[str, Path],
    *,
    target_file: str,
    start_line: int,
    end_line: int,
) -> dict[str, MullMutant]:
    mutants: dict[str, MullMutant] = {}
    for test_id, sqlite_path in test_reports.items():
        con = sqlite3.connect(str(sqlite_path))
        try:
            rows = con.execute(
                "SELECT mutant_id, mutator, filename, line_number, column_number, status, duration "
                "FROM mutant WHERE filename LIKE ? AND line_number BETWEEN ? AND ?",
                (f"%{target_file}", start_line, end_line),
            ).fetchall()
        finally:
            con.close()

        for mutant_id, mutator, filename, line_number, column_number, status, duration in rows:
            m = mutants.get(mutant_id)
            if m is None:
                m = MullMutant(
                    mutant_id=mutant_id, mutator=mutator, filename=filename,
                    line_number=line_number, column_number=column_number, per_test={},
                )
                mutants[mutant_id] = m
            m.per_test[test_id] = (status, duration)
    return mutants


def normalize_mull_report(
    *,
    report_dir: Path,
    dataset: str,
    subject_id: str,
    target_id: str,
    function_name: str,
    target_file: str,
    start_line: int,
    end_line: int,
    eligible_tests: list[str],
    run_name: str,
    run_dir: Path,
) -> dict:
    test_reports = load_test_reports(report_dir, target_id, eligible_tests)
    mutants = aggregate_mutants(
        test_reports, target_file=target_file, start_line=start_line, end_line=end_line
    )

    results_csv = execution_results_path(run_dir)
    test_results_csv = execution_test_results_path(run_dir)
    results_csv.parent.mkdir(parents=True, exist_ok=True)

    for mutant_id, m in sorted(mutants.items()):
        killed = any(status in KILLING_STATUSES for status, _ in m.per_test.values())

        result = MutantResult(
            dataset=dataset,
            subject_id=subject_id,
            function_name=function_name,
            mutant_id=mutant_id,
            build_status="SUCCESS",
            test_status="FAIL" if killed else "PASS",
            killed=killed,
            executable=True,
            log_path="",
            target_id=target_id,
            run_name=run_name,
            mutant_hash="",
        )

        observations = []
        for i, test_id in enumerate(eligible_tests, 1):
            status, duration = m.per_test.get(test_id, (8, None))  # default: NotCovered
            outcome = "PASS" if status == 2 else ("NOT_RUN" if status in NOT_RUN_STATUSES else "FAIL")
            failure_type = None if outcome != "FAIL" else STATUS_NAMES.get(status, "Unknown").upper()
            observations.append(
                TestObservation(
                    test_name=test_id,
                    eligible=True,
                    executed=status not in NOT_RUN_STATUSES,
                    outcome=outcome,
                    duration_ms=duration,
                    failure_type=failure_type,
                    message=f"mull status={STATUS_NAMES.get(status, status)} "
                            f"mutator={m.mutator} line={m.line_number}:{m.column_number}",
                    execution_index=i,
                )
            )

        append_result_csv(str(results_csv), result)
        append_test_results_csv(test_results_csv, result, observations)

    summary = summarize_results_csv(
        csv_path=results_csv,
        keep_duplicates=False,
        json_out=execution_summary_path(run_dir),
        print_to_stdout=False,
    )
    return {
        "results_csv": str(results_csv),
        "test_results_csv": str(test_results_csv),
        "summary_json": str(execution_summary_path(run_dir)),
        "total_mutants": len(mutants),
        "summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", required=True, help="Dir with <target_id>__<test_id>.sqlite from Fase 2")
    parser.add_argument("--dataset", default="manybugs")
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--target-file", required=True)
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--end-line", type=int, required=True)
    parser.add_argument("--eligible-tests", nargs="+", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--run-dir", required=True, help="Output dir; writes <run-dir>/execution/{results,test_results}.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = normalize_mull_report(
        report_dir=Path(args.report_dir),
        dataset=args.dataset,
        subject_id=args.subject_id,
        target_id=args.target_id,
        function_name=args.function_name,
        target_file=args.target_file,
        start_line=args.start_line,
        end_line=args.end_line,
        eligible_tests=args.eligible_tests,
        run_name=args.run_name,
        run_dir=Path(args.run_dir),
    )
    print(f"Wrote {result['total_mutants']} mutants to {result['results_csv']}")
    print(f"Test observations: {result['test_results_csv']}")
    print(f"Summary: {result['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
