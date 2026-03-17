import json
from pathlib import Path

from harness.models import Subject, Target, Mutant, MutantResult


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

    original_path = run_path / f"{base_name}.original.txt"
    mutant_path = run_path / f"{base_name}.mutant.txt"
    meta_path = run_path / f"{base_name}.json"

    original_path.write_text(original_code, encoding="utf-8")
    mutant_path.write_text(mutant.code, encoding="utf-8")

    metadata = {
        "dataset": subject.dataset,
        "subject_id": subject.subject_id,
        "language": subject.language,
        "version": getattr(subject, "version", None),
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