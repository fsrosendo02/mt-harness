import json
import os
import re
import signal
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from harness.models import Subject, Target
from harness.reporting.validation import validate_run_dir
from harness.storage.layout import execution_results_path, execution_summary_path, manifest_path
from harness.storage.run_state import write_run_manifest
from harness.targets.catalog import load_catalog_entries


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
    csv_path.write_text(
        "dataset,subject_id,function_name,mutant_id,build_status,test_status,killed,executable,log_path\n",
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
            "model_name": cfg.get("model"),
            "model_provider": cfg.get("provider"),
            "n_requested_mutants": cfg.get("num_mutants"),
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
        "model_name": cfg.get("model"),
        "model_provider": cfg.get("provider"),
        "n_requested_mutants": cfg.get("num_mutants"),
        "notes": failure_message,
    }
    extra_metadata.update(existing_extra)
    extra_metadata["batch_id"] = cfg.get("batch_id", extra_metadata.get("batch_id"))
    extra_metadata["target_id"] = cfg.get("target_id", extra_metadata.get("target_id"))
    extra_metadata["model_name"] = cfg.get("model", extra_metadata.get("model_name"))
    extra_metadata["model_provider"] = cfg.get("provider", extra_metadata.get("model_provider"))
    extra_metadata["n_requested_mutants"] = cfg.get("num_mutants", extra_metadata.get("n_requested_mutants"))
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
            validate_run_dir(run_dir)
            manifest = load_json(manifest_file)
            return (
                manifest.get("status", "ok" if return_code == 0 else "failure"),
                manifest.get("failure_reason"),
                manifest.get("failure_message"),
            )
        except FileNotFoundError as exc:
            return "failure", "missing_run_artifacts", str(exc)
        except Exception as exc:
            return "failure", "final_validation_failed", str(exc)

    return ("ok" if return_code == 0 else "failure"), None, None


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run_batch.py <config_file>")
        sys.exit(1)

    base_config_path = sys.argv[1]
    base_cfg = load_json(base_config_path)

    catalog_path = base_cfg["catalog_file"]
    catalog = load_catalog_entries(catalog_path)

    batches_dir = Path("harness/experiments/batches")
    batch_id = next_batch_id(batches_dir)
    batch_timeout = base_cfg.get("batch_timeout", 1200)

    batch_manifest = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "catalog_file": catalog_path,
        "base_config_file": base_config_path,
        "model": base_cfg["model"],
        "prompt_file": base_cfg["prompt_file"],
        "num_mutants": base_cfg["num_mutants"],
        "timeout": base_cfg["timeout"],
        "batch_timeout": batch_timeout,
        "temperature": base_cfg.get("temperature", 0.0),
        "targets": [entry["target_id"] for entry in catalog],
        "runs": [],
        "summary": {
            "ok": 0,
            "failure": 0,
            "no_valid_mutants": 0,
            "total": 0,
            "failure_reason_counts": {},
        },
    }

    for entry in catalog:
        target_id = entry["target_id"]
        subject = entry["subject"]
        function = entry["function"]

        run_name = f"{batch_id}__{slug(subject)}__{slug(function)}__{slug(target_id)}"

        cfg = dict(base_cfg)

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

        run_dir = Path(f"harness/runs/{run_name}")
        run_dir.mkdir(parents=True, exist_ok=True)
        save_json(run_dir / "run_config.json", cfg)

        tmp_config = Path("configs/tmp_batch_config.json")
        tmp_config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        print(f"\n=== Running {target_id} -> {run_name} ===")

        proc = subprocess.Popen(
            ["python3", "run_llm.py", str(tmp_config)],
            start_new_session=True,
        )

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

        batch_manifest["runs"].append({
            "target_id": target_id,
            "subject": subject,
            "function": function,
            "run_name": run_name,
            "run_dir": f"harness/runs/{run_name}",
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

        save_json(batches_dir / f"{batch_id}.json", batch_manifest)

    print("\nBatch complete.")
    print(f"Batch manifest: {batches_dir / f'{batch_id}.json'}")
    print(f"Summary: {batch_manifest['summary']}")


if __name__ == "__main__":
    main()
