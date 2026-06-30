from __future__ import annotations

from pathlib import Path
import re


HARNESS_DIR = Path("harness")
DATASETS_DIR = HARNESS_DIR / "datasets"
DATASET_CATALOGS_DIR = DATASETS_DIR / "catalogs"
DATASET_COVERAGE_DIR = DATASETS_DIR / "coverage"
DATASET_COVERAGE_CATALOGS_DIR = DATASET_COVERAGE_DIR / "catalogs"

EXECUTIONS_DIR = HARNESS_DIR / "executions"
JAVA_EXECUTIONS_DIR = EXECUTIONS_DIR / "java"
RUNS_DIR = EXECUTIONS_DIR
BATCHES_DIR = EXECUTIONS_DIR / "batches"
LLM_EXECUTIONS_DIR = JAVA_EXECUTIONS_DIR / "llm"
MAJOR_EXECUTIONS_DIR = JAVA_EXECUTIONS_DIR / "major"
MAJOR_EXECUTION_CAMPAIGNS_DIR = MAJOR_EXECUTIONS_DIR / "execution"
LLM_DEFAULT_BATCH = "adhoc"

REPORTS_DIR = HARNESS_DIR / "reports"
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


def llm_root() -> Path:
    return LLM_EXECUTIONS_DIR


def major_root() -> Path:
    return MAJOR_EXECUTIONS_DIR


def major_execution_root() -> Path:
    return MAJOR_EXECUTION_CAMPAIGNS_DIR


def major_execution_campaign_dir(campaign_name: str) -> Path:
    return major_execution_root() / _safe_catalog_name(campaign_name)


def llm_batch_dir(batch_id: str, runs_subdir: str | None = None) -> Path:
    if runs_subdir:
        return runs_root() / runs_subdir.strip("/") / _safe_catalog_name(batch_id)
    return llm_root() / _safe_catalog_name(batch_id)


def llm_batch_runs_dir(batch_id: str) -> Path:
    return llm_batch_dir(batch_id) / "runs"


def llm_batch_manifest_path(batch_id: str, runs_subdir: str | None = None) -> Path:
    return llm_batch_dir(batch_id, runs_subdir) / "batch_manifest.json"


def llm_batch_summary_dir(batch_id: str, runs_subdir: str | None = None) -> Path:
    return llm_batch_dir(batch_id, runs_subdir) / "reports"


def default_llm_batch_id(batch_id: str | None = None) -> str:
    return _safe_catalog_name(batch_id or LLM_DEFAULT_BATCH)


def llm_run_dir(run_name: str, batch_id: str | None = None) -> Path:
    resolved_batch_id = default_llm_batch_id(batch_id or infer_batch_id_from_run_name(run_name))
    return llm_batch_runs_dir(resolved_batch_id) / run_name


def infer_batch_id_from_run_name(run_name: str) -> str | None:
    parts = str(run_name).split("__", 1)
    if len(parts) == 2 and parts[0]:
        return _safe_catalog_name(parts[0])
    return None


def resolve_run_dir(run_name: str, batch_id: str | None = None) -> Path:
    candidates: list[Path] = []
    if batch_id:
        candidates.append(llm_run_dir(run_name, batch_id))

    inferred_batch = infer_batch_id_from_run_name(run_name)
    if inferred_batch and inferred_batch != batch_id:
        candidates.append(llm_run_dir(run_name, inferred_batch))

    candidates.append(llm_run_dir(run_name, batch_id))
    candidates.append(RUNS_DIR / run_name)
    candidates.append(LEGACY_RUNS_DIR / run_name)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate

    return candidates[0]


def discover_run_dirs() -> list[Path]:
    discovered: list[Path] = []

    if llm_root().exists():
        discovered.extend(
            sorted(
                path
                for path in llm_root().glob("*/runs/*")
                if path.is_dir()
            )
        )

    for legacy_root in (RUNS_DIR, LEGACY_RUNS_DIR):
        if not legacy_root.exists():
            continue
        discovered.extend(
            sorted(
                path
                for path in legacy_root.iterdir()
                if path.is_dir() and (path / "run_manifest.json").exists()
            )
        )

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in discovered:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def discover_batch_manifest_paths() -> list[Path]:
    discovered: list[Path] = []

    if llm_root().exists():
        discovered.extend(
            sorted(
                path
                for path in llm_root().glob("*/batch_manifest.json")
                if path.is_file()
            )
        )

    # Also discover manifests co-located with runs when runs_subdir is used
    # (e.g. harness/executions/c/llm/batch01/batch_manifest.json)
    if runs_root().exists():
        discovered.extend(
            sorted(
                path
                for path in runs_root().glob("*/*/*/batch_manifest.json")
                if path.is_file()
            )
        )

    if BATCHES_DIR.exists():
        discovered.extend(sorted(path for path in BATCHES_DIR.glob("batch*.json") if path.is_file()))

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in discovered:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


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
    return llm_root() / "experiment_index.csv"


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
