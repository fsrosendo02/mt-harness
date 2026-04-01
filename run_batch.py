import json
import os
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
import subprocess

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

        status = "timeout" if timed_out else ("ok" if return_code == 0 else "failed")

        batch_manifest["runs"].append({
            "target_id": target_id,
            "subject": subject,
            "function": function,
            "run_name": run_name,
            "run_dir": f"harness/runs/{run_name}",
            "return_code": return_code,
            "status": status,
        })

        batch_manifest["summary"]["total"] += 1
        if status == "ok":
            batch_manifest["summary"]["ok"] += 1
        elif status == "failed":
            batch_manifest["summary"]["failed"] += 1
        elif status == "timeout":
            batch_manifest["summary"]["timeout"] += 1

        save_json(batches_dir / f"{batch_id}.json", batch_manifest)

    print("\nBatch complete.")
    print(f"Batch manifest: {batches_dir / f'{batch_id}.json'}")
    print(f"Summary: {batch_manifest['summary']}")


if __name__ == "__main__":
    main()