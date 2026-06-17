#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.storage.layout import (
    BATCHES_DIR,
    LLM_DEFAULT_BATCH,
    REPORTS_DIR,
    llm_batch_manifest_path,
    llm_batch_summary_dir,
    llm_root,
    llm_run_dir,
    major_root,
)


LEGACY_RUNS_DIR = Path("harness/executions/runs")
LEGACY_LLM_ROOT = Path("harness/executions/llm")
LEGACY_MAJOR_ROOT = Path("harness/executions/major")
LEGACY_BATCH_SUMMARIES_DIR = REPORTS_DIR / "batch_summaries"
RUN_MANIFEST = "run_manifest.json"
LLM_MUTANT_SOURCES = {"llm", "manual"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def infer_batch_id(run_name: str, manifest: dict[str, Any]) -> str:
    extra = manifest.get("extra_metadata") or {}
    batch_id = extra.get("batch_id")
    if isinstance(batch_id, str) and batch_id.strip():
        return batch_id.strip()
    if "__" in run_name:
        return run_name.split("__", 1)[0]
    return LLM_DEFAULT_BATCH


def should_migrate_run(manifest: dict[str, Any]) -> bool:
    extra = manifest.get("extra_metadata") or {}
    mutant_source = extra.get("mutant_source")
    if mutant_source in LLM_MUTANT_SOURCES:
        return True
    if extra.get("batch_id"):
        return True
    return bool(extra.get("model_name") or extra.get("model_provider"))


def replace_strings(payload: Any, old: str, new: str) -> Any:
    if isinstance(payload, dict):
        return {key: replace_strings(value, old, new) for key, value in payload.items()}
    if isinstance(payload, list):
        return [replace_strings(value, old, new) for value in payload]
    if isinstance(payload, str):
        return payload.replace(old, new)
    return payload


def rewrite_json_file(path: Path, old: str, new: str) -> None:
    payload = load_json(path)
    save_json(path, replace_strings(payload, old, new))


def rewrite_text_file(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def rewrite_csv_file(path: Path, old: str, new: str) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow([
                cell.replace(old, new) if isinstance(cell, str) else cell
                for cell in row
            ])


def rewrite_run_artifacts(run_dir: Path, old: str, new: str) -> None:
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".json":
            rewrite_json_file(path, old, new)
        elif path.suffix == ".csv":
            rewrite_csv_file(path, old, new)
        elif path.suffix in {".txt", ".log"} or path.name == "run_error.txt":
            rewrite_text_file(path, old, new)


def rewrite_tree_artifacts(root: Path, old: str, new: str) -> None:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".json":
            rewrite_json_file(path, old, new)
        elif path.suffix == ".csv":
            rewrite_csv_file(path, old, new)
        elif path.suffix in {".txt", ".log", ".xml", ".mml", ".properties"} or path.name == "run_error.txt":
            rewrite_text_file(path, old, new)


def move_tree_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        target = destination / child.name
        if target.exists():
            if child.is_dir() and target.is_dir():
                move_tree_contents(child, target)
                child.rmdir()
                continue
            if child.is_file() and target.is_file():
                target.unlink()
                shutil.move(str(child), str(target))
                continue
            raise FileExistsError(f"Cannot merge {child} into existing {target}")
        shutil.move(str(child), str(target))


def migrate_runs() -> list[tuple[str, Path]]:
    migrated: list[tuple[str, Path]] = []

    if not LEGACY_RUNS_DIR.exists():
        return migrated

    for run_dir in sorted(path for path in LEGACY_RUNS_DIR.iterdir() if path.is_dir()):
        manifest_path = run_dir / RUN_MANIFEST
        if not manifest_path.exists():
            continue

        manifest = load_json(manifest_path)
        if not should_migrate_run(manifest):
            continue

        run_name = run_dir.name
        batch_id = infer_batch_id(run_name, manifest)
        destination = llm_run_dir(run_name, batch_id)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if run_dir.resolve() == destination.resolve():
            migrated.append((batch_id, destination))
            continue

        shutil.move(str(run_dir), str(destination))
        rewrite_run_artifacts(destination, str(run_dir), str(destination))
        migrated.append((batch_id, destination))

    return migrated


def migrate_llm_root() -> bool:
    if not LEGACY_LLM_ROOT.exists():
        return False

    destination = llm_root()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if LEGACY_LLM_ROOT.resolve() != destination.resolve():
        move_tree_contents(LEGACY_LLM_ROOT, destination)
        LEGACY_LLM_ROOT.rmdir()
    rewrite_tree_artifacts(destination, str(LEGACY_LLM_ROOT), str(destination))
    return True


def migrate_major_root() -> bool:
    if not LEGACY_MAJOR_ROOT.exists():
        return False

    destination = major_root()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if LEGACY_MAJOR_ROOT.resolve() != destination.resolve():
        move_tree_contents(LEGACY_MAJOR_ROOT, destination)
        LEGACY_MAJOR_ROOT.rmdir()
    rewrite_tree_artifacts(destination, str(LEGACY_MAJOR_ROOT), str(destination))
    return True


def migrate_batch_manifests() -> list[Path]:
    migrated: list[Path] = []

    if not BATCHES_DIR.exists():
        return migrated

    for legacy_path in sorted(BATCHES_DIR.glob("batch*.json")):
        payload = load_json(legacy_path)
        batch_id = str(payload.get("batch_id") or legacy_path.stem)
        destination = llm_batch_manifest_path(batch_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_path), str(destination))
        migrated.append(destination)

    return migrated


def migrate_batch_summaries() -> list[Path]:
    migrated: list[Path] = []

    if not LEGACY_BATCH_SUMMARIES_DIR.exists():
        return migrated

    for summary_dir in sorted(path for path in LEGACY_BATCH_SUMMARIES_DIR.iterdir() if path.is_dir()):
        destination = llm_batch_summary_dir(summary_dir.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(summary_dir), str(destination))
        migrated.append(destination)

    return migrated


def rewrite_batch_manifest_paths(batch_manifest_path: Path) -> None:
    payload = load_json(batch_manifest_path)
    batch_id = str(payload.get("batch_id") or batch_manifest_path.parent.name)

    for run in payload.get("runs", []):
        if not isinstance(run, dict):
            continue
        run_name = run.get("run_name")
        if not run_name:
            continue
        run["run_dir"] = str(llm_run_dir(str(run_name), batch_id))

    save_json(batch_manifest_path, payload)


def main() -> None:
    migrated_runs = migrate_runs()
    migrated_manifests = migrate_batch_manifests()
    migrated_summaries = migrate_batch_summaries()
    migrated_llm_root = migrate_llm_root()
    migrated_major_root = migrate_major_root()

    for batch_manifest_path in migrated_manifests:
        rewrite_batch_manifest_paths(batch_manifest_path)

    # Also normalize batch manifests that may already exist in the new tree.
    for batch_manifest_path in sorted(llm_root().glob("*/batch_manifest.json")):
        rewrite_batch_manifest_paths(batch_manifest_path)

    print(f"Migrated runs: {len(migrated_runs)}")
    print(f"Migrated batch manifests: {len(migrated_manifests)}")
    print(f"Migrated batch summary dirs: {len(migrated_summaries)}")
    print(f"Migrated LLM root: {migrated_llm_root}")
    print(f"Migrated Major root: {migrated_major_root}")


if __name__ == "__main__":
    main()
