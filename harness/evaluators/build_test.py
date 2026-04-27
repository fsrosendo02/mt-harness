from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target, Mutant, MutantResult, TestObservation


class BuildTestEvaluator:
    def __init__(self, adapter: BenchmarkAdapter):
        self.adapter = adapter

    def evaluate_mutant(
        self,
        subject: Subject,
        target: Target,
        mutant: Mutant,
        workdir: str,
        log_path: str,
        eligible_tests: list[str] | None = None,
    ) -> tuple[MutantResult, list[TestObservation]]:
        eligible_tests = list(eligible_tests or [])
        self.adapter.reset_subject(workdir)
        self.adapter.apply_mutant(workdir, target, mutant.code)

        build_ok, build_log = self.adapter.build(workdir)

        if not build_ok:
            Path(log_path).write_text(build_log, encoding="utf-8")
            observations = [
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=False,
                    outcome="NOT_RUN",
                    failure_type="BUILD_FAIL",
                    message="Mutant build failed before test execution",
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
                    build_status="FAIL",
                    test_status="NOT_RUN",
                    killed=False,
                    executable=False,
                    log_path=log_path,
                ),
                observations,
            )

        test_run = self.adapter.test_target(workdir, eligible_tests)
        test_ok = test_run.success
        test_log = test_run.log
        full_log = build_log + "\n\n=== TEST ===\n\n" + test_log
        Path(log_path).write_text(full_log, encoding="utf-8")
        observations = list(test_run.observations)
        killed = any(obs.outcome in {"FAIL", "ERROR"} for obs in observations) if observations else (not test_ok)
        return (
            MutantResult(
                dataset=subject.dataset,
                subject_id=subject.subject_id,
                function_name=target.function_name,
                mutant_id=mutant.mutant_id,
                build_status="SUCCESS",
                test_status="PASS" if not killed else "FAIL",
                killed=killed,
                executable=True,
                log_path=log_path,
            ),
            observations,
        )
