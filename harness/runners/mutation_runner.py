import shutil
import time
from datetime import datetime
from pathlib import Path

from harness.evaluators.mutant import MutantEvaluator
from harness.experiments.build_experiment_index import build_experiment_index
from harness.reporting.summary import summarize_results_csv
from harness.reporting.validation import validate_run_dir
from harness.storage.artifacts import save_mutant_artifacts
from harness.storage.cleanup import cleanup_paths
from harness.storage.results import append_result_csv
from harness.storage.run_state import (
    load_completed_mutant_ids,
    prepare_run_dir,
    write_run_manifest,
)
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
    ):
        total_start = time.time()

        run_path = prepare_run_dir(run_dir, mode=run_mode)
        csv_path = run_path / "results.csv"

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

        created_tmp_paths = []

        for mutant in mutants:
            if mutant.mutant_id in completed_mutant_ids:
                log(f"Skipping already completed mutant {mutant.mutant_id}")
                continue

            mutant_start = time.time()
            log(f"Running mutant {mutant.mutant_id}")

            workdir = f"{workdir_base}_{mutant.mutant_id}"
            log_path = str(run_path / f"{mutant.mutant_id}.log")

            workdir_path = Path(workdir)
            if workdir_path.exists():
                t = time.time()
                shutil.rmtree(workdir_path)
                log_duration(f"Remove existing workdir for {mutant.mutant_id}", t)

            t = time.time()
            shutil.copytree(base_snapshot_path, workdir_path)
            log_duration(f"Copy base snapshot for {mutant.mutant_id}", t)

            eval_start = time.time()
            result = self.evaluator.evaluate(
                subject=subject,
                target=target,
                mutant=mutant,
                workdir=workdir,
                log_path=log_path,
            )
            log_duration(f"Evaluate {mutant.mutant_id}", eval_start)

            t = time.time()
            append_result_csv(csv_path, result)
            log_duration(f"Append CSV for {mutant.mutant_id}", t)

            t = time.time()
            save_mutant_artifacts(
                run_dir=run_path,
                subject=subject,
                target=target,
                mutant=mutant,
                result=result,
                original_code=original_code,
            )
            log_duration(f"Save artifacts for {mutant.mutant_id}", t)

            created_tmp_paths.append(workdir)
            log_duration(f"Mutant {mutant.mutant_id}", mutant_start)

        if csv_path.exists():
            t = time.time()
            summarize_results_csv(
                csv_path=csv_path,
                keep_duplicates=False,
                json_out=run_path / "summary.json",
                print_to_stdout=True,
            )
            log_duration("Summarize results CSV", t)

        if validate_after_run:
            t = time.time()
            validation_result = validate_run_dir(run_path)
            log(f"Run validation passed: {validation_result}")
            log_duration("Validate run dir", t)

        if cleanup_tmp:
            t = time.time()
            cleanup_targets = list(created_tmp_paths)
            cleanup_targets.append(base_snapshot_dir)
            cleanup_paths(cleanup_targets, print_to_stdout=True)
            log_duration("Cleanup tmp paths", t)

        if rebuild_index:
            t = time.time()
            index_path = build_experiment_index(print_to_stdout=True)
            log(f"Experiment index updated: {index_path}")
            log_duration("Rebuild experiment index", t)

        log_duration("MutationRunner total", total_start)