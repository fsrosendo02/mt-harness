import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def prepare_run_dir(run_dir, mode="fresh"):
    run_path = Path(run_dir)

    if mode not in {"fresh", "overwrite", "resume"}:
        raise ValueError(f"Unsupported run mode: {mode}")

    if mode == "fresh":
        if run_path.exists():
            raise FileExistsError(
                f"Run directory already exists: {run_path}. "
                f"Use a new path or mode='overwrite' or mode='resume'."
            )
        run_path.mkdir(parents=True, exist_ok=False)
        return run_path

    if mode == "overwrite":
        if run_path.exists():
            shutil.rmtree(run_path)
        run_path.mkdir(parents=True, exist_ok=False)
        return run_path

    run_path.mkdir(parents=True, exist_ok=True)
    return run_path


def load_completed_mutant_ids(csv_path):
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return set()

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return {row["mutant_id"] for row in reader if row.get("mutant_id")}


def write_run_manifest(
    run_dir,
    subject,
    target,
    mutants,
    run_mode,
    workdir_base,
    extra_metadata=None,
    status=None,
    failure_reason=None,
    failure_message=None,
    started_at_utc=None,
    completed_at_utc=None,
):
    run_path = Path(run_dir)
    manifest_path = run_path / "run_manifest.json"
    created_at_utc = datetime.now(timezone.utc).isoformat()

    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            created_at_utc = existing.get("created_at_utc", created_at_utc)
        except Exception:
            pass

    payload = {
        "created_at_utc": created_at_utc,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "run_mode": run_mode,
        "status": status or "unknown",
        "failure_reason": failure_reason,
        "failure_message": failure_message,
        "workdir_base": workdir_base,
        "requested_mutant_count": len(mutants),
        "subject": {
            "dataset": getattr(subject, "dataset", None),
            "subject_id": getattr(subject, "subject_id", None),
            "version": getattr(subject, "version", None),
        },
        "target": {
            "target_id": getattr(target, "target_id", None),
            "function_name": getattr(target, "function_name", None),
            "file_path": getattr(target, "file_path", None),
            "start_line": getattr(target, "start_line", None),
            "end_line": getattr(target, "end_line", None),
            "language": getattr(target, "language", None),
        },
        "mutant_ids": [getattr(m, "mutant_id", None) for m in mutants],
        "extra_metadata": extra_metadata or {},
    }

    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path
