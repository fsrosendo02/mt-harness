from __future__ import annotations

from pathlib import Path
import re


HARNESS_DIR = Path("harness")
DATASETS_DIR = HARNESS_DIR / "datasets"
DATASET_CATALOGS_DIR = DATASETS_DIR / "catalogs"
DATASET_COVERAGE_DIR = DATASETS_DIR / "coverage"
DATASET_COVERAGE_CATALOGS_DIR = DATASET_COVERAGE_DIR / "catalogs"

EXECUTIONS_DIR = HARNESS_DIR / "executions"
RUNS_DIR = EXECUTIONS_DIR / "runs"
BATCHES_DIR = EXECUTIONS_DIR / "batches"

REPORTS_DIR = HARNESS_DIR / "reports"
EXPERIMENT_INDEX_CSV = REPORTS_DIR / "experiment_index.csv"
MATRICES_DIR = REPORTS_DIR / "matrices"
KILL_MATRICES_DIR = MATRICES_DIR / "base"

LEGACY_RUNS_DIR = HARNESS_DIR / "runs"
LEGACY_BATCHES_DIR = HARNESS_DIR / "experiments" / "batches"
LEGACY_EXPERIMENT_INDEX_CSV = HARNESS_DIR / "experiments" / "experiment_index.csv"
LEGACY_TARGET_COVERAGE_DIR = HARNESS_DIR / "experiments" / "target_coverage"
LEGACY_TARGET_TESTS_CSV = LEGACY_TARGET_COVERAGE_DIR / "target_tests.csv"
LEGACY_KILL_MATRICES_DIR = LEGACY_TARGET_COVERAGE_DIR / "kill_matrices"
LEGACY_DATASET_TARGETS_DIR = HARNESS_DIR / "targets"


def _safe_catalog_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "catalog"


def runs_root() -> Path:
    return RUNS_DIR


def batches_root() -> Path:
    return BATCHES_DIR


def reports_root() -> Path:
    return REPORTS_DIR


def catalogs_root() -> Path:
    return DATASET_CATALOGS_DIR


def target_tests_csv_path() -> Path:
    return global_target_tests_csv_path()


def global_target_tests_csv_path() -> Path:
    return DATASET_COVERAGE_DIR / "global_target_tests.csv"


def target_test_catalogs_dir() -> Path:
    return DATASET_COVERAGE_CATALOGS_DIR


def catalog_name_from_path(catalog_file: str | Path) -> str:
    return _safe_catalog_name(Path(catalog_file).stem)


def catalog_target_tests_dir(catalog_file: str | Path) -> Path:
    return DATASET_COVERAGE_CATALOGS_DIR / catalog_name_from_path(catalog_file)


def catalog_target_tests_csv_path(catalog_file: str | Path) -> Path:
    return catalog_target_tests_dir(catalog_file) / "target_tests.csv"


def experiment_index_path() -> Path:
    return EXPERIMENT_INDEX_CSV


def kill_matrices_dir() -> Path:
    return KILL_MATRICES_DIR


def matrices_dir() -> Path:
    return MATRICES_DIR


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


def execution_test_results_path(run_dir: str | Path) -> Path:
    return execution_dir(run_dir) / "test_results.csv"


def resolve_results_path(run_dir: str | Path) -> Path:
    current = execution_results_path(run_dir)
    legacy = run_path(run_dir) / "results.csv"
    return current if current.exists() or not legacy.exists() else legacy


def resolve_summary_path(run_dir: str | Path) -> Path:
    current = execution_summary_path(run_dir)
    legacy = run_path(run_dir) / "summary.json"
    return current if current.exists() or not legacy.exists() else legacy
