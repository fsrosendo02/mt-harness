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
from harness.storage.layout import execution_dir, execution_results_path, execution_summary_path
from harness.storage.results import append_result_csv
from harness.storage.run_state import (
    load_completed_mutant_ids,
    prepare_run_dir,
    write_run_manifest,
)
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
    ):
        total_start = time.time()
        created_tmp_paths: list[str] = []
        cleanup_targets: list[str] = []

        if prepare_run_dir_on_start:
            run_path = prepare_run_dir(run_dir, mode=run_mode)
        else:
            run_path = Path(run_dir)
            run_path.mkdir(parents=True, exist_ok=True)
        run_name = run_path.name
        execution_path = execution_dir(run_path)
        execution_path.mkdir(parents=True, exist_ok=True)
        csv_path = execution_results_path(run_path)

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
            for mutant in mutants:
                if mutant.mutant_id in completed_mutant_ids:
                    log(f"[mutant {mutant.mutant_id}] skip already completed")
                    continue

                mutant_start = time.time()
                log(f"[mutant {mutant.mutant_id}] start")

                workdir = f"{workdir_base}_{mutant.mutant_id}"
                log_path = str(execution_path / f"{mutant.mutant_id}.log")

                workdir_path = Path(workdir)
                if workdir_path.exists():
                    t = time.time()
                    shutil.rmtree(workdir_path)
                    log_duration(f"[mutant {mutant.mutant_id}] remove existing workdir", t)

                t = time.time()
                shutil.copytree(base_snapshot_path, workdir_path)
                created_tmp_paths.append(workdir)
                log_duration(f"[mutant {mutant.mutant_id}] copy base snapshot", t)

                eval_start = time.time()
                result = self.evaluator.evaluate(
                    subject=subject,
                    target=target,
                    mutant=mutant,
                    workdir=workdir,
                    log_path=log_path,
                    baseline=baseline,
                )
                result.target_id = getattr(target, "target_id", None)
                result.run_name = run_name
                result.mutant_hash = compute_mutant_hash(mutant.code)
                log_duration(f"[mutant {mutant.mutant_id}] evaluate", eval_start)

                t = time.time()
                append_result_csv(csv_path, result)
                log_duration(f"[mutant {mutant.mutant_id}] append CSV", t)

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

                log_duration(f"[mutant {mutant.mutant_id}] total", mutant_start)

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
