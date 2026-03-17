from pathlib import Path

from harness.evaluators.build_test import BuildTestEvaluator
from harness.models import Subject, Target
from harness.storage.results import append_result_csv



class ExperimentRunner:
    def __init__(self, adapter, generator):
        self.adapter = adapter
        self.generator = generator
        self.evaluator = BuildTestEvaluator(adapter)

    def run(
        self,
        subject: Subject,
        target: Target,
        num_mutants: int,
        workdir: str,
        run_dir: str,
    ) -> None:
        Path(run_dir).mkdir(parents=True, exist_ok=True)

        mutants = self.generator.generate(subject, target, num_mutants)
        csv_path = str(Path(run_dir) / "results.csv")

        for mutant in mutants:
            log_path = str(Path(run_dir) / f"{mutant.mutant_id}.log")

            result = self.evaluator.evaluate_mutant(
                subject=subject,
                target=target,
                mutant=mutant,
                workdir=workdir,
                log_path=log_path,
            )

            append_result_csv(csv_path, result)
