#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.storage.layout import major_execution_campaign_dir
from harness.storage.results import RESULT_FIELDNAMES
from harness.storage.test_results import TEST_RESULT_FIELDNAMES


DEFAULT_CATALOG = "harness/datasets/catalogs/defects4j_final_catalog.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare and/or execute a full Major exported-mutants campaign into "
            "harness/executions/java/major/execution/<campaign>."
        )
    )
    parser.add_argument(
        "--source-campaign-dir",
        required=True,
        help="Path to a Major generation campaign directory, e.g. harness/executions/java/major/major_with_exports.",
    )
    parser.add_argument(
        "--execution-campaign",
        required=True,
        help="Campaign name to create under harness/executions/java/major/execution/.",
    )
    parser.add_argument(
        "--catalog",
        default=DEFAULT_CATALOG,
        help=f"Catalog JSON path. Default: {DEFAULT_CATALOG}.",
    )
    parser.add_argument(
        "--target-id",
        action="append",
        help="Optional target filter. Repeatable.",
    )
    parser.add_argument(
        "--mutant-workers",
        type=int,
        default=1,
        help="Mutant workers per target execution. Default: 1.",
    )
    parser.add_argument(
        "--mutant-limit-per-target",
        type=int,
        help="Optional limit of exported mutants to execute per target.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only generate per-target config files and manifests; do not execute.",
    )
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="Only rebuild aggregate CSV/summary from an existing execution campaign.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately if one target execution fails.",
    )
    parser.add_argument(
        "--rerun-existing",
        action="store_true",
        help="Re-execute targets even if they were already marked ok in targets.csv.",
    )
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "campaign"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for line in message.splitlines() or [""]:
            handle.write(f"[{timestamp}] {line}\n")


def load_selected_targets(source_campaign_dir: Path, target_filters: set[str] | None) -> list[dict[str, object]]:
    summary_path = source_campaign_dir / "run_summary.json"
    summary = load_json(summary_path)
    selected: list[dict[str, object]] = []

    for row in summary.get("results", []):
        if row.get("status") != "ok":
            continue
        if not row.get("archived_mutants_dir"):
            continue
        if not row.get("exported_mutant_java_files"):
            continue
        target_id = str(row.get("target_id") or "").strip()
        if not target_id:
            continue
        if target_filters and target_id not in target_filters:
            continue
        selected.append(row)

    return selected


def campaign_paths(campaign_name: str) -> dict[str, Path]:
    root = major_execution_campaign_dir(campaign_name).resolve()
    return {
        "root": root,
        "configs": root / "configs",
        "runs": root / "runs",
        "manifest": root / "manifest.json",
        "targets_csv": root / "targets.csv",
        "results_csv": root / "results.csv",
        "test_results_csv": root / "test_results.csv",
        "summary_json": root / "summary.json",
        "log": root / "campaign.log",
    }


def read_targets_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_targets_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "target_id",
        "subject",
        "version",
        "function_name",
        "major_target_dir",
        "run_name",
        "config_path",
        "exported_mutant_java_files",
        "status",
        "return_code",
        "run_dir",
        "execution_summary_json",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def build_run_name(campaign_name: str, target_id: str) -> str:
    return f"major_exec__{safe_name(campaign_name)}__{target_id}"


def prepare_manifest_rows(
    *,
    selected_targets: list[dict[str, object]],
    paths: dict[str, Path],
    campaign_name: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in selected_targets:
        target_id = str(row["target_id"])
        run_name = build_run_name(campaign_name, target_id)
        rows.append(
            {
                "target_id": target_id,
                "subject": row.get("subject", ""),
                "version": row.get("version", "f"),
                "function_name": row.get("function", ""),
                "major_target_dir": str(paths["source_targets_root"] / target_id),
                "run_name": run_name,
                "config_path": str(paths["configs"] / f"{target_id}.json"),
                "exported_mutant_java_files": row.get("exported_mutant_java_files", ""),
                "status": "prepared",
                "return_code": "",
                "run_dir": str(paths["runs"] / run_name),
                "execution_summary_json": str(
                    paths["runs"] / run_name / "execution" / "summary.json"
                ),
            }
        )
    return rows


def ensure_parent_dirs(paths: dict[str, Path]) -> None:
    for key in ("root", "configs", "runs"):
        paths[key].mkdir(parents=True, exist_ok=True)


def build_prepare_cmd(
    *,
    catalog: str,
    major_target_dir: str,
    subject: str,
    version: str,
    run_name: str,
    run_dir: str,
    config_path: str,
    mutant_workers: int,
    mutant_limit_per_target: int | None,
    execute: bool,
) -> list[str]:
    cmd = [
        "python3",
        "scripts/run_major_exported_mutants.py",
        "--catalog",
        catalog,
        "--major-target-dir",
        major_target_dir,
        "--subject",
        subject,
        "--version",
        version,
        "--run-name",
        run_name,
        "--run-dir",
        run_dir,
        "--output-config",
        config_path,
        "--mutant-workers",
        str(mutant_workers),
    ]
    if mutant_limit_per_target is not None:
        cmd.extend(["--mutant-limit", str(mutant_limit_per_target)])
    if execute:
        cmd.append("--execute")
    return cmd


def load_run_results(results_csv: Path) -> list[dict[str, str]]:
    if not results_csv.exists():
        return []
    with results_csv.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def aggregate_campaign(paths: dict[str, Path]) -> dict[str, object]:
    target_rows = read_targets_manifest(paths["targets_csv"])
    results_rows: list[dict[str, str]] = []
    test_rows: list[dict[str, str]] = []
    executed_targets = 0
    ok_targets = 0
    failed_targets = 0

    for target in target_rows:
        run_dir = Path(target.get("run_dir", ""))
        summary_json = Path(target.get("execution_summary_json", ""))
        results_csv = run_dir / "execution" / "results.csv"
        test_results_csv = run_dir / "execution" / "test_results.csv"

        if summary_json.exists():
            executed_targets += 1
        status = (target.get("status") or "").strip().lower()
        if status == "ok":
            ok_targets += 1
        elif status in {"failed", "missing_results"}:
            failed_targets += 1

        results_rows.extend(load_run_results(results_csv))
        test_rows.extend(load_run_results(test_results_csv))

    paths["results_csv"].parent.mkdir(parents=True, exist_ok=True)
    with paths["results_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDNAMES)
        writer.writeheader()
        for row in results_rows:
            writer.writerow({name: row.get(name, "") for name in RESULT_FIELDNAMES})

    with paths["test_results_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TEST_RESULT_FIELDNAMES)
        writer.writeheader()
        for row in test_rows:
            writer.writerow({name: row.get(name, "") for name in TEST_RESULT_FIELDNAMES})

    summary = {
        "campaign_root": str(paths["root"]),
        "targets_csv": str(paths["targets_csv"]),
        "results_csv": str(paths["results_csv"]),
        "test_results_csv": str(paths["test_results_csv"]),
        "total_targets": len(target_rows),
        "executed_targets": executed_targets,
        "ok_targets": ok_targets,
        "failed_targets": failed_targets,
        "result_rows": len(results_rows),
        "test_result_rows": len(test_rows),
        "aggregated_at_utc": now_utc(),
    }
    paths["summary_json"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    paths = campaign_paths(args.execution_campaign)
    paths["source_campaign_dir"] = Path(args.source_campaign_dir).resolve()
    paths["source_targets_root"] = paths["source_campaign_dir"] / "targets"
    ensure_parent_dirs(paths)

    if args.aggregate_only:
        summary = aggregate_campaign(paths)
        print(f"Aggregated campaign: {paths['root']}")
        print(f"Targets: {summary['total_targets']}")
        print(f"Result rows: {summary['result_rows']}")
        print(f"Test result rows: {summary['test_result_rows']}")
        return 0

    target_filters = set(args.target_id or [])
    selected_targets = load_selected_targets(
        paths["source_campaign_dir"],
        target_filters if target_filters else None,
    )

    manifest = {
        "execution_campaign": args.execution_campaign,
        "source_campaign_dir": str(paths["source_campaign_dir"]),
        "catalog": str(Path(args.catalog).resolve()),
        "mutant_workers": args.mutant_workers,
        "mutant_limit_per_target": args.mutant_limit_per_target,
        "prepare_only": args.prepare_only,
        "created_at_utc": now_utc(),
        "selected_target_count": len(selected_targets),
    }
    paths["manifest"].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    rows = prepare_manifest_rows(
        selected_targets=selected_targets,
        paths=paths,
        campaign_name=args.execution_campaign,
    )

    existing_by_target = {
        row["target_id"]: row
        for row in read_targets_manifest(paths["targets_csv"])
        if row.get("target_id")
    }
    merged_rows: list[dict[str, object]] = []
    for row in rows:
        existing = existing_by_target.get(str(row["target_id"]))
        if existing:
            row["status"] = existing.get("status", row["status"])
            row["return_code"] = existing.get("return_code", row["return_code"])
        merged_rows.append(row)
    write_targets_manifest(paths["targets_csv"], merged_rows)

    append_log(paths["log"], f"Execution campaign root: {paths['root']}")
    append_log(paths["log"], f"Source generation campaign: {paths['source_campaign_dir']}")
    append_log(paths["log"], f"Selected targets: {len(merged_rows)}")

    if args.prepare_only:
        for row in merged_rows:
            cmd = build_prepare_cmd(
                catalog=args.catalog,
                major_target_dir=str(row["major_target_dir"]),
                subject=str(row["subject"]),
                version=str(row["version"] or "f"),
                run_name=str(row["run_name"]),
                run_dir=str(row["run_dir"]),
                config_path=str(row["config_path"]),
                mutant_workers=args.mutant_workers,
                mutant_limit_per_target=args.mutant_limit_per_target,
                execute=False,
            )
            append_log(paths["log"], " ".join(cmd))
            subprocess.run(cmd, check=True)
        print(f"Prepared campaign in: {paths['root']}")
        print(f"Targets prepared: {len(merged_rows)}")
        return 0

    final_rows: list[dict[str, object]] = [dict(row) for row in merged_rows]
    for index, row in enumerate(final_rows):
        target_id = str(row["target_id"])
        if not args.rerun_existing and str(row.get("status", "")).lower() == "ok":
            append_log(paths["log"], f"[skip] {target_id} already marked ok")
            continue

        cmd = build_prepare_cmd(
            catalog=args.catalog,
            major_target_dir=str(row["major_target_dir"]),
            subject=str(row["subject"]),
            version=str(row["version"] or "f"),
            run_name=str(row["run_name"]),
            run_dir=str(row["run_dir"]),
            config_path=str(row["config_path"]),
            mutant_workers=args.mutant_workers,
            mutant_limit_per_target=args.mutant_limit_per_target,
            execute=True,
        )
        append_log(paths["log"], " ".join(cmd))
        result = subprocess.run(cmd)
        row["return_code"] = result.returncode

        run_dir = Path(str(row["run_dir"]))
        results_csv = run_dir / "execution" / "results.csv"
        row["status"] = "ok" if result.returncode == 0 and results_csv.exists() else "failed"
        if result.returncode == 0 and not results_csv.exists():
            row["status"] = "missing_results"

        append_log(
            paths["log"],
            f"[target {target_id}] returncode={result.returncode} status={row['status']}",
        )
        write_targets_manifest(paths["targets_csv"], final_rows)

        if result.returncode != 0 and args.stop_on_error:
            append_log(paths["log"], f"[stop] stopping on first error at {target_id}")
            break

    write_targets_manifest(paths["targets_csv"], final_rows)
    summary = aggregate_campaign(paths)
    print(f"Execution campaign: {paths['root']}")
    print(f"Targets selected: {len(final_rows)}")
    print(f"OK targets: {summary['ok_targets']}")
    print(f"Failed targets: {summary['failed_targets']}")
    print(f"Result rows: {summary['result_rows']}")
    print(f"Test result rows: {summary['test_result_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
