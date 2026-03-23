import json
from pathlib import Path
from typing import Any

from harness.models import Mutant


def save_generation_artifacts(
    *,
    output_dir: str | Path,
    system_prompt: str | None,
    user_prompt: str,
    raw_response: str,
    mutants: list[Mutant],
    parse_report: dict[str, Any] | None = None,
) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if system_prompt:
        (output_path / "system_prompt.txt").write_text(
            system_prompt.rstrip() + "\n",
            encoding="utf-8",
        )

    (output_path / "user_prompt.txt").write_text(
        user_prompt.rstrip() + "\n",
        encoding="utf-8",
    )

    (output_path / "raw_response.txt").write_text(
        raw_response.rstrip() + "\n",
        encoding="utf-8",
    )

    parsed = {
        "mutants": [
            {
                "mutant_id": m.mutant_id,
                "source": m.source,
                "code": m.code,
            }
            for m in mutants
        ]
    }

    (output_path / "parsed_mutants.json").write_text(
        json.dumps(parsed, indent=2),
        encoding="utf-8",
    )

    if parse_report is not None:
        (output_path / "parse_report.json").write_text(
            json.dumps(parse_report, indent=2),
            encoding="utf-8",
        )
