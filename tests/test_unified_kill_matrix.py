from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from harness.reporting.unified_kill_matrix import build_unified_exports, export_unified_kill_matrix


class UnifiedKillMatrixTests(unittest.TestCase):
    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_build_unified_exports_keeps_sparse_kills_and_executable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            run_name = "batch01__Lang_1__example__lang_1f_example_line1"
            index_path = tmp / "experiment_index.csv"
            runs_dir = tmp / "runs"

            self._write_csv(
                index_path,
                [
                    "run_name",
                    "run_status",
                    "mutant_source",
                    "target_id",
                    "model_name",
                    "eligible_test_count",
                    "killed_mutants",
                    "executable_mutants",
                ],
                [
                    {
                        "run_name": run_name,
                        "run_status": "ok",
                        "mutant_source": "llm",
                        "target_id": "lang_1f_example__line1_3",
                        "model_name": "qwen3:14b",
                        "eligible_test_count": 3,
                        "killed_mutants": 1,
                        "executable_mutants": 2,
                    },
                    {
                        "run_name": "ignored_failure_run",
                        "run_status": "failure",
                        "mutant_source": "llm",
                        "target_id": "ignored_target",
                        "model_name": "qwen3:14b",
                        "eligible_test_count": 0,
                        "killed_mutants": "",
                        "executable_mutants": "",
                    },
                ],
            )

            run_dir = runs_dir / run_name / "execution"
            self._write_csv(
                run_dir / "results.csv",
                [
                    "dataset",
                    "subject_id",
                    "target_id",
                    "run_name",
                    "function_name",
                    "mutant_id",
                    "mutant_hash",
                    "build_status",
                    "test_status",
                    "killed",
                    "executable",
                    "log_path",
                ],
                [
                    {
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "run_name": run_name,
                        "function_name": "example",
                        "mutant_id": "m1",
                        "mutant_hash": "h1",
                        "build_status": "SUCCESS",
                        "test_status": "FAIL",
                        "killed": True,
                        "executable": True,
                        "log_path": "m1.log",
                    },
                    {
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "run_name": run_name,
                        "function_name": "example",
                        "mutant_id": "m2",
                        "mutant_hash": "h2",
                        "build_status": "SUCCESS",
                        "test_status": "PASS",
                        "killed": False,
                        "executable": True,
                        "log_path": "m2.log",
                    },
                    {
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "run_name": run_name,
                        "function_name": "example",
                        "mutant_id": "m3",
                        "mutant_hash": "h3",
                        "build_status": "FAIL",
                        "test_status": "NOT_RUN",
                        "killed": False,
                        "executable": False,
                        "log_path": "m3.log",
                    },
                ],
            )
            self._write_csv(
                run_dir / "test_results.csv",
                [
                    "run_name",
                    "dataset",
                    "subject_id",
                    "target_id",
                    "mutant_id",
                    "mutant_hash",
                    "test_name",
                    "eligible",
                    "executed",
                    "outcome",
                    "duration_ms",
                    "failure_type",
                    "message",
                    "worker_id",
                    "execution_index",
                    "build_status",
                    "executable",
                    "log_path",
                ],
                [
                    {
                        "run_name": run_name,
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "mutant_id": "m1",
                        "mutant_hash": "h1",
                        "test_name": "TestAlpha::testOne",
                        "eligible": True,
                        "executed": True,
                        "outcome": "FAIL",
                        "duration_ms": 1,
                        "failure_type": "ASSERTION",
                        "message": "boom",
                        "worker_id": 1,
                        "execution_index": 1,
                        "build_status": "SUCCESS",
                        "executable": True,
                        "log_path": "m1.log",
                    },
                    {
                        "run_name": run_name,
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "mutant_id": "m1",
                        "mutant_hash": "h1",
                        "test_name": "TestBeta::testTwo",
                        "eligible": True,
                        "executed": True,
                        "outcome": "ERROR",
                        "duration_ms": 2,
                        "failure_type": "EXCEPTION",
                        "message": "err",
                        "worker_id": 1,
                        "execution_index": 2,
                        "build_status": "SUCCESS",
                        "executable": True,
                        "log_path": "m1.log",
                    },
                    {
                        "run_name": run_name,
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "mutant_id": "m1",
                        "mutant_hash": "h1",
                        "test_name": "TestBeta::testTwo",
                        "eligible": True,
                        "executed": True,
                        "outcome": "ERROR",
                        "duration_ms": 3,
                        "failure_type": "EXCEPTION",
                        "message": "duplicate row should collapse",
                        "worker_id": 1,
                        "execution_index": 3,
                        "build_status": "SUCCESS",
                        "executable": True,
                        "log_path": "m1.log",
                    },
                    {
                        "run_name": run_name,
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "mutant_id": "m2",
                        "mutant_hash": "h2",
                        "test_name": "TestAlpha::testOne",
                        "eligible": True,
                        "executed": True,
                        "outcome": "PASS",
                        "duration_ms": 1,
                        "failure_type": "",
                        "message": "",
                        "worker_id": 1,
                        "execution_index": 1,
                        "build_status": "SUCCESS",
                        "executable": True,
                        "log_path": "m2.log",
                    },
                    {
                        "run_name": run_name,
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "mutant_id": "m3",
                        "mutant_hash": "h3",
                        "test_name": "TestAlpha::testOne",
                        "eligible": True,
                        "executed": False,
                        "outcome": "NOT_RUN",
                        "duration_ms": "",
                        "failure_type": "BUILD_FAIL",
                        "message": "",
                        "worker_id": 1,
                        "execution_index": 1,
                        "build_status": "FAIL",
                        "executable": False,
                        "log_path": "m3.log",
                    },
                ],
            )

            long_rows, summary_rows, validation_errors = build_unified_exports(
                index_path=index_path,
                runs_base_dir=runs_dir,
            )

            self.assertEqual(validation_errors, [])
            self.assertEqual(
                long_rows,
                [
                    {
                        "target_id": "lang_1f_example__line1_3",
                        "model_name": "qwen3:14b",
                        "run_name": run_name,
                        "mutant_id": "m1",
                        "test_name": "TestAlpha::testOne",
                    },
                    {
                        "target_id": "lang_1f_example__line1_3",
                        "model_name": "qwen3:14b",
                        "run_name": run_name,
                        "mutant_id": "m1",
                        "test_name": "TestBeta::testTwo",
                    },
                ],
            )
            self.assertEqual(
                summary_rows,
                [
                    {
                        "target_id": "lang_1f_example__line1_3",
                        "model_name": "qwen3:14b",
                        "run_name": run_name,
                        "mutant_id": "m1",
                        "killed": 1,
                        "n_killing_tests": 2,
                        "eligible_test_count": "3",
                    },
                    {
                        "target_id": "lang_1f_example__line1_3",
                        "model_name": "qwen3:14b",
                        "run_name": run_name,
                        "mutant_id": "m2",
                        "killed": 0,
                        "n_killing_tests": 0,
                        "eligible_test_count": "3",
                    },
                ],
            )

    def test_export_raises_on_cross_validation_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            run_name = "batch01__Lang_1__example__lang_1f_example_line1"
            index_path = tmp / "experiment_index.csv"
            runs_dir = tmp / "runs"
            output_dir = tmp / "out"

            self._write_csv(
                index_path,
                [
                    "run_name",
                    "run_status",
                    "mutant_source",
                    "target_id",
                    "model_name",
                    "eligible_test_count",
                    "killed_mutants",
                    "executable_mutants",
                ],
                [
                    {
                        "run_name": run_name,
                        "run_status": "ok",
                        "mutant_source": "llm",
                        "target_id": "lang_1f_example__line1_3",
                        "model_name": "qwen3:14b",
                        "eligible_test_count": 1,
                        "killed_mutants": 1,
                        "executable_mutants": 2,
                    }
                ],
            )

            run_dir = runs_dir / run_name / "execution"
            self._write_csv(
                run_dir / "results.csv",
                [
                    "dataset",
                    "subject_id",
                    "target_id",
                    "run_name",
                    "function_name",
                    "mutant_id",
                    "mutant_hash",
                    "build_status",
                    "test_status",
                    "killed",
                    "executable",
                    "log_path",
                ],
                [
                    {
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "run_name": run_name,
                        "function_name": "example",
                        "mutant_id": "m1",
                        "mutant_hash": "h1",
                        "build_status": "SUCCESS",
                        "test_status": "PASS",
                        "killed": False,
                        "executable": True,
                        "log_path": "m1.log",
                    }
                ],
            )
            self._write_csv(
                run_dir / "test_results.csv",
                [
                    "run_name",
                    "dataset",
                    "subject_id",
                    "target_id",
                    "mutant_id",
                    "mutant_hash",
                    "test_name",
                    "eligible",
                    "executed",
                    "outcome",
                    "duration_ms",
                    "failure_type",
                    "message",
                    "worker_id",
                    "execution_index",
                    "build_status",
                    "executable",
                    "log_path",
                ],
                [
                    {
                        "run_name": run_name,
                        "dataset": "defects4j",
                        "subject_id": "Lang_1",
                        "target_id": "lang_1f_example__line1_3",
                        "mutant_id": "m1",
                        "mutant_hash": "h1",
                        "test_name": "TestAlpha::testOne",
                        "eligible": True,
                        "executed": True,
                        "outcome": "PASS",
                        "duration_ms": 1,
                        "failure_type": "",
                        "message": "",
                        "worker_id": 1,
                        "execution_index": 1,
                        "build_status": "SUCCESS",
                        "executable": True,
                        "log_path": "m1.log",
                    }
                ],
            )

            with self.assertRaisesRegex(ValueError, "validation failed"):
                export_unified_kill_matrix(
                    index_path=index_path,
                    output_dir=output_dir,
                    runs_base_dir=runs_dir,
                )


if __name__ == "__main__":
    unittest.main()
