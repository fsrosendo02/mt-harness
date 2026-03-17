from pathlib import Path

from harness.models import Target


def extract_target_code(workdir: str, target: Target) -> str:
    file_path = Path(workdir) / target.file_path
    lines = file_path.read_text(encoding="utf-8").splitlines()

    selected = lines[target.start_line - 1 : target.end_line]
    return "\n".join(selected) + "\n"