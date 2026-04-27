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


def load_generation_mutants(output_dir: str | Path) -> list[Mutant]:
    output_path = Path(output_dir)
    parsed_path = output_path / "parsed_mutants.json"

    if not parsed_path.exists():
        raise FileNotFoundError(f"Parsed mutants file not found: {parsed_path}")

    data = json.loads(parsed_path.read_text(encoding="utf-8"))
    items = data.get("mutants", [])
    if not isinstance(items, list):
        raise ValueError(f"Expected 'mutants' list in {parsed_path}")

    mutants: list[Mutant] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid mutant entry in {parsed_path}: {item!r}")

        mutant_id = item.get("mutant_id")
        code = item.get("code")
        source = item.get("source", "llm")

        if not isinstance(mutant_id, str) or not mutant_id.strip():
            raise ValueError(f"Missing mutant_id in {parsed_path}")
        if not isinstance(code, str):
            raise ValueError(f"Missing code for mutant {mutant_id} in {parsed_path}")
        if not isinstance(source, str) or not source.strip():
            source = "llm"

        mutants.append(
            Mutant(
                mutant_id=mutant_id,
                code=code,
                source=source,
                raw_response=None,
            )
        )

    return mutants
