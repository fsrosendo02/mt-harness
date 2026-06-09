#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


KNOWN_SOURCE_PREFIXES = (
    "src/main/java/",
    "src/java/",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Major mutation DSL (.mml) file scoped to the methods "
            "listed in a catalog."
        )
    )
    parser.add_argument(
        "--catalog",
        required=True,
        help="Path to the catalog JSON file.",
    )
    parser.add_argument(
        "--subject",
        help="Optional subject_id filter (for example: Lang_1).",
    )
    return parser.parse_args()


def load_catalog(catalog_path: Path) -> tuple[str, list[dict]]:
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Catalog must be a JSON object")

    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Catalog must contain a 'targets' list")

    catalog_name = data.get("catalog_name")
    if not isinstance(catalog_name, str) or not catalog_name.strip():
        catalog_name = catalog_path.stem

    return catalog_name.strip(), targets


def filter_targets(targets: list[dict], subject: str | None) -> list[dict]:
    filtered: list[dict] = []
    for entry in targets:
        if not isinstance(entry, dict):
            continue
        if subject is not None and entry.get("subject") != subject:
            continue
        filtered.append(entry)
    return filtered


def class_name_from_file(file_path: str) -> str:
    normalized = file_path.strip()

    for prefix in KNOWN_SOURCE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    if normalized.endswith(".java"):
        normalized = normalized[:-5]

    return normalized.replace("/", ".")


def build_output_path(output_catalog_name: str, subject: str | None) -> Path:
    output_dir = Path("scripts") / "major_dsl"
    suffix = f"_{subject}" if subject else ""
    return output_dir / f"{output_catalog_name}{suffix}.mml"


def build_lines(catalog_name: str, subject: str | None, targets: list[dict]) -> list[str]:
    header_subject = subject if subject else "ALL"
    lines = [
        f"// Major mutation DSL generated from catalog: {catalog_name}",
        f"// Subject scope: {header_subject}",
        f"// Target count: {len(targets)}",
        "",
        "disable ALL;",
    ]

    for entry in targets:
        file_path = entry.get("file")
        method_name = entry.get("function")
        target_id = entry.get("target_id")

        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError(f"Catalog entry is missing a valid 'file': {entry!r}")
        if not isinstance(method_name, str) or not method_name.strip():
            raise ValueError(f"Catalog entry is missing a valid 'function': {entry!r}")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError(f"Catalog entry is missing a valid 'target_id': {entry!r}")

        class_name = class_name_from_file(file_path)
        lines.append(
            f'mutate class "{class_name}" method "{method_name}"; // {target_id}'
        )

    return lines


def main() -> int:
    args = parse_args()
    catalog_path = Path(args.catalog)
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog file not found: {catalog_path}")

    catalog_name, targets = load_catalog(catalog_path)
    output_catalog_name = catalog_path.stem
    selected_targets = filter_targets(targets, args.subject)

    if not selected_targets:
        subject_note = f" for subject '{args.subject}'" if args.subject else ""
        raise ValueError(
            f"No targets found in catalog '{catalog_name}'{subject_note}"
        )

    output_path = build_output_path(output_catalog_name, args.subject)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = build_lines(catalog_name, args.subject, selected_targets)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {len(selected_targets)} target(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
