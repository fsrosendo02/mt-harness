#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from prepare_major_checkout import patch_checkout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Checkout a Defects4J subject, patch its build.xml for Major, "
            "and run ant clean compile to generate mutants."
        )
    )
    parser.add_argument(
        "--subject",
        required=True,
        help="Defects4J subject in Project_BugId form, for example Lang_1.",
    )
    parser.add_argument(
        "--version",
        default="f",
        help="Defects4J version suffix, default: f.",
    )
    parser.add_argument(
        "--mml-bin",
        required=True,
        help="Absolute path to the compiled Major .mml.bin file.",
    )
    parser.add_argument(
        "--major-home",
        default=str(Path("harness/llm/providers/major").resolve()),
        help="Absolute path to the local Major installation directory.",
    )
    parser.add_argument(
        "--work-root",
        default="/tmp/major",
        help="Root directory for temporary Defects4J checkouts.",
    )
    parser.add_argument(
        "--checkout-name",
        help="Optional checkout directory name. Defaults to the subject id.",
    )
    parser.add_argument(
        "--keep-checkout",
        action="store_true",
        help="Keep an existing checkout directory instead of deleting it first.",
    )
    parser.add_argument(
        "--write-backup",
        action="store_true",
        help="Write build.xml.bak before patching build.xml.",
    )
    parser.add_argument(
        "--summary-json",
        help="Optional path to write a JSON summary.",
    )
    parser.add_argument(
        "--log-file",
        help="Optional path to write full checkout+compile output.",
    )
    parser.add_argument(
        "--artifacts-dir",
        help=(
            "Optional directory to copy run artifacts into, "
            "including mutants.log and the patched build.xml."
        ),
    )
    parser.add_argument(
        "--run-name",
        help="Optional run name to record in generated CSV rows.",
    )
    parser.add_argument(
        "--target-id",
        help="Optional target id to record in generated CSV rows.",
    )
    parser.add_argument(
        "--function-name",
        help="Optional function name to record in generated CSV rows.",
    )
    parser.add_argument(
        "--dataset",
        default="defects4j",
        help="Dataset label to record in generated CSV rows. Default: defects4j.",
    )
    return parser.parse_args()


def run_cmd(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def subject_parts(subject: str) -> tuple[str, str]:
    if "_" not in subject:
        raise ValueError(
            f"Subject must be in Project_BugId form, got: {subject!r}"
        )
    project, bug_id = subject.split("_", 1)
    if not project or not bug_id:
        raise ValueError(
            f"Subject must be in Project_BugId form, got: {subject!r}"
        )
    return project, bug_id


def parse_generated_mutants(ant_output: str) -> int | None:
    match = re.search(r"Generated\s+(\d+)\s+mutants", ant_output)
    return int(match.group(1)) if match else None


def count_mutants_log(mutants_log: Path) -> int | None:
    if not mutants_log.exists():
        return None
    with mutants_log.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def path_for_csv(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def format_log_block(title: str, command: list[str], output: str) -> str:
    lines = [
        f"[{now_iso()}] {title}",
        f"[{now_iso()}] COMMAND: {' '.join(command)}",
    ]
    if output.strip():
        for line in output.rstrip().splitlines():
            lines.append(f"[{now_iso()}] {line}")
    return "\n".join(lines) + "\n"


MUTANT_LOG_RE = re.compile(
    r"^(?P<major_id>\d+):(?P<operator>[A-Z]+):(?P<mutation_descriptor>.*):"
    r"(?P<owner>[\w.$]+)@(?P<method>[^(]+\([^)]*\)):(?P<source_line>\d+):"
    r"(?P<instruction_index>\d+):(?P<original_expression>.*) \|==> "
    r"(?P<replacement_expression>.*)$"
)


def parse_mutants_log_rows(
    *,
    mutants_log: Path,
    dataset: str,
    subject_id: str,
    target_id: str,
    run_name: str,
    function_name: str,
    build_status: str,
    executable: bool,
    log_path: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not mutants_log.exists():
        return rows

    with mutants_log.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            raw_line = raw_line.rstrip("\n")
            if not raw_line.strip():
                continue
            mutant_hash = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
            match = MUTANT_LOG_RE.match(raw_line)
            if match:
                parsed = match.groupdict()
                mutant_id = f"major_{parsed['major_id']}"
                owner = parsed["owner"]
                method = parsed["method"]
                source_line = int(parsed["source_line"])
                instruction_index = int(parsed["instruction_index"])
                operator = parsed["operator"]
                mutation_descriptor = parsed["mutation_descriptor"]
                original_expression = parsed["original_expression"]
                replacement_expression = parsed["replacement_expression"]
            else:
                mutant_id = f"major_{len(rows) + 1}"
                owner = ""
                method = ""
                source_line = ""
                instruction_index = ""
                operator = ""
                mutation_descriptor = ""
                original_expression = ""
                replacement_expression = ""

            rows.append(
                {
                    "dataset": dataset,
                    "subject_id": subject_id,
                    "target_id": target_id,
                    "run_name": run_name,
                    "function_name": function_name,
                    "mutant_id": mutant_id,
                    "mutant_hash": mutant_hash,
                    "build_status": build_status,
                    "test_status": "NOT_RUN",
                    "killed": "",
                    "executable": executable,
                    "log_path": log_path,
                    "operator": operator,
                    "mutation_descriptor": mutation_descriptor,
                    "owner": owner,
                    "method": method,
                    "source_line": source_line,
                    "instruction_index": instruction_index,
                    "original_expression": original_expression,
                    "replacement_expression": replacement_expression,
                    "raw_mutant": raw_line,
                }
            )
    return rows


def write_results_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    fieldnames = [
        "dataset",
        "subject_id",
        "target_id",
        "run_name",
        "function_name",
        "mutant_id",
        "mutant_hash",
        "build_status",
        "test_status",
        "killed",
        "executable",
        "log_path",
        "operator",
        "mutation_descriptor",
        "owner",
        "method",
        "source_line",
        "instruction_index",
        "original_expression",
        "replacement_expression",
        "raw_mutant",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    started_at = time.time()
    args = parse_args()
    project, bug_id = subject_parts(args.subject)
    checkout_name = args.checkout_name or args.subject
    checkout_dir = Path(args.work_root).resolve() / checkout_name

    if checkout_dir.exists() and not args.keep_checkout:
        shutil.rmtree(checkout_dir)

    checkout_dir.parent.mkdir(parents=True, exist_ok=True)

    checkout_cmd = [
        "defects4j",
        "checkout",
        "-p",
        project,
        "-v",
        f"{bug_id}{args.version}",
        "-w",
        str(checkout_dir),
    ]
    checkout_result = run_cmd(checkout_cmd)
    if checkout_result.returncode != 0:
        raise RuntimeError(
            "Defects4J checkout failed:\n"
            f"{checkout_result.stdout}{checkout_result.stderr}"
        )

    checkout_elapsed_sec = time.time() - started_at

    patch_result = patch_checkout(
        checkout=checkout_dir,
        mml_bin=Path(args.mml_bin),
        major_home=Path(args.major_home),
        write_backup=args.write_backup,
    )

    ant_bin = Path(args.major_home).resolve() / "bin" / "ant"
    compile_cmd = [str(ant_bin), "clean", "compile"]
    compile_started_at = time.time()
    compile_result = run_cmd(compile_cmd, cwd=checkout_dir)
    compile_elapsed_sec = time.time() - compile_started_at
    ant_output = compile_result.stdout + compile_result.stderr
    generated_mutants = parse_generated_mutants(ant_output)
    mutants_log = Path(patch_result["mutants_log"])
    mutants_log_count = count_mutants_log(mutants_log)
    total_elapsed_sec = time.time() - started_at

    checkout_output = checkout_result.stdout + checkout_result.stderr
    full_log_text = ""
    full_log_text += format_log_block("== defects4j checkout ==", checkout_cmd, checkout_output)
    full_log_text += "\n"
    full_log_text += format_log_block("== major ant clean compile ==", compile_cmd, ant_output)
    full_log_text += "\n"
    full_log_text += f"[{now_iso()}] checkout_elapsed_sec={checkout_elapsed_sec:.3f}\n"
    full_log_text += f"[{now_iso()}] compile_elapsed_sec={compile_elapsed_sec:.3f}\n"
    full_log_text += f"[{now_iso()}] total_elapsed_sec={total_elapsed_sec:.3f}\n"

    if args.log_file:
        log_path = Path(args.log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(full_log_text, encoding="utf-8")

    archived_mutants_log = None
    archived_build_xml = None
    if args.artifacts_dir:
        artifacts_dir = Path(args.artifacts_dir).resolve()
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        if mutants_log.exists():
            archived_mutants_log = artifacts_dir / "mutants.log"
            shutil.copy2(mutants_log, archived_mutants_log)
        build_xml_path = Path(patch_result["build_xml"])
        if build_xml_path.exists():
            archived_build_xml = artifacts_dir / "build.xml"
            shutil.copy2(build_xml_path, archived_build_xml)

    effective_run_name = args.run_name or checkout_name
    effective_target_id = args.target_id or checkout_name
    effective_function_name = args.function_name or ""
    effective_log_path = (
        path_for_csv(Path(args.log_file))
        if args.log_file
        else path_for_csv(Path(args.artifacts_dir) / "run.log") if args.artifacts_dir else ""
    )
    build_status = "SUCCESS" if compile_result.returncode == 0 else "FAIL"
    executable = compile_result.returncode == 0
    results_rows = parse_mutants_log_rows(
        mutants_log=mutants_log,
        dataset=args.dataset,
        subject_id=args.subject,
        target_id=effective_target_id,
        run_name=effective_run_name,
        function_name=effective_function_name,
        build_status=build_status,
        executable=executable,
        log_path=effective_log_path,
    )
    results_csv_path = None
    if args.artifacts_dir:
        results_csv_path = Path(args.artifacts_dir).resolve() / "results.csv"
        write_results_csv(results_rows, results_csv_path)

    summary = {
        "subject": args.subject,
        "version": args.version,
        "checkout_dir": str(checkout_dir),
        "mml_bin": str(Path(args.mml_bin).resolve()),
        "major_home": str(Path(args.major_home).resolve()),
        "build_xml": patch_result["build_xml"],
        "mutants_log": str(mutants_log),
        "checkout_returncode": checkout_result.returncode,
        "compile_returncode": compile_result.returncode,
        "checkout_elapsed_sec": round(checkout_elapsed_sec, 3),
        "compile_elapsed_sec": round(compile_elapsed_sec, 3),
        "total_elapsed_sec": round(total_elapsed_sec, 3),
        "generated_mutants": generated_mutants,
        "mutants_log_count": mutants_log_count,
        "results_row_count": len(results_rows),
    }
    if args.log_file:
        summary["log_file"] = str(Path(args.log_file).resolve())
    if archived_mutants_log is not None:
        summary["archived_mutants_log"] = str(archived_mutants_log)
    if archived_build_xml is not None:
        summary["archived_build_xml"] = str(archived_build_xml)
    if results_csv_path is not None:
        summary["results_csv"] = str(results_csv_path)

    if args.summary_json:
        summary_path = Path(args.summary_json).resolve()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Subject: {args.subject}")
    print(f"Checkout: {checkout_dir}")
    print(f"Build XML: {patch_result['build_xml']}")
    print(f"Mutants log: {mutants_log}")
    if generated_mutants is not None:
        print(f"Generated mutants: {generated_mutants}")
    if mutants_log_count is not None:
        print(f"mutants.log entries: {mutants_log_count}")
    print(f"Checkout elapsed: {checkout_elapsed_sec:.2f}s")
    print(f"Compile elapsed: {compile_elapsed_sec:.2f}s")
    print(f"Total elapsed: {total_elapsed_sec:.2f}s")
    if args.log_file:
        print(f"Log file: {Path(args.log_file).resolve()}")
    if results_csv_path is not None:
        print(f"Results CSV: {results_csv_path}")
    if archived_mutants_log is not None:
        print(f"Archived mutants log: {archived_mutants_log}")

    if compile_result.returncode != 0:
        print("\nCompile output:\n")
        print(ant_output.rstrip())
        raise SystemExit(compile_result.returncode)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
