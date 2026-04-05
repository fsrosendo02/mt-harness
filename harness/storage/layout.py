from __future__ import annotations

from pathlib import Path


def run_path(run_dir: str | Path) -> Path:
    return Path(run_dir)


def manifest_path(run_dir: str | Path) -> Path:
    return run_path(run_dir) / "run_manifest.json"


def generation_dir(run_dir: str | Path) -> Path:
    return run_path(run_dir) / "generation"


def execution_dir(run_dir: str | Path) -> Path:
    return run_path(run_dir) / "execution"


def rejected_dir(run_dir: str | Path) -> Path:
    return run_path(run_dir) / "rejected"


def execution_results_path(run_dir: str | Path) -> Path:
    return execution_dir(run_dir) / "results.csv"


def execution_summary_path(run_dir: str | Path) -> Path:
    return execution_dir(run_dir) / "summary.json"


def resolve_results_path(run_dir: str | Path) -> Path:
    current = execution_results_path(run_dir)
    legacy = run_path(run_dir) / "results.csv"
    return current if current.exists() or not legacy.exists() else legacy


def resolve_summary_path(run_dir: str | Path) -> Path:
    current = execution_summary_path(run_dir)
    legacy = run_path(run_dir) / "summary.json"
    return current if current.exists() or not legacy.exists() else legacy
