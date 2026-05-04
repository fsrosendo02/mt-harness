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


def load_generation_mutants_with_recovery(output_dir: str | Path) -> tuple[list[Mutant], dict[str, Any]]:
    output_path = Path(output_dir)
    parsed_path = output_path / "parsed_mutants.json"

    if not parsed_path.exists():
        raise FileNotFoundError(f"Parsed mutants file not found: {parsed_path}")

    info: dict[str, Any] = {
        "parsed_mutants_path": str(parsed_path),
        "parsed_mutants_integrity": "ok",
        "requested_mutant_count": None,
        "loaded_mutant_count": 0,
        "recovered_mutant_count": 0,
        "missing_mutant_count": None,
        "recovery_sources": [],
        "warnings": [],
    }

    try:
        data = json.loads(parsed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        recovered = recover_execution_mutants(output_path.parent)
        if not recovered:
            raise ValueError(f"Corrupt parsed mutants file: {parsed_path}: {exc}") from exc
        info["parsed_mutants_integrity"] = "corrupt"
        info["loaded_mutant_count"] = len(recovered)
        info["recovered_mutant_count"] = len(recovered)
        info["recovery_sources"] = ["execution_artifacts"]
        info["warnings"].append(str(exc))
        return recovered, info

    items = data.get("mutants", [])
    if not isinstance(items, list):
        raise ValueError(f"Expected 'mutants' list in {parsed_path}")

    requested_count = data.get("requested_mutant_count")
    if isinstance(requested_count, int) and requested_count >= 0:
        info["requested_mutant_count"] = requested_count

    mutants: list[Mutant] = []
    invalid_entries = 0
    for item in items:
        if not isinstance(item, dict):
            invalid_entries += 1
            continue

        mutant_id = item.get("mutant_id")
        code = item.get("code")
        source = item.get("source", "llm")

        if not isinstance(mutant_id, str) or not mutant_id.strip():
            invalid_entries += 1
            continue
        if not isinstance(code, str):
            invalid_entries += 1
            continue
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

    recovered = recover_execution_mutants(output_path.parent, exclude_ids={m.mutant_id for m in mutants})
    all_mutants = mutants + recovered

    if invalid_entries or recovered:
        info["parsed_mutants_integrity"] = "partial"
    if invalid_entries:
        info["warnings"].append(f"Skipped {invalid_entries} invalid mutant entr(y/ies) in {parsed_path}")
    if recovered:
        info["recovery_sources"].append("execution_artifacts")

    info["loaded_mutant_count"] = len(all_mutants)
    info["recovered_mutant_count"] = len(recovered)
    if info["requested_mutant_count"] is None:
        info["requested_mutant_count"] = len(items)
    if info["requested_mutant_count"] is not None:
        info["missing_mutant_count"] = max(0, info["requested_mutant_count"] - len(all_mutants))

    return all_mutants, info


def recover_execution_mutants(run_dir: str | Path, *, exclude_ids: set[str] | None = None) -> list[Mutant]:
    execution_dir = Path(run_dir) / "execution"
    exclude_ids = exclude_ids or set()
    mutants: list[Mutant] = []

    if not execution_dir.exists():
        return mutants

    for mutant_path in sorted(execution_dir.glob("*.mutant.txt")):
        mutant_id = mutant_path.name.removesuffix(".mutant.txt")
        if mutant_id == "original" or mutant_id in exclude_ids:
            continue
        code = mutant_path.read_text(encoding="utf-8")
        mutants.append(
            Mutant(
                mutant_id=mutant_id,
                code=code,
                source="execution_recovered",
                raw_response=None,
            )
        )

    return mutants
