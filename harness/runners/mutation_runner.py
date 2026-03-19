import shutil
from pathlib import Path

from harness.evaluators.mutant import MutantEvaluator
from harness.storage.results import append_result_csv
from harness.storage.artifacts import save_mutant_artifacts
from harness.storage.run_state import (
    prepare_run_dir,
    load_completed_mutant_ids,
    write_run_manifest,
)
from harness.storage.cleanup import cleanup_paths
from harness.utils.source import extract_target_code
from harness.reporting.summary import summarize_results_csv
from harness.reporting.validation import validate_run_dir
from harness.experiments.build_experiment_index import build_experiment_index


class MutationRunner:
    def __init__(self, adapter):
        self.adapter = adapter
        self.evaluator = MutantEvaluator(adapter)

    def run(
        self,
        subject,
        target,
        mutants,
        run_dir,
        workdir_base,
        run_mode="fresh",
        extra_metadata=None,
        cleanup_tmp=True,
        validate_after_run=True,
        rebuild_index=True,
    ):
        run_path = prepare_run_dir(run_dir, mode=run_mode)
        csv_path = run_path / "results.csv"

        write_run_manifest(
            run_dir=run_path,
            subject=subject,
            target=target,
            mutants=mutants,
            run_mode=run_mode,
            workdir_base=workdir_base,
            extra_metadata=extra_metadata,
        )

        completed_mutant_ids = set()
        if run_mode == "resume":
            completed_mutant_ids = load_completed_mutant_ids(csv_path)
            if completed_mutant_ids:
                print(
                    f"Resume mode: found {len(completed_mutant_ids)} already completed mutants "
                    f"in {csv_path}"
                )

        created_tmp_paths = []

        for mutant in mutants:
            if mutant.mutant_id in completed_mutant_ids:
                print(f"Skipping already completed mutant {mutant.mutant_id}")
                continue

            print(f"Running mutant {mutant.mutant_id}")

            snapshotdir = f"{workdir_base}_{mutant.mutant_id}_snapshot"
            workdir = f"{workdir_base}_{mutant.mutant_id}"
            log_path = str(run_path / f"{mutant.mutant_id}.log")

            self.adapter.checkout_subject(subject, snapshotdir)
            original_code = extract_target_code(snapshotdir, target)

            if Path(workdir).exists():
                shutil.rmtree(workdir)
            shutil.copytree(snapshotdir, workdir)

            result = self.evaluator.evaluate(
                subject=subject,
                target=target,
                mutant=mutant,
                workdir=workdir,
                log_path=log_path,
            )

            append_result_csv(csv_path, result)

            save_mutant_artifacts(
                run_dir=run_path,
                subject=subject,
                target=target,
                mutant=mutant,
                result=result,
                original_code=original_code,
            )

            created_tmp_paths.append(snapshotdir)
            created_tmp_paths.append(workdir)

        if csv_path.exists():
            summarize_results_csv(
                csv_path=csv_path,
                keep_duplicates=False,
                json_out=run_path / "summary.json",
                print_to_stdout=True,
            )

        if validate_after_run:
            validation_result = validate_run_dir(run_path)
            print(f"Run validation passed: {validation_result}")

        if cleanup_tmp:
            cleanup_paths(created_tmp_paths, print_to_stdout=True)

        if rebuild_index:
            index_path = build_experiment_index(print_to_stdout=True)
            print(f"Experiment index updated: {index_path}")
