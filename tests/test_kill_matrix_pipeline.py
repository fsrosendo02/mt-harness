from __future__ import annotations

import csv
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.adapters.base import BenchmarkAdapter
from harness.models import Mutant, Subject, Target, TestObservation, TestRunResult
from harness.runners.mutation_runner import MutationRunner


class StructuredOutcomeAdapter(BenchmarkAdapter):
    def checkout_subject(self, subject: Subject, workdir: str) -> None:
        raise NotImplementedError

    def build(self, workdir: str) -> tuple[bool, str]:
        content = (Path(workdir) / "src" / "Example.java").read_text(encoding="utf-8")
        if "BUILD_FAIL" in content:
            return False, "synthetic build failure"
        return True, "synthetic build success"

    def test(
        self, workdir: str, eligible_tests: list[str] | None = None,
    ) -> tuple[bool, str]:
        test_run = self.test_target(workdir, eligible_tests or [])
        return test_run.success, test_run.log

    def test_target(self, workdir: str, eligible_tests: list[str]) -> TestRunResult:
        content = (Path(workdir) / "src" / "Example.java").read_text(encoding="utf-8")

        if "MUTANT_FAIL" in content:
            outcomes = {
                eligible_tests[0]: ("PASS", None, None, True),
                eligible_tests[1]: ("FAIL", "ASSERTION", "assertion failed", True),
                eligible_tests[2]: ("SKIPPED", "ASSUMPTION", "assumption not met", True),
            }
        elif "MUTANT_ERROR" in content:
            outcomes = {
                eligible_tests[0]: ("PASS", None, None, True),
                eligible_tests[1]: ("ERROR", "EXCEPTION", "uncaught exception", True),
                eligible_tests[2]: ("PASS", None, None, True),
            }
        else:
            outcomes = {
                test_name: ("PASS", None, None, True)
                for test_name in eligible_tests
            }

        observations = []
        for index, test_name in enumerate(eligible_tests, start=1):
            outcome, failure_type, message, executed = outcomes[test_name]
            observations.append(
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=executed,
                    outcome=outcome,
                    duration_ms=index * 10,
                    failure_type=failure_type,
                    message=message,
                    execution_index=index,
                )
            )

        success = all(obs.outcome == "PASS" for obs in observations)
        return TestRunResult(success=success, log="synthetic test log", observations=observations)

    def apply_mutant(self, workdir: str, target: Target, mutant_code: str) -> None:
        path = Path(workdir) / target.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mutant_code, encoding="utf-8")

    def reset_subject(self, workdir: str) -> None:
        path = Path(workdir) / "src" / "Example.java"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("BASELINE\nLINE2\nLINE3\n", encoding="utf-8")


class KillMatrixPipelineTests(unittest.TestCase):
    def test_runner_fails_fast_without_target_test_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            run_dir = tmp / "runs" / "run_missing_target_tests"
            base_snapshot_dir = tmp / "base_snapshot"
            workdir_base = str(tmp / "worker")

            source_path = base_snapshot_dir / "src" / "Example.java"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("BASELINE\nLINE2\nLINE3\n", encoding="utf-8")

            subject = Subject(dataset="defects4j", subject_id="Lang_1", language="java", version="f")
            target = Target(
                file_path="src/Example.java",
                function_name="example",
                start_line=1,
                end_line=3,
                target_id="lang_1f_example__line1_3",
            )
            mutants = [
                Mutant(mutant_id="m_pass", code="MUTANT_PASS\nLINE2\nLINE3\n", source="manual"),
            ]

            runner = MutationRunner(StructuredOutcomeAdapter())
            with patch("harness.runners.mutation_runner.load_target_test_map", return_value={}):
                with self.assertRaisesRegex(ValueError, "Strict target-test validation failed"):
                    runner.run(
                        subject=subject,
                        target=target,
                        mutants=mutants,
                        run_dir=run_dir,
                        workdir_base=workdir_base,
                        base_snapshot_dir=base_snapshot_dir,
                        run_mode="fresh",
                        extra_metadata={"experiment_name": "test"},
                        cleanup_tmp=False,
                        validate_after_run=False,
                        rebuild_index=False,
                        mutant_workers=1,
                    )

    def test_structured_test_results_drive_kill_matrix(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runs_root = tmp / "runs"
            run_dir = runs_root / "run_structured"
            base_snapshot_dir = tmp / "base_snapshot"
            workdir_base = str(tmp / "worker")
            output_dir = tmp / "kill_matrices"

            source_path = base_snapshot_dir / "src" / "Example.java"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("BASELINE\nLINE2\nLINE3\n", encoding="utf-8")

            subject = Subject(dataset="defects4j", subject_id="Lang_1", language="java", version="f")
            target = Target(
                file_path="src/Example.java",
                function_name="example",
                start_line=1,
                end_line=3,
                target_id="lang_1f_example__line1_3",
            )
            mutants = [
                Mutant(mutant_id="m_pass", code="MUTANT_PASS\nLINE2\nLINE3\n", source="manual"),
                Mutant(mutant_id="m_fail", code="MUTANT_FAIL\nLINE2\nLINE3\n", source="manual"),
                Mutant(mutant_id="m_error", code="MUTANT_ERROR\nLINE2\nLINE3\n", source="manual"),
                Mutant(mutant_id="m_build_fail", code="BUILD_FAIL\nLINE2\nLINE3\n", source="manual"),
            ]
            eligible_tests = ["TestAlpha::testOne", "TestBeta::testTwo", "TestGamma::testThree"]

            runner = MutationRunner(StructuredOutcomeAdapter())
            with patch(
                "harness.runners.mutation_runner.load_target_test_map",
                return_value={
                    (subject.dataset, subject.subject_id, target.target_id): eligible_tests,
                },
            ):
                runner.run(
                    subject=subject,
                    target=target,
                    mutants=mutants,
                    run_dir=run_dir,
                    workdir_base=workdir_base,
                    base_snapshot_dir=base_snapshot_dir,
                    run_mode="fresh",
                    extra_metadata={"experiment_name": "test"},
                    cleanup_tmp=False,
                    validate_after_run=False,
                    rebuild_index=False,
                    mutant_workers=2,
                )

            test_results_path = run_dir / "execution" / "test_results.csv"
            results_path = run_dir / "execution" / "results.csv"
            self.assertTrue(test_results_path.exists())
            self.assertTrue(results_path.exists())

            with test_results_path.open("r", encoding="utf-8", newline="") as f:
                test_rows = list(csv.DictReader(f))
            self.assertEqual(len(test_rows), len(mutants) * len(eligible_tests))

            by_mutant = {}
            for row in test_rows:
                by_mutant.setdefault(row["mutant_id"], []).append(row)

            for mutant in mutants:
                rows = by_mutant[mutant.mutant_id]
                self.assertEqual(
                    sorted(row["test_name"] for row in rows),
                    sorted(eligible_tests),
                )
                self.assertTrue(all(row["eligible"] == "True" for row in rows))

            observed_outcomes = {row["outcome"] for row in test_rows}
            self.assertEqual(
                observed_outcomes,
                {"PASS", "FAIL", "ERROR", "SKIPPED", "NOT_RUN"},
            )

            build_fail_rows = by_mutant["m_build_fail"]
            self.assertTrue(all(row["outcome"] == "NOT_RUN" for row in build_fail_rows))
            self.assertTrue(all(row["executed"] == "False" for row in build_fail_rows))

            worker_ids = {row["worker_id"] for row in test_rows}
            self.assertGreaterEqual(len(worker_ids - {""}), 2)

            with results_path.open("r", encoding="utf-8", newline="") as f:
                result_rows = {row["mutant_id"]: row for row in csv.DictReader(f)}

            for mutant_id, rows in by_mutant.items():
                derived_killed = any(row["outcome"] in {"FAIL", "ERROR"} for row in rows)
                self.assertEqual(result_rows[mutant_id]["killed"], str(derived_killed))

            subprocess.run(
                [
                    "python3",
                    "-m",
                    "harness.reporting.kill_matrix",
                    "--runs-dir",
                    str(runs_root),
                    "--output-dir",
                    str(output_dir),
                    "--group-by",
                    "run",
                ],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            long_matrix = output_dir / "run_structured_kill_matrix_long.csv"
            summary_csv = output_dir / "kill_matrix_summary.csv"
            self.assertTrue(long_matrix.exists())
            self.assertTrue(summary_csv.exists())

            with long_matrix.open("r", encoding="utf-8", newline="") as f:
                matrix_rows = list(csv.DictReader(f))
            self.assertEqual(len(matrix_rows), len(test_rows))
            self.assertEqual({row["outcome"] for row in matrix_rows}, observed_outcomes)

            matrix_build_fail = [row for row in matrix_rows if row["mutant_id"] == "m_build_fail"]
            self.assertTrue(all(row["outcome"] == "NOT_RUN" for row in matrix_build_fail))
            self.assertEqual(
                {row["killed"] for row in matrix_rows if row["mutant_id"] == "m_fail"},
                {"True"},
            )
            self.assertEqual(
                {row["killed"] for row in matrix_rows if row["mutant_id"] == "m_build_fail"},
                {"False"},
            )

            with summary_csv.open("r", encoding="utf-8", newline="") as f:
                summary_rows = list(csv.DictReader(f))
            self.assertEqual(summary_rows[0]["source_mode"], "test_results.csv")


if __name__ == "__main__":
    unittest.main()
