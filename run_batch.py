import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from harness.models import Subject, Target
from harness.storage.run_state import write_run_manifest
from harness.targets.catalog import load_catalog_entries


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    csv_path = run_dir / "results.csv"
    csv_path.write_text(
        "dataset,subject_id,function_name,mutant_id,build_status,test_status,killed,executable,log_path\n",
        encoding="utf-8",
    )


def write_empty_summary_json(
    run_dir: Path,
    *,
    run_status: str,
    failure_reason: str | None,
    failure_message: str | None,
) -> None:
    payload = {
        "csv_path": str(run_dir / "results.csv"),
        "json_path": str(run_dir / "summary.json"),
        "run_status": run_status,
        "failure_reason": failure_reason,
        "failure_message": failure_message,
        "deduplicated": True,
        "input_row_count": 0,
        "used_row_count": 0,
        "overall": {
            "total_mutants": 0,
            "build_successes": 0,
            "executable_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "baseline_failures": 0,
            "build_success_rate": 0.0,
            "executable_yield": 0.0,
            "mutation_score": None,
        },
        "by_subject_function": [],
    }
    save_json(run_dir / "summary.json", payload)


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
        status="timeout",
        failure_reason="batch_timeout",
        failure_message=message,
        started_at_utc=datetime.now(timezone.utc).isoformat(),
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    write_empty_results_csv(run_dir)
    write_empty_summary_json(
        run_dir,
        run_status="timeout",
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
    if (run_dir / "run_manifest.json").exists():
        return

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
            "notes": failure_message,
        },
        status=run_status,
        failure_reason=failure_reason,
        failure_message=failure_message,
        started_at_utc=datetime.now(timezone.utc).isoformat(),
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    write_empty_results_csv(run_dir)
    write_empty_summary_json(
        run_dir,
        run_status=run_status,
        failure_reason=failure_reason,
        failure_message=failure_message,
    )
    (run_dir / "run_error.txt").write_text(failure_message + "\n", encoding="utf-8")


def load_run_status(run_dir: Path, return_code: int, timed_out: bool) -> tuple[str, str | None]:
    if timed_out:
        return "timeout", "batch_timeout"

    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        try:
            manifest = load_json(manifest_path)
            return manifest.get("status", "ok" if return_code == 0 else "failed"), manifest.get("failure_reason")
        except Exception:
            pass

    return ("ok" if return_code == 0 else "failed"), None


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
            "failed": 0,
            "timeout": 0,
            "parse_failed": 0,
            "no_valid_mutants": 0,
            "total": 0,
        },
    }

    for entry in catalog:
        target_id = entry["target_id"]
        subject = entry["subject"]
        function = entry["function"]

        run_name = f"{batch_id}__{slug(subject)}__{slug(function)}"

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

        status, failure_reason = load_run_status(run_dir, return_code, timed_out)
        if status != "ok":
            ensure_failed_artifacts(
                run_dir,
                cfg,
                run_status=status,
                failure_reason=failure_reason,
                failure_message=(
                    f"run_llm.py exited with return code {return_code}"
                    if not timed_out else f"Batch timeout after {batch_timeout} seconds"
                ),
            )

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
        elif status == "timeout":
            batch_manifest["summary"]["timeout"] += 1
        else:
            batch_manifest["summary"]["failed"] += 1

        save_json(batches_dir / f"{batch_id}.json", batch_manifest)

    print("\nBatch complete.")
    print(f"Batch manifest: {batches_dir / f'{batch_id}.json'}")
    print(f"Summary: {batch_manifest['summary']}")


if __name__ == "__main__":
    main()
