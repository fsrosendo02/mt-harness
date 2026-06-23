#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path so 'harness' is importable when this script
# is invoked directly (e.g. python3 scripts/run_major_catalog.py ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import csv
import json
import re
import subprocess
import time
from datetime import datetime

from normalize_major_mml import normalize_mml_text
from harness.storage.layout import major_root


TARGET_HEADER_RE = re.compile(r"^//\s+([^\s|]+)\s+\|")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Major compile generation for each catalog target and store "
            "artifacts under harness/executions/java/major/."
        )
    )
    parser.add_argument(
        "--catalog",
        required=True,
        help="Path to the catalog JSON file.",
    )
    parser.add_argument(
        "--source-mml",
        required=True,
        help="Path to the source Major .mml file to normalize.",
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
        "--logs-root",
        default=str(major_root()),
        help="Directory where Major execution artifacts and summaries will be written.",
    )
    parser.add_argument(
        "--run-name",
        help="Optional subdirectory name under the Major executions root. Defaults to the catalog stem.",
    )
    parser.add_argument(
        "--subject",
        action="append",
        help="Optional subject filter. Repeatable.",
    )
    parser.add_argument(
        "--target-id",
        action="append",
        help="Optional target_id filter. Repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional max number of targets to process after filtering.",
    )
    parser.add_argument(
        "--keep-checkout",
        action="store_true",
        help="Keep an existing checkout directory instead of recreating it.",
    )
    parser.add_argument(
        "--write-backup",
        action="store_true",
        help="Write build.xml.bak when patching each checkout.",
    )
    parser.add_argument(
        "--export-mutants",
        action="store_true",
        help="Enable Major source export for each generated mutant.",
    )
    return parser.parse_args()


def load_catalog_targets(catalog_path: Path) -> list[dict[str, object]]:
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ValueError("Catalog JSON must contain a 'targets' list")

    valid_targets: list[dict[str, object]] = []
    for entry in targets:
        if not isinstance(entry, dict):
            continue
        target_id = entry.get("target_id")
        subject = entry.get("subject")
        if not isinstance(target_id, str) or not target_id.strip():
            continue
        if not isinstance(subject, str) or not subject.strip():
            continue
        valid_targets.append(entry)
    return valid_targets


def filter_targets(
    targets: list[dict[str, object]],
    *,
    subjects: list[str] | None,
    target_ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, object]]:
    filtered = targets
    if subjects:
        wanted_subjects = set(subjects)
        filtered = [
            entry for entry in filtered if entry.get("subject") in wanted_subjects
        ]
    if target_ids:
        wanted_target_ids = set(target_ids)
        filtered = [
            entry for entry in filtered if entry.get("target_id") in wanted_target_ids
        ]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def run_cmd(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for line in message.splitlines() or [""]:
            handle.write(f"[{timestamp}] {line}\n")


def split_mml_targets(text: str) -> tuple[str, dict[str, str]]:
    preamble_lines: list[str] = []
    blocks: dict[str, list[str]] = {}
    current_target_id: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        header_match = TARGET_HEADER_RE.match(line)
        if header_match:
            if current_target_id is not None:
                blocks[current_target_id] = current_lines
            current_target_id = header_match.group(1)
            current_lines = [line]
            continue

        if current_target_id is None:
            preamble_lines.append(line)
        else:
            current_lines.append(line)

    if current_target_id is not None:
        blocks[current_target_id] = current_lines

    preamble = "\n".join(preamble_lines).rstrip() + "\n\n"
    normalized_blocks = {
        target_id: "\n".join(lines).rstrip() + "\n"
        for target_id, lines in blocks.items()
    }
    return preamble, normalized_blocks


def write_results_csv(results: list[dict[str, object]], output_path: Path) -> None:
    rows: list[dict[str, object]] = []
    for result in results:
        rows.append(
            {
                "target_id": result.get("target_id"),
                "subject": result.get("subject"),
                "function": result.get("function"),
                "file": result.get("file"),
                "start_line": result.get("start_line"),
                "end_line": result.get("end_line"),
                "status": result.get("status"),
                "mmlc_returncode": result.get("mmlc_returncode"),
                "checkout_returncode": result.get("checkout_returncode"),
                "compile_returncode": result.get("compile_returncode"),
                "generated_mutants": result.get("generated_mutants"),
                "mutants_log_count": result.get("mutants_log_count"),
                "mmlc_elapsed_sec": result.get("mmlc_elapsed_sec"),
                "checkout_elapsed_sec": result.get("checkout_elapsed_sec"),
                "compile_elapsed_sec": result.get("compile_elapsed_sec"),
                "total_elapsed_sec": result.get("total_elapsed_sec"),
                "target_dir": result.get("target_dir"),
                "summary_json": result.get("summary_json"),
                "log_file": result.get("log_file"),
            }
        )

    fieldnames = list(rows[0].keys()) if rows else [
        "target_id",
        "subject",
        "function",
        "file",
        "start_line",
        "end_line",
        "status",
        "mmlc_returncode",
        "checkout_returncode",
        "compile_returncode",
        "generated_mutants",
        "mutants_log_count",
        "mmlc_elapsed_sec",
        "checkout_elapsed_sec",
        "compile_elapsed_sec",
        "total_elapsed_sec",
        "target_dir",
        "summary_json",
        "log_file",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_mutant_results_csv(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_mutant_results_csv(rows: list[dict[str, str]], output_path: Path) -> None:
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
    args = parse_args()
    started_at = time.time()

    catalog_path = Path(args.catalog).resolve()
    source_mml = Path(args.source_mml).resolve()
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}")
    if not source_mml.exists():
        raise FileNotFoundError(f"Source MML not found: {source_mml}")

    run_name = args.run_name or catalog_path.stem
    logs_root = Path(args.logs_root).resolve() / run_name
    logs_root.mkdir(parents=True, exist_ok=True)
    campaign_log = logs_root / "campaign.log"
    campaign_log.write_text("", encoding="utf-8")

    normalized_mml = logs_root / f"{source_mml.stem}.normalized.mml"
    normalize_summary_path = logs_root / "normalize_summary.json"
    run_summary_path = logs_root / "run_summary.json"
    results_csv_path = logs_root / "results.csv"
    targets_csv_path = logs_root / "targets.csv"

    normalized_text, normalize_counts = normalize_mml_text(
        source_mml.read_text(encoding="utf-8"),
        drop_operators={"BCR"},
        drop_disable_all=True,
        drop_leading_dollar_scopes=True,
    )
    normalized_mml.write_text(normalized_text, encoding="utf-8")
    append_log(campaign_log, f"Catalog: {catalog_path}")
    append_log(campaign_log, f"Source MML: {source_mml}")
    append_log(campaign_log, f"Normalized MML: {normalized_mml}")
    append_log(
        campaign_log,
        f"Normalize dropped counts: {json.dumps(normalize_counts, sort_keys=True)}",
    )
    normalize_summary_path.write_text(
        json.dumps(
            {
                "source_mml": str(source_mml),
                "normalized_mml": str(normalized_mml),
                "dropped_counts": normalize_counts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    preamble, mml_blocks = split_mml_targets(normalized_text)
    targets = filter_targets(
        load_catalog_targets(catalog_path),
        subjects=args.subject,
        target_ids=args.target_id,
        limit=args.limit,
    )

    results: list[dict[str, object]] = []
    mutant_rows: list[dict[str, str]] = []
    targets_logs_dir = logs_root / "targets"
    targets_logs_dir.mkdir(parents=True, exist_ok=True)
    runner_script = Path("scripts/run_major_subject.py").resolve()
    mmlc_bin = Path(args.major_home).resolve() / "bin" / "mmlc"

    for entry in targets:
        target_started_at = time.time()
        target_id = str(entry["target_id"])
        subject = str(entry["subject"])
        target_dir = targets_logs_dir / target_id
        target_dir.mkdir(parents=True, exist_ok=True)
        summary_json = target_dir / "summary.json"
        log_file = target_dir / "run.log"
        target_results_csv = target_dir / "results.csv"
        target_mml = target_dir / f"{target_id}.mml"
        target_mml_bin = target_dir / f"{target_id}.mml.bin"
        target_mmlc_log = target_dir / "mmlc.log"

        append_log(campaign_log, "")
        append_log(campaign_log, f"== target {target_id} ({subject}) ==")

        block = mml_blocks.get(target_id)
        if block is None:
            result = {
                **entry,
                "status": "missing_mml_block",
                "target_dir": str(target_dir),
                "summary_json": str(summary_json),
                "log_file": str(log_file),
                "results_csv": str(target_results_csv),
            }
            append_log(
                campaign_log,
                f"[target {target_id}] missing block in normalized MML",
            )
            results.append(result)
            continue

        target_mml.write_text(preamble + block, encoding="utf-8")
        mmlc_cmd = [str(mmlc_bin), str(target_mml), str(target_mml_bin)]
        mmlc_started_at = time.time()
        mmlc_result = run_cmd(mmlc_cmd)
        mmlc_elapsed_sec = time.time() - mmlc_started_at
        target_mmlc_log.write_text(
            (mmlc_result.stdout or "") + (mmlc_result.stderr or ""),
            encoding="utf-8",
        )
        append_log(campaign_log, " ".join(mmlc_cmd))
        if mmlc_result.stdout:
            append_log(campaign_log, mmlc_result.stdout.rstrip())
        if mmlc_result.stderr:
            append_log(campaign_log, mmlc_result.stderr.rstrip())

        if mmlc_result.returncode != 0:
            result = {
                **entry,
                "status": "mmlc_failed",
                "mmlc_returncode": mmlc_result.returncode,
                "mmlc_elapsed_sec": round(mmlc_elapsed_sec, 3),
                "target_dir": str(target_dir),
                "summary_json": str(summary_json),
                "log_file": str(log_file),
                "results_csv": str(target_results_csv),
            }
            append_log(
                campaign_log,
                f"[target {target_id}] mmlc failed returncode={mmlc_result.returncode}",
            )
            results.append(result)
            continue

        cmd = [
            "python3",
            str(runner_script),
            "--subject",
            subject,
            "--checkout-name",
            target_id,
            "--mml-bin",
            str(target_mml_bin),
            "--major-home",
            str(Path(args.major_home).resolve()),
            "--work-root",
            args.work_root,
            "--summary-json",
            str(summary_json),
            "--log-file",
            str(log_file),
            "--artifacts-dir",
            str(target_dir),
            "--run-name",
            run_name,
            "--target-id",
            target_id,
            "--function-name",
            str(entry.get("function", "")),
        ]
        if args.keep_checkout:
            cmd.append("--keep-checkout")
        if args.write_backup:
            cmd.append("--write-backup")
        if args.export_mutants:
            cmd.append("--export-mutants")

        append_log(campaign_log, " ".join(cmd))
        run_result = run_cmd(cmd, cwd=Path.cwd())
        target_elapsed_sec = time.time() - target_started_at
        if run_result.stdout:
            append_log(campaign_log, run_result.stdout.rstrip())
        if run_result.stderr:
            append_log(campaign_log, run_result.stderr.rstrip())
        append_log(
            campaign_log,
            f"[target {target_id}] returncode={run_result.returncode} elapsed={target_elapsed_sec:.2f}s",
        )

        target_summary: dict[str, object] = {
            **entry,
            "status": "ok" if run_result.returncode == 0 else "compile_failed",
            "mmlc_returncode": mmlc_result.returncode,
            "mmlc_elapsed_sec": round(mmlc_elapsed_sec, 3),
            "target_dir": str(target_dir),
            "summary_json": str(summary_json),
            "log_file": str(log_file),
            "results_csv": str(target_results_csv),
        }
        if summary_json.exists():
            try:
                target_summary.update(json.loads(summary_json.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                target_summary["summary_parse_error"] = True
        else:
            target_summary["stdout"] = run_result.stdout
            target_summary["stderr"] = run_result.stderr
        mutant_rows.extend(read_mutant_results_csv(target_results_csv))
        results.append(target_summary)

    total_elapsed_sec = time.time() - started_at
    write_mutant_results_csv(mutant_rows, results_csv_path)
    write_results_csv(results, targets_csv_path)
    run_summary = {
        "catalog": str(catalog_path),
        "source_mml": str(source_mml),
        "normalized_mml": str(normalized_mml),
        "logs_root": str(logs_root),
        "campaign_log": str(campaign_log),
        "results_csv": str(results_csv_path),
        "targets_csv": str(targets_csv_path),
        "target_count": len(targets),
        "mutant_row_count": len(mutant_rows),
        "total_elapsed_sec": round(total_elapsed_sec, 3),
        "results": results,
    }
    run_summary_path.write_text(json.dumps(run_summary, indent=2) + "\n", encoding="utf-8")

    final_lines = [
        f"Catalog: {catalog_path}",
        f"Normalized MML: {normalized_mml}",
        f"Logs root: {logs_root}",
        f"Campaign log: {campaign_log}",
        f"Results CSV: {results_csv_path}",
        f"Targets CSV: {targets_csv_path}",
        f"Targets processed: {len(targets)}",
        f"Mutant rows: {len(mutant_rows)}",
        f"Elapsed: {total_elapsed_sec:.2f}s",
        f"Run summary: {run_summary_path}",
    ]
    for line in final_lines:
        print(line)
        append_log(campaign_log, line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
