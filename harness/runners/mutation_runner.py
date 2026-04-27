import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from harness.evaluators.mutant import MutantEvaluator
from harness.reporting.experiment_index import build_experiment_index
from harness.reporting.summary import summarize_results_csv
from harness.reporting.validation import validate_run_dir
from harness.storage.artifacts import save_mutant_artifacts
from harness.storage.cleanup import cleanup_paths
from harness.storage.layout import (
    execution_dir,
    execution_results_path,
    execution_summary_path,
    execution_test_results_path,
)
from harness.storage.results import append_result_csv
from harness.storage.test_results import append_test_results_csv
from harness.storage.run_state import (
    load_completed_mutant_ids,
    prepare_run_dir,
    write_run_manifest,
)
from harness.models import MutantResult, TestObservation
from harness.targets.target_tests import load_target_test_map, target_tests_for
from harness.utils.mutant_identity import compute_mutant_hash
from harness.utils.source import extract_target_code


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def log_duration(label: str, start: float) -> None:
    log(f"{label} finished in {time.time() - start:.2f}s")


class MutationRunner:
    def __init__(self, adapter):
        self.adapter = adapter
        self.evaluator = MutantEvaluator(adapter)

    def _require_eligible_tests(self, *, subject, target, eligible_tests: list[str]) -> None:
        if eligible_tests:
            return

        target_ref = getattr(target, "target_id", None) or target.function_name or target.file_path
        raise ValueError(
            "Strict target-test validation failed: "
            f"no eligible tests found for target '{target_ref}' "
            f"(dataset={subject.dataset}, subject={subject.subject_id}). "
            "Populate the per-catalog target_tests.csv coverage mapping for this target "
            "before running mutation execution."
        )

    def _baseline_fail_result(
        self,
        subject,
        target,
        mutant,
        log_path: str,
        baseline,
        *,
        eligible_tests: list[str],
        worker_id: int,
    ) -> tuple[MutantResult, list[TestObservation]]:
        full_log = (
            "=== BASELINE BUILD ===\n\n"
            + baseline.build_log
            + "\n\n=== BASELINE TEST ===\n\n"
            + baseline.test_log
        )
        Path(log_path).write_text(full_log, encoding="utf-8")

        observations = [
            TestObservation(
                test_name=test_name,
                eligible=True,
                executed=False,
                outcome="NOT_RUN",
                duration_ms=None,
                failure_type="BASELINE_FAIL",
                message="Baseline build/test failed before mutant execution",
                worker_id=worker_id,
                execution_index=index,
            )
            for index, test_name in enumerate(eligible_tests, start=1)
        ]

        return (
            MutantResult(
                dataset=subject.dataset,
                subject_id=subject.subject_id,
                function_name=target.function_name,
                mutant_id=mutant.mutant_id,
                build_status="BASELINE_FAIL",
                test_status="BASELINE_FAIL",
                killed=False,
                executable=False,
                log_path=log_path,
                target_id=getattr(target, "target_id", None),
                worker_id=worker_id,
            ),
            observations,
        )

    def _evaluate_mutant_in_workdir(
        self,
        *,
        worker_id: int,
        subject,
        target,
        mutant,
        baseline,
        base_snapshot_path: Path,
        workdir_path: Path,
        log_path: str,
        run_name: str,
        eligible_tests: list[str],
    ):
        mutant_start = time.time()
        worker_label = f"worker {worker_id:02d}"
        log(f"[{worker_label}] [mutant {mutant.mutant_id}] start in {workdir_path}")

        if not baseline.build_ok or not baseline.test_ok:
            result, observations = self._baseline_fail_result(
                subject,
                target,
                mutant,
                log_path,
                baseline,
                eligible_tests=eligible_tests,
                worker_id=worker_id,
            )
            result.run_name = run_name
            result.mutant_hash = compute_mutant_hash(mutant.code)
            log_duration(
                f"[{worker_label}] [mutant {mutant.mutant_id}] baseline-fail result",
                mutant_start,
            )
            return mutant, result, observations

        if workdir_path.exists():
            t = time.time()
            log(f"[{worker_label}] resetting {workdir_path} before mutant {mutant.mutant_id}")
            shutil.rmtree(workdir_path)
            log_duration(
                f"[{worker_label}] [mutant {mutant.mutant_id}] remove existing worker workdir",
                t,
            )

        t = time.time()
        log(
            f"[{worker_label}] cloning base snapshot into {workdir_path} "
            f"for mutant {mutant.mutant_id}"
        )
        shutil.copytree(base_snapshot_path, workdir_path)
        log_duration(
            f"[{worker_label}] [mutant {mutant.mutant_id}] copy base snapshot",
            t,
        )

        evaluator = MutantEvaluator(self.adapter)
        eval_start = time.time()
        log(f"[{worker_label}] evaluating mutant {mutant.mutant_id}")
        result, observations = evaluator.evaluate(
            subject=subject,
            target=target,
            mutant=mutant,
            workdir=str(workdir_path),
            log_path=log_path,
            baseline=baseline,
            eligible_tests=eligible_tests,
            worker_id=worker_id,
        )
        result.target_id = getattr(target, "target_id", None)
        result.run_name = run_name
        result.mutant_hash = compute_mutant_hash(mutant.code)
        log_duration(f"[{worker_label}] [mutant {mutant.mutant_id}] evaluate", eval_start)
        log(
            f"[{worker_label}] finished mutant {mutant.mutant_id} "
            f"with build={result.build_status} test={result.test_status}"
        )
        log_duration(f"[{worker_label}] [mutant {mutant.mutant_id}] total", mutant_start)

        return mutant, result, observations

    def _run_worker_chunk(
        self,
        *,
        worker_id: int,
        worker_mutants,
        subject,
        target,
        baseline,
        base_snapshot_path: Path,
        workdir_base: str,
        execution_path: Path,
        run_name: str,
        eligible_tests: list[str],
    ):
        worker_dir = Path(f"{workdir_base}_worker{worker_id:02d}")
        results = []
        worker_label = f"worker {worker_id:02d}"
        assigned_mutants = ", ".join(mutant.mutant_id for mutant in worker_mutants)

        log(
            f"[{worker_label}] created with {len(worker_mutants)} mutant(s): "
            f"{assigned_mutants}"
        )
        log(f"[{worker_label}] using checkout directory {worker_dir}")

        try:
            for mutant in worker_mutants:
                log_path = str(execution_path / f"{mutant.mutant_id}.log")
                results.append(
                    self._evaluate_mutant_in_workdir(
                        worker_id=worker_id,
                        subject=subject,
                        target=target,
                        mutant=mutant,
                        baseline=baseline,
                        base_snapshot_path=base_snapshot_path,
                        workdir_path=worker_dir,
                        log_path=log_path,
                        run_name=run_name,
                        eligible_tests=eligible_tests,
                    )
                )
        finally:
            if worker_dir.exists():
                log(f"[{worker_label}] cleaning up {worker_dir}")
                shutil.rmtree(worker_dir)
            log(f"[{worker_label}] completed")

        return str(worker_dir), results

    def _persist_mutant_result(
        self,
        *,
        csv_path,
        test_results_csv_path,
        run_path,
        subject,
        target,
        original_code,
        mutant,
        result,
        observations,
    ):
        log(f"[run] persisting mutant {mutant.mutant_id} into {csv_path}")
        t = time.time()
        append_result_csv(csv_path, result)
        log_duration(f"[mutant {mutant.mutant_id}] append CSV", t)

        log(f"[run] persisting mutant {mutant.mutant_id} test observations into {test_results_csv_path}")
        t = time.time()
        append_test_results_csv(test_results_csv_path, result, observations)
        log_duration(f"[mutant {mutant.mutant_id}] append test-results CSV", t)

        t = time.time()
        save_mutant_artifacts(
            run_dir=run_path,
            subject=subject,
            target=target,
            mutant=mutant,
            result=result,
            original_code=original_code,
        )
        log_duration(f"[mutant {mutant.mutant_id}] save artifacts", t)

    def run(
        self,
        subject,
        target,
        mutants,
        run_dir,
        workdir_base,
        base_snapshot_dir,
        run_mode="fresh",
        extra_metadata=None,
        cleanup_tmp=True,
        validate_after_run=True,
        rebuild_index=True,
        prepare_run_dir_on_start=True,
        mutant_workers=1,
        target_tests_csv_path=None,
    ):
        total_start = time.time()
        created_tmp_paths: list[str] = []
        cleanup_targets: list[str] = []
        mutant_workers = max(1, int(mutant_workers or 1))

        if prepare_run_dir_on_start:
            run_path = prepare_run_dir(run_dir, mode=run_mode)
        else:
            run_path = Path(run_dir)
            run_path.mkdir(parents=True, exist_ok=True)
        run_name = run_path.name
        execution_path = execution_dir(run_path)
        execution_path.mkdir(parents=True, exist_ok=True)
        csv_path = execution_results_path(run_path)
        test_results_csv_path = execution_test_results_path(run_path)
        target_test_map = load_target_test_map(csv_path=target_tests_csv_path)
        eligible_tests = target_tests_for(subject, target, target_test_map)
        self._require_eligible_tests(
            subject=subject,
            target=target,
            eligible_tests=eligible_tests,
        )

        t = time.time()
        write_run_manifest(
            run_dir=run_path,
            subject=subject,
            target=target,
            mutants=mutants,
            run_mode=run_mode,
            workdir_base=workdir_base,
            extra_metadata=extra_metadata,
        )
        log_duration("Write run manifest", t)

        completed_mutant_ids = set()
        if run_mode == "resume":
            t = time.time()
            completed_mutant_ids = load_completed_mutant_ids(csv_path)
            log_duration("Load completed mutant ids", t)
            if completed_mutant_ids:
                log(
                    f"Resume mode: found {len(completed_mutant_ids)} already completed mutants "
                    f"in {csv_path}"
                )

        base_snapshot_path = Path(base_snapshot_dir)
        if not base_snapshot_path.exists():
            raise FileNotFoundError(
                f"Base snapshot directory not found: {base_snapshot_dir}"
            )

        t = time.time()
        original_code = extract_target_code(base_snapshot_dir, target)
        log_duration("Extract original target code", t)

        log("[baseline] running shared baseline once for all mutants")
        baseline_start = time.time()
        baseline = self.evaluator.baseline_evaluator.evaluate(subject, base_snapshot_dir)
        log_duration("Shared baseline evaluation", baseline_start)
        try:
            pending_mutants = []
            for mutant in mutants:
                if mutant.mutant_id in completed_mutant_ids:
                    log(f"[mutant {mutant.mutant_id}] skip already completed")
                    continue
                pending_mutants.append(mutant)

            if not pending_mutants:
                log("No pending mutants to execute")
            elif mutant_workers == 1:
                log("Running sequential execution with 1 worker")
                for mutant in pending_mutants:
                    workdir_path = Path(f"{workdir_base}_{mutant.mutant_id}")
                    created_tmp_paths.append(str(workdir_path))
                    log_path = str(execution_path / f"{mutant.mutant_id}.log")
                    mutant, result, observations = self._evaluate_mutant_in_workdir(
                        worker_id=1,
                        subject=subject,
                        target=target,
                        mutant=mutant,
                        baseline=baseline,
                        base_snapshot_path=base_snapshot_path,
                        workdir_path=workdir_path,
                        log_path=log_path,
                        run_name=run_name,
                        eligible_tests=eligible_tests,
                    )
                    self._persist_mutant_result(
                        csv_path=csv_path,
                        test_results_csv_path=test_results_csv_path,
                        run_path=run_path,
                        subject=subject,
                        target=target,
                        original_code=original_code,
                        mutant=mutant,
                        result=result,
                        observations=observations,
                    )
            else:
                worker_count = min(mutant_workers, len(pending_mutants))
                log(
                    f"Running {len(pending_mutants)} mutants with {worker_count} worker(s)"
                )
                chunks = [[] for _ in range(worker_count)]
                for index, mutant in enumerate(pending_mutants):
                    chunks[index % worker_count].append(mutant)

                for worker_id, worker_mutants in enumerate(chunks, start=1):
                    if not worker_mutants:
                        continue
                    log(
                        f"[scheduler] worker {worker_id:02d} assigned mutants: "
                        f"{', '.join(mutant.mutant_id for mutant in worker_mutants)}"
                    )

                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = [
                        executor.submit(
                            self._run_worker_chunk,
                            worker_id=worker_id,
                            worker_mutants=worker_mutants,
                            subject=subject,
                            target=target,
                            baseline=baseline,
                            base_snapshot_path=base_snapshot_path,
                            workdir_base=workdir_base,
                            execution_path=execution_path,
                            run_name=run_name,
                            eligible_tests=eligible_tests,
                        )
                        for worker_id, worker_mutants in enumerate(chunks, start=1)
                        if worker_mutants
                    ]

                    for future in as_completed(futures):
                        worker_dir, worker_results = future.result()
                        created_tmp_paths.append(worker_dir)
                        log(
                            f"[scheduler] worker results ready from {worker_dir}: "
                            f"{', '.join(mutant.mutant_id for mutant, _, _ in worker_results)}"
                        )
                        for mutant, result, observations in worker_results:
                            self._persist_mutant_result(
                                csv_path=csv_path,
                                test_results_csv_path=test_results_csv_path,
                                run_path=run_path,
                                subject=subject,
                                target=target,
                                original_code=original_code,
                                mutant=mutant,
                                result=result,
                                observations=observations,
                            )

            if csv_path.exists():
                t = time.time()
                summarize_results_csv(
                    csv_path=csv_path,
                    keep_duplicates=False,
                    json_out=execution_summary_path(run_path),
                    print_to_stdout=True,
                )
                log_duration("Summarize results CSV", t)

            if validate_after_run:
                t = time.time()
                validation_result = validate_run_dir(run_path)
                log(f"Run validation passed: {validation_result}")
                log_duration("Validate run dir", t)

            if rebuild_index:
                t = time.time()
                index_path = build_experiment_index(print_to_stdout=True)
                log(f"Experiment index updated: {index_path}")
                log_duration("Rebuild experiment index", t)

            log_duration("MutationRunner total", total_start)
        finally:
            if cleanup_tmp:
                cleanup_targets = list(created_tmp_paths)
                cleanup_targets.append(base_snapshot_dir)
                if cleanup_targets:
                    t = time.time()
                    cleanup_paths(cleanup_targets, print_to_stdout=True)
                    log_duration("Cleanup tmp paths", t)
