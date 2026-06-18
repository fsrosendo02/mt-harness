import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.adapters.defects4j import Defects4JAdapter
from harness.executions.metadata import build_experiment_metadata
from harness.llm.parsing import LLMResponseParser
from harness.models import Mutant, Subject, Target
from harness.runners.mutation_runner import MutationRunner
from harness.storage.layout import catalog_target_tests_csv_path, llm_run_dir
from harness.targets.catalog import get_target_by_id
from harness.targets.resolver import resolve_target


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run manually supplied mutants through the harness without LLM generation."
    )
    parser.add_argument(
        "config",
        help="Path to a JSON file describing the subject, target, and mutant list.",
    )
    return parser.parse_args()


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_subject(cfg: dict) -> Subject:
    return Subject(
        dataset=cfg["dataset"],
        subject_id=cfg["subject"],
        language=cfg.get("language", "java"),
        version=cfg["version"],
    )


def build_target(cfg: dict) -> Target:
    if "target_id" in cfg:
        if "catalog_file" not in cfg:
            raise ValueError("Config uses target_id but is missing catalog_file")
        entry = get_target_by_id(cfg["catalog_file"], cfg["target_id"])
    else:
        entry = cfg

    return Target(
        file_path=entry["file"],
        function_name=entry["function"],
        start_line=entry.get("start_line"),
        end_line=entry.get("end_line"),
        language=entry.get("language", cfg.get("language", "java")),
        target_id=entry.get("target_id"),
    )


def build_mutants(cfg: dict, *, target_code: str, language: str) -> list[Mutant]:
    items = cfg.get("mutants")
    if not isinstance(items, list) or not items:
        raise ValueError("Config must define a non-empty 'mutants' list")

    if all(isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("code", "").strip() for item in items):
        mutants = []
        for index, item in enumerate(items, start=1):
            mutant_id = item.get("mutant_id") or f"m{index:02d}"
            mutants.append(
                Mutant(
                    mutant_id=mutant_id,
                    code=item["code"].rstrip() + "\n",
                    source=item.get("source", "manual"),
                    raw_response=item.get("raw_response"),
                )
            )
        return mutants

    if all(
        isinstance(item, dict)
        and item.get("line") is not None
        and item.get("precode") is not None
        and item.get("aftercode") is not None
        for item in items
    ):
        parser = LLMResponseParser()
        raw_text = json.dumps({"mutants": items}, ensure_ascii=False)
        mutants, report = parser.parse_with_report(
            raw_text=raw_text,
            requested_count=len(items),
            original_target_code=target_code,
            language=language,
        )
        print(f"Materialized {len(mutants)} mutant(s) from raw line-edit payload")
        print(f"Rejected candidates during materialization: {report.rejected_count}")
        for rejection in report.rejections:
            print(f"  - rejection[{rejection.index}]: {rejection.reason}")
        if not mutants:
            raise ValueError("No valid mutants could be materialized from raw payload")
        return mutants

    mutants = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"mutants[{index}] must be an object")
        mutant_id = item.get("mutant_id") or f"m{index:02d}"
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"mutants[{index}] is missing a non-empty 'code'")
        mutants.append(
            Mutant(
                mutant_id=mutant_id,
                code=code.rstrip() + "\n",
                source=item.get("source", "manual"),
                raw_response=item.get("raw_response"),
            )
        )
    return mutants


def main():
    args = parse_args()
    cfg = load_json(args.config)

    subject = build_subject(cfg)
    target = build_target(cfg)

    if cfg["dataset"] != "defects4j":
        raise ValueError("run_manual_mutants.py currently supports only dataset=defects4j")

    run_name = cfg["run_name"]
    configured_run_dir = cfg.get("run_dir")
    if configured_run_dir:
        run_dir = str(Path(configured_run_dir))
    else:
        run_dir = str(llm_run_dir(run_name, cfg.get("batch_id") or "manual"))
    base_snapshot_dir = f"tmp/base_{run_name}"
    workdir_base = f"tmp/{run_name}"

    adapter = Defects4JAdapter()
    runner = MutationRunner(adapter)

    print(f"Loading manual mutants from {args.config}")
    print(f"Run name: {run_name}")
    print(f"Target: {target.function_name} ({target.file_path}:{target.start_line}-{target.end_line})")
    print(f"Workers: {cfg.get('mutant_workers', 1)}")

    print(f"Checking out subject into {base_snapshot_dir}")
    adapter.checkout_subject(subject, base_snapshot_dir)
    target, target_code = resolve_target(cfg, base_snapshot_dir)
    mutants = build_mutants(
        cfg,
        target_code=target_code,
        language=target.language,
    )
    print(f"Loaded {len(mutants)} executable mutant(s)")

    extra_metadata = build_experiment_metadata(
        experiment_name=run_name,
        mutant_source="manual",
        model_name=None,
        model_provider="manual",
        prompt_name=None,
        prompt_version=None,
        temperature=None,
        n_requested_mutants=len(mutants),
        generation_mode="manual_input",
        dataset_split=None,
        batch_id=cfg.get("batch_id"),
        target_id=getattr(target, "target_id", None),
        run_group_id=cfg.get("run_group_id"),
        run_index_for_target=cfg.get("run_index_for_target"),
        runs_per_target=cfg.get("runs_per_target"),
        mutant_workers=cfg.get("mutant_workers", 1),
        notes=cfg.get("notes", f"Manual mutants loaded from {args.config}"),
    )

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=run_dir,
        workdir_base=workdir_base,
        base_snapshot_dir=base_snapshot_dir,
        run_mode=cfg.get("run_mode", "overwrite"),
        extra_metadata=extra_metadata,
        cleanup_tmp=cfg.get("cleanup_tmp", True),
        validate_after_run=cfg.get("validate_after_run", True),
        rebuild_index=cfg.get("rebuild_index", False),
        mutant_workers=cfg.get("mutant_workers", 1),
        target_tests_csv_path=(
            str(catalog_target_tests_csv_path(cfg["catalog_file"]))
            if cfg.get("catalog_file")
            else None
        ),
    )

    print()
    print("Done.")
    print(f"Results stored in: {run_dir}")


if __name__ == "__main__":
    main()
