from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.evaluators.baseline import BaselineEvaluator
from harness.models import Subject, Target, Mutant, MutantResult


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
    ) -> MutantResult:
        self.adapter.checkout_subject(subject, workdir)

        baseline = self.baseline_evaluator.evaluate(subject, workdir)
        if not baseline.build_ok or not baseline.test_ok:
            full_log = (
                "=== BASELINE BUILD ===\n\n"
                + baseline.build_log
                + "\n\n=== BASELINE TEST ===\n\n"
                + baseline.test_log
            )
            Path(log_path).write_text(full_log, encoding="utf-8")

            return MutantResult(
                dataset=subject.dataset,
                subject_id=subject.subject_id,
                function_name=target.function_name,
                mutant_id=mutant.mutant_id,
                build_status="BASELINE_FAIL",
                test_status="BASELINE_FAIL",
                killed=False,
                executable=False,
                log_path=log_path,
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

            return MutantResult(
                dataset=subject.dataset,
                subject_id=subject.subject_id,
                function_name=target.function_name,
                mutant_id=mutant.mutant_id,
                build_status="FAIL",
                test_status="NOT_RUN",
                killed=False,
                executable=False,
                log_path=log_path,
            )

        test_ok, test_log = self.adapter.test(workdir)

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

        return MutantResult(
            dataset=subject.dataset,
            subject_id=subject.subject_id,
            function_name=target.function_name,
            mutant_id=mutant.mutant_id,
            build_status="SUCCESS",
            test_status="PASS" if test_ok else "FAIL",
            killed=not test_ok,
            executable=True,
            log_path=log_path,
        )
