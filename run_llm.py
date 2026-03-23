import json
import sys
from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.experiments.metadata import build_experiment_metadata
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.parsing import parse_report_to_dict
from harness.llm.prompt_builder import PromptBuilder
from harness.llm.providers.ollama_provider import OllamaProvider
from harness.models import Subject, Target
from harness.runners.mutation_runner import MutationRunner
from harness.utils.source import extract_target_code


REQUIRED_FIELDS = [
    "dataset",
    "subject",
    "version",
    "file",
    "function",
    "start_line",
    "end_line",
    "model",
    "num_mutants",
    "timeout",
    "run_name",
    "prompt_file",
]


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    return data


def build_adapter(dataset: str):
    if dataset == "defects4j":
        return Defects4JAdapter()

    raise ValueError(f"Unsupported dataset: {dataset}")


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

    target = Target(
        file_path=cfg["file"],
        function_name=cfg["function"],
        start_line=cfg["start_line"],
        end_line=cfg["end_line"],
    )

    run_dir = f"harness/runs/{cfg['run_name']}"

    provider = OllamaProvider(cfg["model"], timeout_seconds=cfg["timeout"])
    prompt_builder = PromptBuilder(cfg["prompt_file"])
    generator = LLMMutantGenerator(provider=provider, prompt_builder=prompt_builder)
    runner = MutationRunner(adapter)

    base_snapshot_dir = f"tmp/base_{cfg['run_name']}"
    adapter.checkout_subject(subject, base_snapshot_dir)
    target_code = extract_target_code(base_snapshot_dir, target)

    built_prompt = generator.prompt_builder.build(
        subject=subject,
        target=target,
        target_code=target_code,
        context_code=None,
        num_mutants=cfg["num_mutants"],
    )

    print(f"Config file: {config_path}")
    print(f"Dataset: {cfg['dataset']}")
    print(f"Prompt file: {built_prompt.prompt_file}")
    print(f"Prompt length: {len(built_prompt.user_prompt)} chars")

    raw_text = provider.generate(
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        temperature=cfg.get("temperature", 0.0),
    )

    mutants, report = generator.parser.parse_with_report(
        raw_text=raw_text,
        requested_count=cfg["num_mutants"],
        original_target_code=target_code,
    )

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

    if not mutants:
        print()
        print("No valid mutants were parsed from the LLM output.")
        print(f"Generation artifacts saved to: {generation_dir}")
        return

    workdir_base = f"tmp/{cfg['run_name']}"

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=run_dir,
        workdir_base=workdir_base,
        base_snapshot_dir=base_snapshot_dir,
        run_mode=cfg.get("run_mode", "overwrite"),
        extra_metadata=build_experiment_metadata(
            experiment_name=cfg["run_name"],
            mutant_source="llm",
            model_name=cfg["model"],
            model_provider=cfg.get("model_provider", "ollama"),
            prompt_name=Path(cfg["prompt_file"]).stem,
            prompt_version=cfg.get("prompt_version", "file_based"),
            temperature=cfg.get("temperature", 0.0),
            n_requested_mutants=cfg["num_mutants"],
            generation_mode=cfg.get("generation_mode", "batch_once"),
            dataset_split=cfg.get("dataset_split"),
            notes=cfg.get(
                "notes",
                f"Prompt file: {cfg['prompt_file']}; Config file: {config_path}",
            ),
        ),
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

    print()
    print("Done.")
    print(f"Generation artifacts: {generation_dir}")
    print(f"Results stored in: {run_dir}")


if __name__ == "__main__":
    main()
