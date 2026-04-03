import json
from pathlib import Path
from typing import Any

from harness.models import Subject, Target, Mutant, MutantResult


def _ensure_shared_original(path: Path, original_code: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(original_code, encoding="utf-8")
    return path


def save_mutant_artifacts(
    run_dir: str,
    subject: Subject,
    target: Target,
    mutant: Mutant,
    result: MutantResult,
    original_code: str,
) -> None:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    base_name = mutant.mutant_id

    original_path = _ensure_shared_original(run_path / "original.txt", original_code)
    mutant_path = run_path / f"{base_name}.mutant.txt"
    meta_path = run_path / f"{base_name}.json"

    mutant_path.write_text(mutant.code, encoding="utf-8")

    metadata = {
        "dataset": subject.dataset,
        "subject_id": subject.subject_id,
        "language": subject.language,
        "version": getattr(subject, "version", None),
        "target_id": getattr(target, "target_id", None),
        "file_path": target.file_path,
        "function_name": target.function_name,
        "start_line": target.start_line,
        "end_line": target.end_line,
        "mutant_id": mutant.mutant_id,
        "mutant_source": mutant.source,
        "build_status": result.build_status,
        "test_status": result.test_status,
        "killed": result.killed,
        "executable": result.executable,
        "log_path": result.log_path,
    }

    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def save_rejected_mutant_artifacts(
    run_dir: str,
    subject: Subject,
    target: Target,
    original_code: str,
    rejections: list[dict[str, Any]],
    raw_response_path: str | None = None,
) -> None:
    if not rejections:
        return

    rejected_dir = Path(run_dir) / "rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)

    for idx, rejection in enumerate(rejections, start=1):
        rejection_index = rejection.get("index")
        if isinstance(rejection_index, int) and rejection_index > 0:
            base_name = f"rej{rejection_index:02d}"
        else:
            base_name = f"rej{idx:02d}"
        payload = rejection.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {"raw_payload": payload}
        candidate_code = payload_dict.get("candidate_code")

        original_path = _ensure_shared_original(rejected_dir / "original.txt", original_code)

        mutant_text = (
            candidate_code
            if isinstance(candidate_code, str) and candidate_code.strip()
            else json.dumps(payload, indent=2, ensure_ascii=False)
        )
        (rejected_dir / f"{base_name}.mutant.txt").write_text(
            mutant_text.rstrip() + "\n",
            encoding="utf-8",
        )

        log_lines = [
            f"rejection_index: {rejection.get('index')}",
            f"artifact_id: {base_name}",
            f"reason: {rejection.get('reason')}",
        ]
        if raw_response_path:
            log_lines.append(f"raw_response_path: {raw_response_path}")
        if isinstance(payload_dict.get("resolution_reason"), str):
            log_lines.append(f"resolution_reason: {payload_dict['resolution_reason']}")
        if isinstance(payload_dict.get("line"), int):
            log_lines.append(f"line: {payload_dict['line']}")
        if payload_dict.get("precode") is not None:
            log_lines.append(f"precode: {payload_dict['precode']}")
        if payload_dict.get("aftercode") is not None:
            log_lines.append(f"aftercode: {payload_dict['aftercode']}")

        (rejected_dir / f"{base_name}.log").write_text(
            "\n".join(log_lines).rstrip() + "\n",
            encoding="utf-8",
        )

        metadata = {
            "dataset": subject.dataset,
            "subject_id": subject.subject_id,
            "language": subject.language,
            "version": getattr(subject, "version", None),
            "target_id": getattr(target, "target_id", None),
            "file_path": target.file_path,
            "function_name": target.function_name,
            "start_line": target.start_line,
            "end_line": target.end_line,
            "mutant_id": base_name,
            "mutant_source": "llm_rejected",
            "artifact_type": "rejected_candidate",
            "rejection_index": rejection.get("index"),
            "rejection_reason": rejection.get("reason"),
            "raw_response_path": raw_response_path,
            "payload": payload,
            "log_path": str(rejected_dir / f"{base_name}.log"),
            "mutant_path": str(rejected_dir / f"{base_name}.mutant.txt"),
            "original_path": str(original_path),
        }

        (rejected_dir / f"{base_name}.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
