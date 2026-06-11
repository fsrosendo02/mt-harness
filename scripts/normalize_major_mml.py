#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize a Major MML file to match the operator/scoping subset "
            "accepted by the local Major build."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the source .mml file.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the normalized .mml output file.",
    )
    parser.add_argument(
        "--drop-operator",
        action="append",
        default=["BCR"],
        help="Operator prefix to drop entirely. Repeatable. Default: BCR.",
    )
    parser.add_argument(
        "--drop-disable-all",
        action="store_true",
        default=True,
        help="Drop 'disable ALL;' lines. Enabled by default.",
    )
    parser.add_argument(
        "--keep-disable-all",
        dest="drop_disable_all",
        action="store_false",
        help="Keep 'disable ALL;' lines.",
    )
    parser.add_argument(
        "--drop-leading-dollar-scopes",
        action="store_true",
        default=True,
        help=(
            "Drop scope lines whose class path contains a segment starting with '$', "
            "for example com.foo.$Bar::baz(). Enabled by default."
        ),
    )
    parser.add_argument(
        "--keep-leading-dollar-scopes",
        dest="drop_leading_dollar_scopes",
        action="store_false",
        help="Keep scopes that include path segments starting with '$'.",
    )
    return parser.parse_args()


def should_drop_line(
    line: str,
    *,
    drop_operators: set[str],
    drop_disable_all: bool,
    drop_leading_dollar_scopes: bool,
) -> tuple[bool, str | None]:
    stripped = line.strip()

    if drop_disable_all and stripped == "disable ALL;":
        return True, "disable_all"

    for operator in drop_operators:
        if stripped.startswith(f"{operator}<"):
            return True, f"operator:{operator}"

    if drop_leading_dollar_scopes:
        # Example problematic scope:
        # AOR<"com.google.gson.internal.$Gson$Types::resolve(Type,Class,Type)">;
        if re.search(r'<"[^"]*\.\$[^"]*::', stripped):
            return True, "leading_dollar_scope"

    return False, None


def normalize_mml_text(
    text: str,
    *,
    drop_operators: set[str],
    drop_disable_all: bool,
    drop_leading_dollar_scopes: bool,
) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    kept_lines: list[str] = []

    for line in text.splitlines():
        drop, reason = should_drop_line(
            line,
            drop_operators=drop_operators,
            drop_disable_all=drop_disable_all,
            drop_leading_dollar_scopes=drop_leading_dollar_scopes,
        )
        if drop:
            counts[reason or "unknown"] = counts.get(reason or "unknown", 0) + 1
            continue
        kept_lines.append(line)

    return "\n".join(kept_lines) + "\n", counts


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input MML file not found: {input_path}")

    drop_operators = {value.strip() for value in args.drop_operator if value.strip()}
    normalized_text, counts = normalize_mml_text(
        input_path.read_text(encoding="utf-8"),
        drop_operators=drop_operators,
        drop_disable_all=args.drop_disable_all,
        drop_leading_dollar_scopes=args.drop_leading_dollar_scopes,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(normalized_text, encoding="utf-8")

    print(f"Wrote normalized MML to {output_path}")
    if counts:
        for reason in sorted(counts):
            print(f"  dropped {counts[reason]:4d} line(s) for {reason}")
    else:
        print("  no lines were dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
