#!/usr/bin/env python3
"""Import a legacy LLM mutation ZIP into the current execution layout."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "harness/datasets/catalogs/defects4j_final_catalog.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def target_id_map(catalog_path: Path) -> dict[str, str]:
    payload = load_json(catalog_path)
    entries = payload.get("targets", []) if isinstance(payload, dict) else payload
    mapping: dict[str, str] = {}
    for entry in entries:
        target_id = str(entry["target_id"])
        prefix, separator, _end_line = target_id.rpartition("_")
        legacy_id = prefix if separator else target_id
        if legacy_id in mapping and mapping[legacy_id] != target_id:
            raise ValueError(f"Ambiguous legacy target ID: {legacy_id}")
        mapping[legacy_id] = target_id
    return mapping


def rewrite_payload(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: rewrite_payload(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_payload(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    return value


def import_archive(archive: Path, destination: Path, catalog: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    id_map = target_id_map(catalog)
    with tempfile.TemporaryDirectory(prefix="mt-import-") as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive) as source:
            bad_member = source.testzip()
            if bad_member:
                raise ValueError(f"Corrupt ZIP member: {bad_member}")
            source.extractall(staging)

        legacy_runs = staging / "harness/runs"
        legacy_batches = staging / "harness/experiments/batches"
        if not legacy_runs.is_dir() or not legacy_batches.is_dir():
            raise ValueError("ZIP does not contain the expected legacy runs and batches")

        destination.mkdir(parents=True)
        for run_dir in sorted(path for path in legacy_runs.iterdir() if path.is_dir()):
            shutil.move(str(run_dir), str(destination / run_dir.name))

        for legacy_manifest in sorted(legacy_batches.glob("batch*.json")):
            manifest = load_json(legacy_manifest)
            batch_id = str(manifest.get("batch_id") or legacy_manifest.stem)
            batch_dir = destination / batch_id
            batch_dir.mkdir(parents=True, exist_ok=True)

            manifest["catalog_file"] = "harness/datasets/catalogs/defects4j_final_catalog.json"
            for index, old_target_id in enumerate(manifest.get("targets", [])):
                if old_target_id not in id_map:
                    raise ValueError(f"No final-catalog match for {old_target_id}")
                manifest["targets"][index] = id_map[old_target_id]

            for run in manifest.get("runs", []):
                old_target_id = run.get("target_id")
                if old_target_id not in id_map:
                    raise ValueError(f"No final-catalog match for {old_target_id}")
                run["target_id"] = id_map[old_target_id]
                run["run_dir"] = str(
                    Path("harness/executions/java/llm/32") / str(run["run_name"])
                )

                parsed = destination / str(run["run_name"]) / "generation/parsed_mutants.json"
                if not parsed.exists():
                    if run.get("status") != "failure":
                        raise ValueError(f"Missing parsed mutants for non-failed run: {parsed}")
                    # A legacy batch timeout can leave no generation artifact at all.
                    # Preserve it as an explicit empty generation so execute_only can
                    # report no valid mutants instead of failing on a missing file.
                    save_json(parsed, {"mutants": []})

            save_json(batch_dir / "batch_manifest.json", manifest)

        replacements = {
            "harness/targets/defects4j_240funcs_topbranch.json": (
                "harness/datasets/catalogs/defects4j_final_catalog.json"
            ),
            "harness/runs/": "harness/executions/java/llm/32/",
        }
        for path in destination.rglob("*.json"):
            payload = rewrite_payload(load_json(path), replacements)
            if isinstance(payload, dict) and payload.get("target_id") in id_map:
                payload["target_id"] = id_map[payload["target_id"]]
            save_json(path, payload)


def validate(destination: Path) -> None:
    manifests = sorted(destination.glob("batch*/batch_manifest.json"))
    if len(manifests) != 6:
        raise ValueError(f"Expected 6 batch manifests, found {len(manifests)}")

    seen_runs: set[str] = set()
    for path in manifests:
        manifest = load_json(path)
        runs = manifest.get("runs", [])
        if len(runs) != 204:
            raise ValueError(f"{path}: expected 204 runs, found {len(runs)}")
        if manifest.get("num_mutants") != 32:
            raise ValueError(f"{path}: expected num_mutants=32")
        for run in runs:
            run_name = str(run["run_name"])
            parsed = destination / run_name / "generation/parsed_mutants.json"
            if not parsed.is_file():
                raise ValueError(f"Missing parsed mutants: {parsed}")
            seen_runs.add(run_name)

    actual_runs = {path.name for path in destination.iterdir() if path.is_dir() and "__" in path.name}
    if actual_runs != seen_runs:
        raise ValueError(
            f"Run mismatch: manifests={len(seen_runs)}, directories={len(actual_runs)}"
        )

    print(f"Imported {len(manifests)} batches and {len(seen_runs)} runs into {destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()

    import_archive(args.archive.resolve(), args.destination, args.catalog.resolve())
    validate(args.destination)


if __name__ == "__main__":
    main()
