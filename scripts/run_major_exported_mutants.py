#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.targets.catalog import get_target_by_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge Major exported mutant source files into the manual_mutants "
            "execution pipeline."
        )
    )
    parser.add_argument(
        "--catalog",
        required=True,
        help="Path to the catalog JSON used to define target file and line bounds.",
    )
    parser.add_argument(
        "--major-target-dir",
        required=True,
        help="Path to a Major per-target artifacts directory containing mutants/ and summary.json.",
    )
    parser.add_argument(
        "--target-id",
        help="Optional target_id override. Defaults to the target directory name.",
    )
    parser.add_argument(
        "--subject",
        help=(
            "Optional subject override in Project_BugId form. "
            "Required when summary.json is not present in the Major target directory."
        ),
    )
    parser.add_argument(
        "--run-name",
        required=True,
        help="Execution run name to create for the downstream execution pipeline.",
    )
    parser.add_argument(
        "--run-dir",
        help=(
            "Optional explicit run directory for downstream execution artifacts. "
            "If omitted, the manual_mutants default layout is used."
        ),
    )
    parser.add_argument(
        "--version",
        default="f",
        help="Subject version suffix for execution. Default: f.",
    )
    parser.add_argument(
        "--dataset",
        default="defects4j",
        help="Dataset label. Default: defects4j.",
    )
    parser.add_argument(
        "--language",
        default="java",
        help="Target language label. Default: java.",
    )
    parser.add_argument(
        "--mutant-workers",
        type=int,
        default=1,
        help="Parallel workers for execution. Default: 1.",
    )
    parser.add_argument(
        "--mutant-limit",
        type=int,
        help="Optional max number of exported mutants to include.",
    )
    parser.add_argument(
        "--output-config",
        help=(
            "Optional path for the generated manual_mutants config JSON. "
            "Defaults to <major-target-dir>/manual_execution_config.json."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Immediately run harness.executions.manual_mutants with the generated config.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_target_id(major_target_dir: Path, explicit_target_id: str | None) -> str:
    if explicit_target_id:
        return explicit_target_id
    return major_target_dir.name


def resolve_subject(major_target_dir: Path, explicit_subject: str | None) -> str:
    if explicit_subject:
        return explicit_subject
    summary_path = major_target_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(
            f"summary.json not found in {major_target_dir}; pass --subject explicitly"
        )
    summary = load_json(summary_path)
    subject = summary.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError(f"Could not resolve subject from {summary_path}")
    return subject


def load_mutant_log_lines(mutants_log: Path) -> dict[str, str]:
    if not mutants_log.exists():
        return {}

    lines_by_id: dict[str, str] = {}
    for raw_line in mutants_log.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if not stripped or ":" not in stripped:
            continue
        major_id = stripped.split(":", 1)[0].strip()
        if major_id:
            lines_by_id[major_id] = stripped
    return lines_by_id


def choose_mutated_file(mutant_dir: Path, target_file: str) -> Path:
    java_files = sorted(mutant_dir.rglob("*.java"))
    if not java_files:
        raise FileNotFoundError(f"No exported Java files found in {mutant_dir}")
    if len(java_files) == 1:
        return java_files[0]

    target_name = Path(target_file).name
    exact_name_matches = [path for path in java_files if path.name == target_name]
    if len(exact_name_matches) == 1:
        return exact_name_matches[0]

    target_parts = Path(target_file).parts
    suffix_matches: list[Path] = []
    for path in java_files:
        parts = path.parts
        for start in range(len(parts)):
            if tuple(parts[start:]) == target_parts[-len(parts[start:]) :]:
                suffix_matches.append(path)
                break
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    raise ValueError(
        f"Could not uniquely identify mutated file in {mutant_dir}; candidates={java_files}"
    )


def extract_target_slice(mutated_file: Path, start_line: int, end_line: int) -> str:
    lines = mutated_file.read_text(encoding="utf-8").splitlines()
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError(
            f"Invalid target slice {start_line}-{end_line} for {mutated_file} "
            f"with {len(lines)} lines"
        )
    return "\n".join(lines[start_line - 1 : end_line]) + "\n"


def build_config(args: argparse.Namespace) -> tuple[dict, Path]:
    major_target_dir = Path(args.major_target_dir).resolve()
    mutants_root = major_target_dir / "mutants"
    if not mutants_root.exists():
        raise FileNotFoundError(
            f"Exported mutants directory not found in {major_target_dir}. "
            "Run Major generation with --export-mutants first."
        )

    target_id = resolve_target_id(major_target_dir, args.target_id)
    target_entry = get_target_by_id(args.catalog, target_id)
    subject = resolve_subject(major_target_dir, args.subject)
    start_line = target_entry.get("start_line")
    end_line = target_entry.get("end_line")
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        raise ValueError(f"Target {target_id} is missing start_line/end_line in catalog")

    mutant_log_lines = load_mutant_log_lines(major_target_dir / "mutants.log")
    mutants = []
    mutant_dirs = sorted(
        path for path in mutants_root.iterdir() if path.is_dir() and path.name.isdigit()
    )
    if args.mutant_limit is not None:
        mutant_dirs = mutant_dirs[: args.mutant_limit]

    for mutant_dir in mutant_dirs:
        major_id = mutant_dir.name
        mutated_file = choose_mutated_file(mutant_dir, str(target_entry["file"]))
        mutants.append(
            {
                "mutant_id": f"major_{major_id}",
                "source": "major_export",
                "raw_response": mutant_log_lines.get(major_id),
                "code": extract_target_slice(mutated_file, start_line, end_line),
            }
        )

    if not mutants:
        raise ValueError(f"No exported mutant directories found under {mutants_root}")

    config = {
        "dataset": args.dataset,
        "subject": subject,
        "version": args.version,
        "language": target_entry.get("language", args.language),
        "catalog_file": str(Path(args.catalog).resolve()),
        "target_id": target_id,
        "run_name": args.run_name,
        "mutant_workers": args.mutant_workers,
        "notes": f"Major exported mutants bridged from {major_target_dir}",
        "mutants": mutants,
    }
    if args.run_dir:
        config["run_dir"] = str(Path(args.run_dir).resolve())
    output_config = (
        Path(args.output_config).resolve()
        if args.output_config
        else major_target_dir / "manual_execution_config.json"
    )
    return config, output_config


def run_execution(config_path: Path) -> int:
    cmd = ["python3", "-m", "harness.executions.manual_mutants", str(config_path)]
    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    args = parse_args()
    config, output_config = build_config(args)
    output_config.parent.mkdir(parents=True, exist_ok=True)
    output_config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    print(f"Generated manual config: {output_config}")
    print(f"Mutants included: {len(config['mutants'])}")
    print(f"Run name: {config['run_name']}")

    if args.execute:
        return run_execution(output_config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
