import json
import sys
from pathlib import Path
import csv
import json
import re

from harness.adapters.defects4j import Defects4JAdapter
from harness.experiments.metadata import build_experiment_metadata
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.parsing import parse_report_to_dict
from harness.llm.prompt_builder import PromptBuilder
from harness.llm.providers.ollama_provider import OllamaProvider
from harness.llm.providers.gpt4o_provider import GPT4oProvider
from harness.models import Subject
from harness.runners.mutation_runner import MutationRunner
from harness.targets.resolver import resolve_target
from harness.storage.run_state import write_run_manifest





BASE_REQUIRED_FIELDS = [
    "dataset",
    "subject",
    "version",
    "model",
    "num_mutants",
    "timeout",
    "run_name",
    "prompt_file",
]

def extract_json(text: str) -> str:
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text

def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    missing = [field for field in BASE_REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    has_catalog_target = "target_id" in data
    has_manual_target = "file" in data and "function" in data

    if not has_catalog_target and not has_manual_target:
        raise ValueError(
            "Config must define either:\n"
            "  - target_id + catalog_file\n"
            "or\n"
            "  - file + function"
        )

    if has_catalog_target and "catalog_file" not in data:
        raise ValueError("Config uses target_id but is missing catalog_file")

    return data


def build_adapter(dataset: str):
    if dataset == "defects4j":
        return Defects4JAdapter()

    raise ValueError(f"Unsupported dataset: {dataset}")

def write_empty_results_csv(run_dir: str):
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    csv_path = run_path / "results.csv"

    fieldnames = [
        "dataset",
        "subject_id",
        "function_name",
        "mutant_id",
        "build_status",
        "test_status",
        "killed",
        "executable",
        "log_path",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

    return csv_path


def write_empty_summary_json(run_dir: str):
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    summary_path = run_path / "summary.json"

    payload = {
        "csv_path": str(run_path / "results.csv"),
        "json_path": str(summary_path),
        "deduplicated": True,
        "input_row_count": 0,
        "used_row_count": 0,
        "overall": {
            "total_mutants": 0,
            "build_successes": 0,
            "executable_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "baseline_failures": 0,
            "build_success_rate": 0.0,
            "executable_yield": 0.0,
            "mutation_score": None,
        },
        "by_subject_function": [],
    }

    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary_path

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 run_llm.py <config.json>")
        sys.exit(1)

    config_path = sys.argv[1]
    cfg = load_config(config_path)

    adapter = build_adapter(cfg["dataset"])

    subject = Subject(
        dataset=cfg["dataset"],
        subject_id=cfg["subject"],
        language=cfg.get("language", "java"),
        version=cfg["version"],
    )

    run_dir = f"harness/runs/{cfg['run_name']}"
    base_snapshot_dir = f"tmp/base_{cfg['run_name']}"
    workdir_base = f"tmp/{cfg['run_name']}"

    provider_type = cfg.get("provider", "ollama")

    if provider_type == "ollama":
        provider = OllamaProvider(cfg["model"], timeout_seconds=cfg["timeout"])

    elif provider_type == "gpt4o":
        provider = GPT4oProvider(cfg["model"], timeout_seconds=cfg["timeout"])

    else:
        raise ValueError(f"Unknown provider: {provider_type}")
    prompt_builder = PromptBuilder(cfg["prompt_file"])
    generator = LLMMutantGenerator(provider=provider, prompt_builder=prompt_builder)
    runner = MutationRunner(adapter)

    adapter.checkout_subject(subject, base_snapshot_dir)

    target, target_code = resolve_target(cfg, base_snapshot_dir)

    built_prompt = generator.prompt_builder.build(
        subject=subject,
        target=target,
        target_code=target_code,
        context_code=None,
        num_mutants=cfg["num_mutants"],
    )

    print(f"Config file: {config_path}")
    print(f"Dataset: {cfg['dataset']}")
    print(f"Subject: {cfg['subject']}")
    print(f"Version: {cfg['version']}")
    if cfg.get("target_id"):
        print(f"Target ID: {cfg['target_id']}")
    print(f"Resolved file: {target.file_path}")
    print(f"Resolved function: {target.function_name}")
    print(f"Resolved start_line: {target.start_line}")
    print(f"Resolved end_line: {target.end_line}")
    print(f"Prompt file: {built_prompt.prompt_file}")
    print(f"Prompt length: {len(built_prompt.user_prompt)} chars")

    from datetime import datetime
    print("DEBUG before provider.generate", datetime.now().isoformat())
    raw_text = provider.generate(
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        temperature=cfg.get("temperature", 0.0),
    )
    print("DEBUG after provider.generate", datetime.now().isoformat())

    raw_text = extract_json(raw_text)

    parse_failed = False
    parse_error_message = None

    try:
        mutants, report = generator.parser.parse_with_report(
            raw_text=raw_text,
            requested_count=cfg["num_mutants"],
            original_target_code=target_code,
            language=target.language,
        )
    except Exception as e:
        parse_failed = True
        parse_error_message = str(e)
        mutants = []

        from harness.llm.parsing import ParseReport
        report = ParseReport(
            requested_count=cfg["num_mutants"],
            accepted_count=0,
            rejected_count=cfg["num_mutants"],
            rejections=[],
        )

    n_requested_mutants = cfg["num_mutants"]
    n_accepted_mutants = len(mutants)
    n_rejected_mutants = report.rejected_count
    acceptance_rate = (
        n_accepted_mutants / n_requested_mutants
        if n_requested_mutants > 0 else None
    )

    rejection_reason_counts = {}
    for rej in report.rejections:
        rejection_reason_counts[rej.reason] = rejection_reason_counts.get(rej.reason, 0) + 1

    if parse_failed:
        rejection_reason_counts["invalid_json_response"] = 1

    print("Generated valid mutants:", len(mutants))
    print("Rejected candidates:", report.rejected_count)
    for rej in report.rejections:
        print(f"  - rejection[{rej.index}]: {rej.reason}")

    for m in mutants:
        print(f"- {m.mutant_id}")

    run_path = Path(run_dir)
    generation_dir = run_path / "generation"
    generation_dir.mkdir(parents=True, exist_ok=True)

    save_generation_artifacts(
        output_dir=generation_dir,
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        raw_response=raw_text,
        mutants=mutants,
        parse_report=parse_report_to_dict(report),
    )

    extra_metadata = build_experiment_metadata(
        experiment_name=cfg["run_name"],
        mutant_source="llm",
        model_name=cfg["model"],
        model_provider=cfg.get("model_provider", "ollama"),
        prompt_name=Path(cfg["prompt_file"]).stem,
        prompt_version=cfg.get("prompt_version", "file_based"),
        temperature=cfg.get("temperature", 0.0),
        n_requested_mutants=n_requested_mutants,
        generation_mode=cfg.get("generation_mode", "batch_once"),
        dataset_split=cfg.get("dataset_split"),
        batch_id=cfg.get("batch_id"),
        target_id=getattr(target, "target_id", None),
        n_accepted_mutants=n_accepted_mutants,
        n_rejected_mutants=n_rejected_mutants,
        acceptance_rate=acceptance_rate,
        rej_duplicate_mutant=rejection_reason_counts.get("duplicate_mutant", 0),
        rej_unchanged_mutant=rejection_reason_counts.get("unchanged_mutant", 0),
        rej_non_executable_change=rejection_reason_counts.get("non_executable_change", 0),
        rej_non_executable_structural_change=sum(
            count for reason, count in rejection_reason_counts.items()
            if str(reason).startswith("non_executable_structural_change")
        ),
        rej_precode_not_found=rejection_reason_counts.get("precode_not_found", 0),
        rej_ambiguous_precode_match=rejection_reason_counts.get("ambiguous_precode_match", 0),
        rej_invalid_json_response=rejection_reason_counts.get("invalid_json_response", 0),
        parse_failed=parse_failed,
        parse_error_message=parse_error_message,
        notes=cfg.get(
            "notes",
            f"Prompt file: {cfg['prompt_file']}; Config file: {config_path}",
        ),
    )

    if not mutants:
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)

        extra_metadata = build_experiment_metadata(
            experiment_name=cfg["run_name"],
            mutant_source="llm",
            model_name=cfg["model"],
            model_provider=cfg.get("model_provider", "ollama"),
            prompt_name=Path(cfg["prompt_file"]).stem,
            prompt_version=cfg.get("prompt_version", "file_based"),
            temperature=cfg.get("temperature", 0.0),
            n_requested_mutants=n_requested_mutants,
            generation_mode=cfg.get("generation_mode", "batch_once"),
            dataset_split=cfg.get("dataset_split"),
            batch_id=cfg.get("batch_id"),
            target_id=getattr(target, "target_id", None),
            n_accepted_mutants=n_accepted_mutants,
            n_rejected_mutants=n_rejected_mutants,
            acceptance_rate=acceptance_rate,
            rej_duplicate_mutant=rejection_reason_counts.get("duplicate_mutant", 0),
            rej_unchanged_mutant=rejection_reason_counts.get("unchanged_mutant", 0),
            rej_non_executable_change=rejection_reason_counts.get("non_executable_change", 0),
            rej_non_executable_structural_change=sum(
                count for reason, count in rejection_reason_counts.items()
                if str(reason).startswith("non_executable_structural_change")
            ),
            rej_precode_not_found=rejection_reason_counts.get("precode_not_found", 0),
            rej_ambiguous_precode_match=rejection_reason_counts.get("ambiguous_precode_match", 0),
            rej_invalid_json_response=rejection_reason_counts.get("invalid_json_response", 0),
            parse_failed=parse_failed,
            parse_error_message=parse_error_message,
            notes=cfg.get(
                "notes",
                f"Prompt file: {cfg['prompt_file']}; Config file: {config_path}",
            ),
        )

        write_run_manifest(
            run_dir=run_dir,
            subject=subject,
            target=target,
            mutants=[],
            run_mode=cfg.get("run_mode", "overwrite"),
            workdir_base=workdir_base,
            extra_metadata=extra_metadata,
        )

        write_empty_results_csv(run_dir)
        write_empty_summary_json(run_dir)

        if parse_failed:
            (generation_dir / "parse_error.txt").write_text(
                parse_error_message or "unknown parse error",
                encoding="utf-8",
            )

        if cfg.get("rebuild_index", True):
            from harness.experiments.build_experiment_index import build_experiment_index
            build_experiment_index()

        print()
        print("No valid mutants were parsed from the LLM output.")
        print(f"Generation artifacts saved to: {generation_dir}")
        print(f"Empty run artifacts stored in: {run_dir}")
        return

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
        rebuild_index=cfg.get("rebuild_index", True),
    )

    generation_dir = Path(run_dir) / "generation"
    save_generation_artifacts(
        output_dir=generation_dir,
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        raw_response=raw_text,
        mutants=mutants,
        parse_report=parse_report_to_dict(report),
    )

    if parse_failed:
        (generation_dir / "parse_error.txt").write_text(
            parse_error_message or "unknown parse error",
            encoding="utf-8",
        )

    print()
    print("Done.")
    print(f"Generation artifacts: {generation_dir}")
    print(f"Results stored in: {run_dir}")


if __name__ == "__main__":
    main()