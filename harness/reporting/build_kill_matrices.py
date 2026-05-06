from __future__ import annotations

import argparse
import logging
from pathlib import Path

from harness.reporting.kill_matrix import (
    EXPERIMENT_INDEX_CSV,
    _require_pandas,
    _safe_output_name,
    build_matrix_per_model,
    build_matrix_per_run,
    build_matrix_per_target,
    load_all_runs,
)
from harness.storage.layout import kill_matrices_dir, matrices_dir


LOG = logging.getLogger(__name__)
DEFAULT_RUNS_DIR = kill_matrices_dir()
DEFAULT_OUTPUT_DIR = matrices_dir()


def _write_dataframe(df: "pd.DataFrame", output_path: Path, output_format: str) -> None:
    pd = _require_pandas()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "csv":
        df.to_csv(output_path)
        return

    try:
        import openpyxl  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Excel output requires openpyxl. Install it or rerun with --format csv."
        ) from exc

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="kill_matrix")


def _overall_mutation_score(df: "pd.DataFrame") -> float:
    if df.empty or "mutant_hash" not in df.columns:
        return 0.0

    working = df.copy()
    working["mutant_hash"] = working["mutant_hash"].fillna("").astype(str).str.strip()
    working = working[working["mutant_hash"] != ""]
    if working.empty:
        return 0.0

    killed_by_hash = working.groupby("mutant_hash")["outcome"].apply(
        lambda outcomes: outcomes.fillna("").astype(str).str.upper().eq("FAIL").any()
    )
    return float(killed_by_hash.mean()) if not killed_by_hash.empty else 0.0


def generate_kill_matrices(
    *,
    runs_dir: str | Path,
    index_path: str | Path,
    output_dir: str | Path,
    output_format: str,
) -> dict[str, int | float | Path]:
    pd = _require_pandas()

    runs_dir = Path(runs_dir)
    index_path = Path(index_path)
    output_dir = Path(output_dir)

    df = load_all_runs(runs_dir, index_path)
    per_run = build_matrix_per_run(df)
    per_target = build_matrix_per_target(df)
    per_model = build_matrix_per_model(df)

    suffix = ".csv" if output_format == "csv" else ".xlsx"
    per_run_dir = output_dir / "per_run"
    per_target_dir = output_dir / "per_target"
    per_model_dir = output_dir / "per_model"
    per_run_dir.mkdir(parents=True, exist_ok=True)
    per_target_dir.mkdir(parents=True, exist_ok=True)
    per_model_dir.mkdir(parents=True, exist_ok=True)

    for run_name, matrix in per_run.items():
        _write_dataframe(matrix, per_run_dir / f"{_safe_output_name(run_name)}{suffix}", output_format)

    for target_id, matrix in per_target.items():
        _write_dataframe(matrix, per_target_dir / f"{_safe_output_name(target_id)}{suffix}", output_format)

    _write_dataframe(per_model, per_model_dir / f"model_kill_rates{suffix}", output_format)

    unique_mutant_hashes = 0
    if not df.empty and "mutant_hash" in df.columns:
        unique_mutant_hashes = int(
            df["mutant_hash"].fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        )

    return {
        "output_dir": output_dir,
        "total_runs_loaded": int(df["run_name"].nunique()) if not df.empty and "run_name" in df.columns else 0,
        "unique_targets": int(df["target_id"].nunique()) if not df.empty and "target_id" in df.columns else 0,
        "unique_mutant_hashes": unique_mutant_hashes,
        "overall_mutation_score": _overall_mutation_score(df),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-run, per-target, and per-model kill matrices.")
    parser.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help="Directory tree containing *_kill_matrix_long.csv files.",
    )
    parser.add_argument("--index", default=str(EXPERIMENT_INDEX_CSV))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--format", choices=["csv", "excel"], default="csv")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        summary = generate_kill_matrices(
            runs_dir=args.runs_dir,
            index_path=args.index,
            output_dir=args.output,
            output_format=args.format,
        )
    except RuntimeError as exc:
        parser.exit(status=1, message=f"{exc}\n")

    print(f"Total runs loaded: {summary['total_runs_loaded']}")
    print(f"Unique targets: {summary['unique_targets']}")
    print(f"Unique mutant hashes: {summary['unique_mutant_hashes']}")
    print(f"Overall mutation score: {summary['overall_mutation_score']:.4f}")


if __name__ == "__main__":
    main()
