from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.evaluators.baseline import BaselineEvaluator
from harness.models import Subject, Target, Mutant, MutantResult, TestObservation


class MutantEvaluator:
    def __init__(self, adapter: BenchmarkAdapter):
        self.adapter = adapter
        self.baseline_evaluator = BaselineEvaluator(adapter)

    def evaluate(
        self,
        subject: Subject,
        target: Target,
        mutant: Mutant,
        workdir: str,
        log_path: str,
        baseline=None,
        eligible_tests: list[str] | None = None,
        worker_id: int | None = None,
    ) -> tuple[MutantResult, list[TestObservation]]:
        eligible_tests = list(eligible_tests or [])

        def not_run_observations(
            *,
            failure_type: str,
            message: str,
        ) -> list[TestObservation]:
            return [
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=False,
                    outcome="NOT_RUN",
                    duration_ms=None,
                    failure_type=failure_type,
                    message=message,
                    worker_id=worker_id,
                    execution_index=index,
                )
                for index, test_name in enumerate(eligible_tests, start=1)
            ]

        def killed_from(observations: list[TestObservation]) -> bool:
            return any(obs.outcome in {"FAIL", "ERROR"} for obs in observations)

        if baseline is None:
            baseline = self.baseline_evaluator.evaluate(subject, workdir, eligible_tests=eligible_tests or None)

        if not baseline.build_ok or not baseline.test_ok:
            full_log = (
                "=== BASELINE BUILD ===\n\n"
                + baseline.build_log
                + "\n\n=== BASELINE TEST ===\n\n"
                + baseline.test_log
            )
            Path(log_path).write_text(full_log, encoding="utf-8")

            observations = not_run_observations(
                failure_type="BASELINE_FAIL",
                message="Baseline build/test failed before mutant execution",
            )
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
                    worker_id=worker_id,
                ),
                observations,
            )

        self.adapter.apply_mutant(workdir, target, mutant.code)

        build_ok, build_log = self.adapter.build(workdir)

        if not build_ok:
            full_log = (
                "=== BASELINE BUILD ===\n\n"
                + baseline.build_log
                + "\n\n=== BASELINE TEST ===\n\n"
                + baseline.test_log
                + "\n\n=== MUTANT BUILD ===\n\n"
                + build_log
            )
            Path(log_path).write_text(full_log, encoding="utf-8")

            observations = not_run_observations(
                failure_type="BUILD_FAIL",
                message="Mutant build failed before test execution",
            )
            return (
                MutantResult(
                    dataset=subject.dataset,
                    subject_id=subject.subject_id,
                    function_name=target.function_name,
                    mutant_id=mutant.mutant_id,
                    build_status="FAIL",
                    test_status="NOT_RUN",
                    killed=False,
                    executable=False,
                    log_path=log_path,
                    worker_id=worker_id,
                ),
                observations,
            )

        test_run = self.adapter.test_target(workdir, eligible_tests)
        observations = []
        for index, observation in enumerate(test_run.observations, start=1):
            observations.append(
                TestObservation(
                    test_name=observation.test_name,
                    eligible=observation.eligible,
                    executed=observation.executed,
                    outcome=observation.outcome,
                    duration_ms=observation.duration_ms,
                    failure_type=observation.failure_type,
                    message=observation.message,
                    worker_id=worker_id if observation.worker_id is None else observation.worker_id,
                    execution_index=(
                        observation.execution_index
                        if observation.execution_index is not None
                        else index
                    ),
                )
            )
        test_ok = test_run.success
        test_log = test_run.log

        full_log = (
            "=== BASELINE BUILD ===\n\n"
            + baseline.build_log
            + "\n\n=== BASELINE TEST ===\n\n"
            + baseline.test_log
            + "\n\n=== MUTANT BUILD ===\n\n"
            + build_log
            + "\n\n=== MUTANT TEST ===\n\n"
            + test_log
        )
        Path(log_path).write_text(full_log, encoding="utf-8")

        killed = killed_from(observations) if observations else (not test_ok)
        test_status = "PASS" if (all(obs.outcome == "PASS" for obs in observations) if observations else test_ok) else "FAIL"

        return (
            MutantResult(
                dataset=subject.dataset,
                subject_id=subject.subject_id,
                function_name=target.function_name,
                mutant_id=mutant.mutant_id,
                build_status="SUCCESS",
                test_status=test_status,
                killed=killed,
                executable=True,
                log_path=log_path,
                worker_id=worker_id,
            ),
            observations,
        )
