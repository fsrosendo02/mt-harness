#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness.storage.layout import discover_batch_manifest_paths


DEFAULT_BATCHES_DIR = Path("harness/executions/java/llm")
DEFAULT_TMP_DIR = Path("tmp/run_all_execution_batches")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def batch_sort_key(path: Path) -> tuple[int, str]:
    batch_label = path.parent.name if path.name == "batch_manifest.json" else path.stem
    match = re.fullmatch(r"batch(\d+)", batch_label)
    if match:
        return int(match.group(1)), batch_label
    return sys.maxsize, batch_label


def discover_batch_manifests(batches_dir: Path) -> list[Path]:
    discovered = []
    for path in discover_batch_manifest_paths():
        if batches_dir in path.parents or path.parent == batches_dir:
            discovered.append(path)
    return sorted(discovered, key=batch_sort_key)


def build_execution_config(base_cfg: dict[str, Any], batch_id: str) -> dict[str, Any]:
    cfg = dict(base_cfg)
    cfg["pipeline_mode"] = "execute_only"
    cfg["source_batch_id"] = batch_id
    cfg.pop("source_batch_manifest", None)
    return cfg


def run_batch_config(config_path: Path) -> int:
    proc = subprocess.run(["python3", "run_batch.py", str(config_path)])
    return int(proc.returncode)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run every existing batch manifest in execute_only mode."
    )
    parser.add_argument(
        "base_config",
        help=(
            "JSON config containing the shared execution settings "
            "(for example run_mode, mutant_workers, cleanup_tmp)."
        ),
    )
    parser.add_argument(
        "--batches-dir",
        type=Path,
        default=DEFAULT_BATCHES_DIR,
        help=f"Directory containing batch manifests (default: {DEFAULT_BATCHES_DIR}).",
    )
    parser.add_argument(
        "--tmp-dir",
        type=Path,
        default=DEFAULT_TMP_DIR,
        help=f"Directory used for generated per-batch configs (default: {DEFAULT_TMP_DIR}).",
    )
    parser.add_argument(
        "--batch-id",
        action="append",
        dest="batch_ids",
        default=[],
        help="Restrict execution to one or more batch ids. Can be passed multiple times.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the batches and generated config paths without running them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_config_path = Path(args.base_config)
    if not base_config_path.exists():
        raise FileNotFoundError(f"Base config not found: {base_config_path}")

    base_cfg = load_json(base_config_path)
    base_cfg["pipeline_mode"] = "execute_only"

    manifests = discover_batch_manifests(args.batches_dir)
    if args.batch_ids:
        wanted = set(args.batch_ids)
        manifests = [
            path for path in manifests
            if path.stem in wanted or path.parent.name in wanted
        ]

    if not manifests:
        raise ValueError(f"No batch manifests found in {args.batches_dir}")

    print(f"[DISCOVERED] {len(manifests)} batch manifest(s) in {args.batches_dir}")
    for manifest_path in manifests:
        print(f"  - {manifest_path.stem}: {manifest_path}")

    failures: list[tuple[str, int]] = []
    for manifest_path in manifests:
        batch_id = manifest_path.parent.name if manifest_path.name == "batch_manifest.json" else manifest_path.stem
        cfg = build_execution_config(base_cfg, batch_id)
        tmp_config_path = args.tmp_dir / f"{batch_id}.json"
        save_json(tmp_config_path, cfg)

        print(f"\n[PREPARED] {batch_id}")
        print(f"  config={tmp_config_path}")

        if args.dry_run:
            continue

        print(f"[RUNNING] python3 run_batch.py {tmp_config_path}")
        return_code = run_batch_config(tmp_config_path)
        print(f"[DONE] {batch_id} return_code={return_code}")

        if return_code != 0:
            failures.append((batch_id, return_code))

    if failures:
        print("\n[SUMMARY] Some batches failed:")
        for batch_id, return_code in failures:
            print(f"  - {batch_id}: return_code={return_code}")
        return 1

    print("\n[SUMMARY] All requested batches completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
