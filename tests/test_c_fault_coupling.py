from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from harness.reporting.c_fault_coupling import build_fault_coupling


RESULT_FIELDS = [
    "dataset", "subject_id", "target_id", "run_name", "function_name",
    "mutant_id", "mutant_hash", "build_status", "test_status", "killed",
    "executable", "log_path",
]
TEST_FIELDS = [
    "run_name", "dataset", "subject_id", "target_id", "mutant_id",
    "mutant_hash", "test_name", "eligible", "executed", "outcome",
    "duration_ms", "failure_type", "message", "worker_id", "execution_index",
    "build_status", "executable", "log_path",
]


class CFaultCouplingTests(unittest.TestCase):
    def write_csv(self, path: Path, fields: list[str], rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def write_run(
        self,
        root: Path,
        name: str,
        *,
        target_id: str,
        subject_id: str,
        killing_tests: list[str],
        model: str | None = None,
        requested: int | None = None,
        omit_test: str | None = None,
    ) -> Path:
        run = root / name
        result = {
            "dataset": "manybugs", "subject_id": subject_id,
            "target_id": target_id, "run_name": name, "function_name": "f",
            "mutant_id": "m01", "mutant_hash": "hash", "build_status": "SUCCESS",
            "test_status": "FAIL" if killing_tests else "PASS",
            "killed": bool(killing_tests), "executable": True, "log_path": "",
        }
        tests = []
        for index, test_name in enumerate(["oracle", "broad"], 1):
            if test_name == omit_test:
                continue
            tests.append({
                "run_name": name, "dataset": "manybugs", "subject_id": subject_id,
                "target_id": target_id, "mutant_id": "m01", "mutant_hash": "hash",
                "test_name": test_name, "eligible": True, "executed": True,
                "outcome": "FAIL" if test_name in killing_tests else "PASS",
                "duration_ms": 1, "failure_type": "", "message": "", "worker_id": "",
                "execution_index": index, "build_status": "SUCCESS",
                "executable": True, "log_path": "",
            })
        self.write_csv(run / "execution/results.csv", RESULT_FIELDS, [result])
        self.write_csv(run / "execution/test_results.csv", TEST_FIELDS, tests)
        config = {"source_revision": "fixed"}
        if model is not None:
            config.update({
                "model": model, "num_mutants": requested,
                "batch_id": name.split("__")[0],
            })
        (run / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
        return run

    def fixture(self, tmp: Path) -> tuple[Path, Path]:
        catalog = tmp / "catalog.json"
        catalog.write_text(json.dumps({"targets": [
            {"target_id": "t1", "subject": "bug1", "function": "f"},
            {"target_id": "t2", "subject": "bug2", "function": "g"},
        ]}), encoding="utf-8")
        mappings = tmp / "target_tests.csv"
        self.write_csv(mappings, ["target_id", "test_name", "match_mode"], [
            {"target_id": "t1", "test_name": "oracle", "match_mode": "oracle"},
            {"target_id": "t1", "test_name": "broad", "match_mode": "broad"},
            {"target_id": "t2", "test_name": "oracle", "match_mode": "oracle"},
            {"target_id": "t2", "test_name": "broad", "match_mode": "broad"},
        ])
        return catalog, mappings

    def test_keeps_requested_counts_and_tools_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            catalog, mappings = self.fixture(tmp)
            llm = tmp / "llm"
            mull_campaign = tmp / "mull" / "campaign"
            self.write_run(
                llm, "batch8__run", target_id="t1", subject_id="bug1",
                killing_tests=["oracle"], model="model-a", requested=8,
            )
            self.write_run(
                llm, "batch16__run", target_id="t1", subject_id="bug1",
                killing_tests=["oracle", "broad"], model="model-a", requested=16,
            )
            self.write_run(
                mull_campaign, "mull-run", target_id="t1", subject_id="bug1",
                killing_tests=["broad"],
            )

            result = build_fault_coupling(
                catalog_file=catalog, target_tests_file=mappings,
                llm_roots=[llm], mull_roots=[tmp / "mull"], output_dir=tmp / "out",
            )

            self.assertEqual(3, len(result["mutant_rows"]))
            conditions = {
                (row["tool"], row["model"], row["num_mutants_requested"])
                for row in result["condition_rows"]
            }
            self.assertEqual({
                ("llm", "model-a", "8"), ("llm", "model-a", "16"),
                ("mull", "mull", ""),
            }, conditions)
            by_requested = {
                row["num_mutants_requested"]: row
                for row in result["mutant_rows"] if row["tool"] == "llm"
            }
            self.assertTrue(by_requested["8"]["coupled"])
            self.assertTrue(by_requested["8"]["exact_match"])
            self.assertTrue(by_requested["16"]["coupled"])
            self.assertFalse(by_requested["16"]["exact_match"])
            mull = next(row for row in result["mutant_rows"] if row["tool"] == "mull")
            self.assertFalse(mull["coupled"])

            campaign = next(
                row for row in result["campaign_rows"]
                if row["tool"] == "llm" and row["num_mutants_requested"] == "8"
            )
            self.assertEqual(2, campaign["n_targets_total"])
            self.assertEqual(1, campaign["n_targets_with_run"])
            self.assertEqual(0.5, campaign["rbdr_target_coupled"])

    def test_excludes_mutant_with_incomplete_test_vector_and_audits_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            catalog, mappings = self.fixture(tmp)
            llm = tmp / "llm"
            self.write_run(
                llm, "batch8__run", target_id="t1", subject_id="bug1",
                killing_tests=["oracle"], model="model-a", requested=8,
                omit_test="broad",
            )
            result = build_fault_coupling(
                catalog_file=catalog, target_tests_file=mappings,
                llm_roots=[llm], output_dir=tmp / "out",
            )
            self.assertEqual([], result["mutant_rows"])
            self.assertEqual(1, result["audit"]["n_issues"])
            self.assertEqual("incomplete_mutant_tests", result["audit"]["issues"][0]["kind"])

    def test_empty_run_is_present_using_manifest_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            catalog, mappings = self.fixture(tmp)
            run = tmp / "llm" / "batch8__empty"
            self.write_csv(run / "execution/results.csv", RESULT_FIELDS, [])
            self.write_csv(run / "execution/test_results.csv", TEST_FIELDS, [])
            (run / "run_manifest.json").write_text(json.dumps({
                "run_name": "batch8__empty",
                "subject": {"subject_id": "bug1"},
                "target": {"target_id": "t1"},
                "extra_metadata": {
                    "model_name": "model-a", "n_requested_mutants": 8,
                    "batch_id": "batch8",
                },
            }), encoding="utf-8")

            result = build_fault_coupling(
                catalog_file=catalog, target_tests_file=mappings,
                llm_roots=[tmp / "llm"], output_dir=tmp / "out",
            )

            self.assertEqual(1, result["audit"]["n_llm_runs"])
            self.assertEqual([], result["mutant_rows"])
            campaign = result["campaign_rows"][0]
            self.assertEqual("8", campaign["num_mutants_requested"])
            self.assertEqual(1, campaign["n_targets_with_run"])
            target = next(row for row in result["target_rows"] if row["target_id"] == "t1")
            self.assertTrue(target["run_present"])
            self.assertEqual(0, target["n_mutants_executable"])

    def test_requires_oracle_for_every_catalog_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            catalog, mappings = self.fixture(tmp)
            with mappings.open(newline="") as handle:
                read_rows = list(csv.DictReader(handle))
            self.write_csv(
                mappings, ["target_id", "test_name", "match_mode"],
                [row for row in read_rows if not (row["target_id"] == "t2" and row["match_mode"] == "oracle")],
            )
            with self.assertRaisesRegex(ValueError, "t2"):
                build_fault_coupling(
                    catalog_file=catalog, target_tests_file=mappings,
                    output_dir=tmp / "out",
                )

    def test_excludes_mull_run_without_fixed_revision_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            catalog, mappings = self.fixture(tmp)
            run = self.write_run(
                tmp / "mull" / "campaign", "old-run", target_id="t1",
                subject_id="bug1", killing_tests=["oracle"],
            )
            (run / "run_config.json").unlink()
            result = build_fault_coupling(
                catalog_file=catalog, target_tests_file=mappings,
                mull_roots=[tmp / "mull"], output_dir=tmp / "out",
            )
            self.assertEqual([], result["mutant_rows"])
            self.assertEqual(0, result["audit"]["n_mull_runs"])
            self.assertEqual(
                "unverified_mull_source_revision",
                result["audit"]["issues"][0]["kind"],
            )


if __name__ == "__main__":
    unittest.main()
