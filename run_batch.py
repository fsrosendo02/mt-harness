import json
import os
import re
import signal
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from harness.models import Subject, Target
from harness.reporting.validation import validate_run_dir
from harness.storage.layout import (
    batches_root,
    execution_results_path,
    execution_summary_path,
    execution_test_results_path,
    llm_batch_manifest_path,
    llm_run_dir,
    manifest_path,
    runs_root,
)
from harness.storage.results import RESULT_FIELDNAMES
from harness.storage.test_results import TEST_RESULT_FIELDNAMES
from harness.storage.run_state import write_run_manifest
from harness.targets.catalog import load_catalog_entries


class TeeStream:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_json_path(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def slug(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w\-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def next_batch_id(batches_dir: Path) -> str:
    batches_dir.mkdir(parents=True, exist_ok=True)

    max_n = 0
    for path in batches_dir.glob("batch*.json"):
        m = re.match(r"batch(\d+)\.json$", path.name)
        if m:
            max_n = max(max_n, int(m.group(1)))

    return f"batch{max_n + 1:02d}"


def pipeline_mode_from_cfg(cfg: dict) -> str:
    return str(cfg.get("pipeline_mode", "full")).strip().lower()


def run_dir_for_cfg(cfg: dict, batch_id: str) -> Path:
    """Compute run directory respecting runs_subdir (mirrors run_llm.run_path_for_config)."""
    run_name = cfg["run_name"]
    subdir = cfg.get("runs_subdir", "").strip("/")
    if subdir:
        return runs_root() / subdir / run_name
    return llm_run_dir(run_name, batch_id)


def format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def save_batch_manifest(batch_manifest_path: Path, batch_manifest: dict) -> None:
    save_json(batch_manifest_path, batch_manifest)


def resolve_source_batch_manifest(base_cfg: dict, batches_dir: Path) -> Path:
    manifest_path_str = base_cfg.get("source_batch_manifest")
    source_batch_id = base_cfg.get("source_batch_id")

    if manifest_path_str:
        return Path(str(manifest_path_str))

    if source_batch_id:
        return llm_batch_manifest_path(str(source_batch_id))

    legacy_source_batch_id = base_cfg.get("source_batch_id")
    if legacy_source_batch_id:
        return batches_dir / f"{legacy_source_batch_id}.json"

    raise ValueError(
        "execute_only batch mode requires source_batch_id or source_batch_manifest"
    )


def build_execute_only_runs(source_manifest: dict, base_cfg: dict) -> list[dict]:
    runs = source_manifest.get("runs", [])
    if not isinstance(runs, list) or not runs:
        raise ValueError("Source batch manifest does not contain any runs")

    batch_id = str(source_manifest.get("batch_id") or base_cfg.get("source_batch_id") or "")
    if not batch_id:
        raise ValueError("Source batch manifest is missing batch_id")

    catalog_file = base_cfg.get("catalog_file") or source_manifest.get("catalog_file")
    built_runs = []
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError(f"Invalid run entry in source batch manifest: {run!r}")

        run_name = run.get("run_name")
        if not run_name:
            raise ValueError("Source batch manifest contains a run without run_name")
        if run.get("target_id") and not catalog_file:
            raise ValueError(
                "execute_only batch mode requires catalog_file when runs reference target_id; "
                "set it in the source batch manifest or the base config"
            )

        cfg = {
            "batch_id": batch_id,
            "run_name": run_name,
            "pipeline_mode": "execute_only",
            "run_mode": base_cfg.get("run_mode", "overwrite"),
            "mutant_workers": base_cfg.get("mutant_workers", 1),
            "missing_target_tests_policy": base_cfg.get(
                "missing_target_tests_policy", "fail"
            ),
            "cleanup_tmp": base_cfg.get("cleanup_tmp", True),
            "validate_after_run": base_cfg.get("validate_after_run", True),
            "rebuild_index": base_cfg.get("rebuild_index", True),
        }
        if catalog_file:
            cfg["catalog_file"] = catalog_file

        built_runs.append(
            {
                "target_id": run.get("target_id"),
                "subject": run.get("subject"),
                "function": run.get("function"),
                "run_group_id": run.get("run_group_id"),
                "run_index_for_target": run.get("run_index_for_target"),
                "runs_per_target": run.get("runs_per_target"),
                "run_name": run_name,
                "cfg": cfg,
            }
        )

    return built_runs


def build_generation_runs(catalog: list[dict], base_cfg: dict, batch_id: str) -> list[dict]:
    runs_per_target = int(base_cfg.get("runs_per_target", 1))
    built_runs = []

    for entry in catalog:
        target_id = entry["target_id"]
        subject = entry["subject"]
        function = entry["function"]
        run_group_id = f"{batch_id}__{slug(target_id)}"

        for run_index_for_target in range(1, runs_per_target + 1):
            run_name = f"{batch_id}__{slug(subject)}__{slug(function)}__{slug(target_id)}"
            if runs_per_target > 1:
                run_name = f"{run_name}__run{run_index_for_target:02d}"

            cfg = dict(base_cfg)

            cfg["catalog_file"] = base_cfg["catalog_file"]
            cfg["target_id"] = entry["target_id"]
            cfg["dataset"] = entry["dataset"]
            cfg["subject"] = entry["subject"]
            cfg["version"] = entry["version"]
            cfg["language"] = entry["language"]
            cfg["file"] = entry["file"]
            cfg["function"] = entry["function"]

            if "start_line" in entry:
                cfg["start_line"] = entry["start_line"]
            if "end_line" in entry:
                cfg["end_line"] = entry["end_line"]
            if "signature" in entry:
                cfg["signature"] = entry["signature"]

            cfg["batch_id"] = batch_id
            cfg["run_name"] = run_name
            cfg["run_group_id"] = run_group_id
            cfg["run_index_for_target"] = run_index_for_target
            cfg["runs_per_target"] = runs_per_target

            built_runs.append(
                {
                    "target_id": target_id,
                    "subject": subject,
                    "function": function,
                    "run_group_id": run_group_id,
                    "run_index_for_target": run_index_for_target,
                    "runs_per_target": runs_per_target,
                    "run_name": run_name,
                    "cfg": cfg,
                }
            )

    return built_runs


def get_descendants(root_pid: int) -> list[int]:
    """Return all descendant PIDs of root_pid."""
    try:
        result = subprocess.run(
            ["ps", "-e", "-o", "pid=", "-o", "ppid="],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []

    children_by_parent: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        children_by_parent.setdefault(ppid, []).append(pid)

    descendants = []
    stack = [root_pid]
    seen = set()

    while stack:
        parent = stack.pop()
        for child in children_by_parent.get(parent, []):
            if child not in seen:
                seen.add(child)
                descendants.append(child)
                stack.append(child)

    return descendants


def kill_process_tree(pid: int) -> None:
    """Best-effort kill of pid and all descendants."""
    descendants = get_descendants(pid)

    for child_pid in reversed(descendants):
        try:
            os.kill(child_pid, signal.SIGKILL)
        except Exception:
            pass

    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        pass

    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def write_empty_results_csv(run_dir: Path) -> None:
    csv_path = execution_results_path(run_dir)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(",".join(RESULT_FIELDNAMES) + "\n", encoding="utf-8")
    execution_test_results_path(run_dir).write_text(
        ",".join(TEST_RESULT_FIELDNAMES) + "\n",
        encoding="utf-8",
    )


def cleanup_killed_run_tmp_paths(cfg: dict) -> list[str]:
    run_name = cfg.get("run_name")
    if not run_name:
        return []

    removed = []
    tmp_root = Path("tmp")

    cleanup_targets = [tmp_root / f"base_{run_name}"]
    cleanup_targets.extend(sorted(tmp_root.glob(f"{run_name}_*")))

    for path in cleanup_targets:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))

    return removed


def write_empty_summary_json(
    run_dir: Path,
    *,
    run_status: str,
    failure_reason: str | None,
    failure_message: str | None,
) -> None:
    execution_fields = {
        "total_mutants": None,
        "build_successes": None,
        "executable_mutants": None,
        "killed_mutants": None,
        "survived_mutants": None,
        "baseline_failures": None,
        "build_success_rate": None,
        "executable_yield": None,
        "mutation_score": None,
    }

    payload = {
        "csv_path": str(execution_results_path(run_dir)),
        "json_path": str(execution_summary_path(run_dir)),
        "run_status": run_status,
        "failure_reason": failure_reason,
        "failure_message": failure_message,
        "deduplicated": None,
        "input_row_count": None,
        "used_row_count": None,
        "duplicate_rows_within_run": None,
        "duplicate_mutants_within_run": None,
        "unique_mutants_within_run": None,
        "overall": execution_fields,
        "by_subject_function": [],
    }
    summary_path = execution_summary_path(run_dir)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(summary_path, payload)


def ensure_timeout_artifacts(run_dir: Path, cfg: dict, batch_timeout: int) -> None:
    subject = Subject(
        dataset=cfg.get("dataset", "unknown"),
        subject_id=cfg.get("subject", "unknown"),
        language=cfg.get("language", "java"),
        version=cfg.get("version", "unknown"),
    )
    target = Target(
        file_path=cfg.get("file", ""),
        function_name=cfg.get("function", ""),
        start_line=cfg.get("start_line"),
        end_line=cfg.get("end_line"),
        language=cfg.get("language", "java"),
        target_id=cfg.get("target_id"),
    )
    message = f"Batch timeout after {batch_timeout} seconds"

    write_run_manifest(
        run_dir=run_dir,
        subject=subject,
        target=target,
        mutants=[],
        run_mode=cfg.get("run_mode", "overwrite"),
        workdir_base=f"tmp/{cfg['run_name']}",
        extra_metadata={
            "batch_id": cfg.get("batch_id"),
            "target_id": cfg.get("target_id"),
            "run_group_id": cfg.get("run_group_id"),
            "run_index_for_target": cfg.get("run_index_for_target"),
            "runs_per_target": cfg.get("runs_per_target"),
            "model_name": cfg.get("model"),
            "model_provider": cfg.get("provider"),
            "n_requested_mutants": cfg.get("num_mutants"),
            "mutant_workers": cfg.get("mutant_workers", 1),
            "notes": message,
        },
        status="failure",
        failure_reason="batch_timeout",
        failure_message=message,
        started_at_utc=datetime.now(timezone.utc).isoformat(),
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    write_empty_results_csv(run_dir)
    write_empty_summary_json(
        run_dir,
        run_status="failure",
        failure_reason="batch_timeout",
        failure_message=message,
    )
    (run_dir / "run_error.txt").write_text(message + "\n", encoding="utf-8")


def ensure_failed_artifacts(
    run_dir: Path,
    cfg: dict,
    *,
    run_status: str,
    failure_reason: str | None,
    failure_message: str,
) -> None:
    manifest_file = manifest_path(run_dir)
    existing_manifest = None
    if manifest_file.exists():
        try:
            existing_manifest = load_json_path(manifest_file)
        except Exception:
            existing_manifest = None

    subject_data = (existing_manifest or {}).get("subject", {})
    target_data = (existing_manifest or {}).get("target", {})
    existing_extra = ((existing_manifest or {}).get("extra_metadata") or {})

    subject = Subject(
        dataset=subject_data.get("dataset") or cfg.get("dataset", "unknown"),
        subject_id=subject_data.get("subject_id") or cfg.get("subject", "unknown"),
        language=cfg.get("language", "java"),
        version=subject_data.get("version") or cfg.get("version", "unknown"),
    )
    target = Target(
        file_path=target_data.get("file_path") or cfg.get("file", ""),
        function_name=target_data.get("function_name") or cfg.get("function", ""),
        start_line=target_data.get("start_line", cfg.get("start_line")),
        end_line=target_data.get("end_line", cfg.get("end_line")),
        language=target_data.get("language") or cfg.get("language", "java"),
        target_id=target_data.get("target_id") or cfg.get("target_id"),
    )

    extra_metadata = {
        "batch_id": cfg.get("batch_id"),
        "target_id": cfg.get("target_id"),
        "run_group_id": cfg.get("run_group_id"),
        "run_index_for_target": cfg.get("run_index_for_target"),
        "runs_per_target": cfg.get("runs_per_target"),
        "model_name": cfg.get("model"),
        "model_provider": cfg.get("provider"),
        "n_requested_mutants": cfg.get("num_mutants"),
        "mutant_workers": cfg.get("mutant_workers", 1),
        "notes": failure_message,
    }
    extra_metadata.update(existing_extra)
    extra_metadata["batch_id"] = cfg.get("batch_id", extra_metadata.get("batch_id"))
    extra_metadata["target_id"] = cfg.get("target_id", extra_metadata.get("target_id"))
    extra_metadata["run_group_id"] = cfg.get("run_group_id", extra_metadata.get("run_group_id"))
    extra_metadata["run_index_for_target"] = cfg.get("run_index_for_target", extra_metadata.get("run_index_for_target"))
    extra_metadata["runs_per_target"] = cfg.get("runs_per_target", extra_metadata.get("runs_per_target"))
    extra_metadata["model_name"] = cfg.get("model", extra_metadata.get("model_name"))
    extra_metadata["model_provider"] = cfg.get("provider", extra_metadata.get("model_provider"))
    extra_metadata["n_requested_mutants"] = cfg.get("num_mutants", extra_metadata.get("n_requested_mutants"))
    extra_metadata["mutant_workers"] = cfg.get("mutant_workers", extra_metadata.get("mutant_workers", 1))
    extra_metadata["notes"] = failure_message

    write_run_manifest(
        run_dir=run_dir,
        subject=subject,
        target=target,
        mutants=[],
        run_mode=cfg.get("run_mode", "overwrite"),
        workdir_base=f"tmp/{cfg['run_name']}",
        extra_metadata=extra_metadata,
        status=run_status,
        failure_reason=failure_reason,
        failure_message=failure_message,
        started_at_utc=(existing_manifest or {}).get("started_at_utc") or datetime.now(timezone.utc).isoformat(),
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    manifest = load_json_path(manifest_file)
    manifest["requested_mutant_count"] = cfg.get("num_mutants", manifest.get("requested_mutant_count", 0))
    save_json(manifest_file, manifest)

    write_empty_results_csv(run_dir)
    write_empty_summary_json(
        run_dir,
        run_status=run_status,
        failure_reason=failure_reason,
        failure_message=failure_message,
    )
    (run_dir / "run_error.txt").write_text(failure_message + "\n", encoding="utf-8")


def load_run_status(run_dir: Path, return_code: int, timed_out: bool) -> tuple[str, str | None, str | None]:
    if timed_out:
        return "failure", "batch_timeout", None

    manifest_file = manifest_path(run_dir)
    if manifest_file.exists():
        try:
            manifest = load_json(manifest_file)
            manifest_status = manifest.get("status", "unknown")
            if manifest_status in {"generated", "running"}:
                return (
                    manifest_status,
                    manifest.get("failure_reason"),
                    manifest.get("failure_message"),
                )

            validate_run_dir(run_dir)
            return (
                manifest_status if manifest_status != "unknown" else ("ok" if return_code == 0 else "failure"),
                manifest.get("failure_reason"),
                manifest.get("failure_message"),
            )
        except FileNotFoundError as exc:
            return "failure", "missing_run_artifacts", str(exc)
        except Exception as exc:
            return "failure", "final_validation_failed", str(exc)

    return ("ok" if return_code == 0 else "failure"), None, None


def stream_process_output(proc: subprocess.Popen) -> threading.Thread:
    def _stream():
        if proc.stdout is None:
            return

        for line in proc.stdout:
            print(line, end="")

    thread = threading.Thread(target=_stream, daemon=True)
    thread.start()
    return thread


def print_run_artifact_status(run_dir: Path) -> None:
    manifest_file = manifest_path(run_dir)
    results_file = execution_results_path(run_dir)
    test_results_file = execution_test_results_path(run_dir)
    summary_file = execution_summary_path(run_dir)
    error_file = run_dir / "run_error.txt"

    print(f"[ARTIFACTS] manifest={manifest_file} exists={manifest_file.exists()}")
    print(f"[ARTIFACTS] results={results_file} exists={results_file.exists()}")
    print(f"[ARTIFACTS] test_results={test_results_file} exists={test_results_file.exists()}")
    print(f"[ARTIFACTS] summary={summary_file} exists={summary_file.exists()}")
    if error_file.exists():
        print(f"[ARTIFACTS] error={error_file} exists=True")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run_batch.py <config_file>")
        sys.exit(1)

    base_config_path = sys.argv[1]
    base_cfg = load_json(base_config_path)
    pipeline_mode = pipeline_mode_from_cfg(base_cfg)

    batches_dir = batches_root()
    batch_timeout = base_cfg.get("batch_timeout", 1200)
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    source_batch_manifest_path = None
    source_batch_manifest = None
    source_batch_id = None
    catalog_path = None
    catalog = []
    targets = []

    if pipeline_mode == "execute_only":
        source_batch_manifest_path = resolve_source_batch_manifest(base_cfg, batches_dir)
        source_batch_manifest = load_json_path(source_batch_manifest_path)
        source_batch_id = source_batch_manifest.get("batch_id")
        if not source_batch_id:
            raise ValueError("Source batch manifest is missing batch_id")
        batch_id = str(source_batch_id)
        log_path = logs_dir / f"{batch_id}.execution.log"
        run_specs = build_execute_only_runs(source_batch_manifest, base_cfg)
    else:
        batch_id = next_batch_id(batches_dir)
        log_path = logs_dir / f"{batch_id}.log"
        catalog_path = base_cfg["catalog_file"]
        catalog = load_catalog_entries(catalog_path)
        targets = [entry["target_id"] for entry in catalog]
        run_specs = build_generation_runs(catalog, base_cfg, batch_id)

    batch_manifest_path = llm_batch_manifest_path(batch_id)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file = log_path.open("w", encoding="utf-8")
    sys.stdout = TeeStream(original_stdout, log_file)
    sys.stderr = TeeStream(original_stderr, log_file)

    try:
        print(f"Batch log: {log_path}")

        if pipeline_mode == "execute_only":
            batch_manifest = dict(source_batch_manifest)
            batch_manifest["batch_id"] = batch_id
            batch_manifest["catalog_file"] = (
                base_cfg.get("catalog_file") or batch_manifest.get("catalog_file")
            )
            batch_manifest["targets"] = batch_manifest.get("targets") or [
                run.get("target_id")
                for run in batch_manifest.get("runs", [])
                if isinstance(run, dict) and run.get("target_id")
            ]
            batch_manifest["execution"] = {
                "created_at": datetime.now().isoformat(),
                "pipeline_mode": "execute_only",
                "base_config_file": base_config_path,
                "log_path": str(log_path),
                "batch_timeout": batch_timeout,
                "summary": {
                    "ok": 0,
                    "failure": 0,
                    "no_coverage": 0,
                    "no_valid_mutants": 0,
                    "generated": 0,
                    "total": 0,
                    "failure_reason_counts": {},
                },
            }
        else:
            batch_manifest = {
                "batch_id": batch_id,
                "created_at": datetime.now().isoformat(),
                "pipeline_mode": pipeline_mode,
                "catalog_file": catalog_path,
                "base_config_file": base_config_path,
                "log_path": str(log_path),
                "source_batch_id": source_batch_id,
                "source_batch_manifest": (
                    str(source_batch_manifest_path) if source_batch_manifest_path else None
                ),
                "model": base_cfg.get("model"),
                "prompt_file": base_cfg.get("prompt_file"),
                "num_mutants": base_cfg.get("num_mutants"),
                "timeout": base_cfg.get("timeout"),
                "batch_timeout": batch_timeout,
                "runs_per_target": base_cfg.get("runs_per_target"),
                "temperature": base_cfg.get("temperature", 0.0),
                "targets": targets,
                "runs": [],
                "summary": {
                    "ok": 0,
                    "failure": 0,
                    "no_coverage": 0,
                    "no_valid_mutants": 0,
                    "generated": 0,
                    "total": 0,
                    "failure_reason_counts": {},
                },
            }

        save_batch_manifest(batch_manifest_path, batch_manifest)

        print(
            f"[BATCH START] id={batch_id} pipeline_mode={pipeline_mode} "
            f"runs={len(run_specs)} timeout={batch_timeout}s"
        )
        print(f"[BATCH START] manifest={batch_manifest_path}")
        print(f"[BATCH START] log={log_path}")
        if pipeline_mode == "execute_only":
            print(f"[BATCH START] source_batch_manifest={source_batch_manifest_path}")
        else:
            print(f"[BATCH START] catalog={catalog_path}")

        total_runs = len(run_specs)
        for run_number, run_spec in enumerate(run_specs, start=1):
            target_id = run_spec.get("target_id")
            subject = run_spec.get("subject")
            function = run_spec.get("function")
            run_group_id = run_spec.get("run_group_id")
            run_index_for_target = run_spec.get("run_index_for_target")
            runs_per_target = run_spec.get("runs_per_target")
            run_name = run_spec["run_name"]
            cfg = dict(run_spec["cfg"])

            run_dir = run_dir_for_cfg(cfg, batch_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            save_json(run_dir / "run_config.json", cfg)

            tmp_config = Path("tmp") / "batch_configs" / batch_id / f"{run_name}.json"
            tmp_config.parent.mkdir(parents=True, exist_ok=True)
            tmp_config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

            print(
                f"\n=== Batch run {run_number}/{total_runs}: {target_id} "
                f"[run {run_index_for_target}/{runs_per_target}] -> {run_name} ==="
            )
            print(f"[RUN START] subject={subject} function={function}")
            print(f"[RUN START] run_dir={run_dir}")
            print(f"[RUN START] config={tmp_config}")
            run_started = time.time()

            proc = subprocess.Popen(
                ["python3", "run_llm.py", str(tmp_config)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            output_thread = stream_process_output(proc)

            timed_out = False

            try:
                return_code = proc.wait(timeout=batch_timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                print(f"[TIMEOUT] {run_name} exceeded {batch_timeout} seconds")
                kill_process_tree(proc.pid)
                time.sleep(0.2)

                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

                return_code = 124

            output_thread.join(timeout=5)

            if timed_out:
                ensure_timeout_artifacts(run_dir, cfg, batch_timeout)
                removed_tmp = cleanup_killed_run_tmp_paths(cfg)
                for path in removed_tmp:
                    print(f"[CLEANUP] Removed temp path after forced stop: {path}")

            status, failure_reason, detected_failure_message = load_run_status(run_dir, return_code, timed_out)
            if status == "failure":
                failure_message = (
                    f"Batch timeout after {batch_timeout} seconds"
                    if timed_out
                    else detected_failure_message or f"run_llm.py exited with return code {return_code}"
                )
                ensure_failed_artifacts(
                    run_dir,
                    cfg,
                    run_status=status,
                    failure_reason=failure_reason,
                    failure_message=failure_message,
                )
                removed_tmp = cleanup_killed_run_tmp_paths(cfg)
                for path in removed_tmp:
                    print(f"[CLEANUP] Removed leftover temp path after failure: {path}")

            if pipeline_mode == "execute_only":
                matched_run = None
                for existing_run in batch_manifest.get("runs", []):
                    if isinstance(existing_run, dict) and existing_run.get("run_name") == run_name:
                        matched_run = existing_run
                        break

                if matched_run is None:
                    matched_run = {
                        "target_id": target_id,
                        "subject": subject,
                        "function": function,
                        "run_group_id": run_group_id,
                        "run_index_for_target": run_index_for_target,
                        "run_name": run_name,
                        "run_dir": str(run_dir),
                    }
                    batch_manifest.setdefault("runs", []).append(matched_run)

                matched_run["execution_return_code"] = return_code
                matched_run["execution_status"] = status
                matched_run["execution_failure_reason"] = failure_reason
                matched_run["run_dir"] = str(run_dir)

                execution_summary = batch_manifest["execution"]["summary"]
                execution_summary["total"] += 1
                if status in execution_summary:
                    execution_summary[status] += 1

                if status == "failure" and failure_reason:
                    counts = execution_summary["failure_reason_counts"]
                    counts[failure_reason] = counts.get(failure_reason, 0) + 1
            else:
                batch_manifest["runs"].append({
                    "target_id": target_id,
                    "subject": subject,
                    "function": function,
                    "run_group_id": run_group_id,
                    "run_index_for_target": run_index_for_target,
                    "run_name": run_name,
                    "run_dir": str(run_dir),
                    "return_code": return_code,
                    "status": status,
                    "failure_reason": failure_reason,
                })

                batch_manifest["summary"]["total"] += 1
                if status in batch_manifest["summary"]:
                    batch_manifest["summary"][status] += 1

                if status == "failure" and failure_reason:
                    counts = batch_manifest["summary"]["failure_reason_counts"]
                    counts[failure_reason] = counts.get(failure_reason, 0) + 1

            run_elapsed = time.time() - run_started
            print(
                f"[RUN DONE] {run_name} status={status} return_code={return_code} "
                f"elapsed={format_elapsed(run_elapsed)}"
            )
            print_run_artifact_status(run_dir)

            save_batch_manifest(batch_manifest_path, batch_manifest)

        print("\nBatch complete.")
        print(f"Batch manifest: {batch_manifest_path}")
        print(f"Batch log: {log_path}")
        if pipeline_mode == "execute_only":
            print(f"Summary: {batch_manifest['execution']['summary']}")
        else:
            print(f"Summary: {batch_manifest['summary']}")
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log_file.close()


if __name__ == "__main__":
    main()
