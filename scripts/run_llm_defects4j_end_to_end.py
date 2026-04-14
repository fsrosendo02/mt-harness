import argparse
from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.experiments.metadata import build_experiment_metadata
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.parsing import parse_report_to_dict
from harness.llm.prompt_builder import PromptBuilder
from harness.llm.providers.ollama_api_provider import OllamaApiProvider
from harness.models import Subject, Target
from harness.runners.mutation_runner import MutationRunner
from harness.utils.source import extract_target_code


def parse_args():
    parser = argparse.ArgumentParser(description="Run LLM-generated mutants on a Defects4J target.")
    parser.add_argument("--subject", required=True, help="Defects4J subject id, e.g. Lang_1")
    parser.add_argument("--version", default="f", help="Program version, default: f")
    parser.add_argument("--file", required=True, help="Target source file path inside the checked out project")
    parser.add_argument("--function", required=True, help="Target function name")
    parser.add_argument("--start-line", type=int, required=True, help="Start line of target region")
    parser.add_argument("--end-line", type=int, required=True, help="End line of target region")
    parser.add_argument("--model", required=True, help="Ollama model name")
    parser.add_argument("--num-mutants", type=int, default=1, help="Number of mutants to request")
    parser.add_argument("--timeout", type=int, default=420, help="Ollama timeout in seconds")
    parser.add_argument("--run-name", required=True, help="Run folder name under harness/runs")
    parser.add_argument("--prompt-file", required=True, help="Path to prompt template file")
    return parser.parse_args()


def main():
    args = parse_args()

    subject = Subject(
        dataset="defects4j",
        subject_id=args.subject,
        language="java",
        version=args.version,
    )

    target = Target(
        file_path=args.file,
        function_name=args.function,
        start_line=args.start_line,
        end_line=args.end_line,
    )

    run_dir = f"harness/runs/{args.run_name}"

    adapter = Defects4JAdapter()
    provider = OllamaApiProvider(args.model, timeout_seconds=args.timeout)
    prompt_builder = PromptBuilder(args.prompt_file)
    generator = LLMMutantGenerator(provider=provider, prompt_builder=prompt_builder)
    runner = MutationRunner(adapter)

    extract_workdir = f"tmp/llm_extract_{args.run_name}"
    adapter.checkout_subject(subject, extract_workdir)
    target_code = extract_target_code(extract_workdir, target)

    built_prompt = generator.prompt_builder.build(
        subject=subject,
        target=target,
        target_code=target_code,
        context_code=None,
        num_mutants=args.num_mutants,
    )

    print(f"Prompt file: {built_prompt.prompt_file}")
    print(f"Prompt length: {len(built_prompt.user_prompt)} chars")

    raw_text = provider.generate(
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        temperature=0.0,
    )

    mutants, report = generator.parser.parse_with_report(
        raw_text=raw_text,
        requested_count=args.num_mutants,
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

    workdir_base = f"tmp/{args.run_name}"

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=run_dir,
        workdir_base=workdir_base,
        run_mode="overwrite",
        extra_metadata=build_experiment_metadata(
            experiment_name=args.run_name,
            mutant_source="llm",
            model_name=args.model,
            model_provider="ollama",
            prompt_name=Path(args.prompt_file).stem,
            prompt_version="file_based",
            temperature=0.0,
            n_requested_mutants=args.num_mutants,
            generation_mode="batch_once",
            dataset_split=None,
            notes=f"Prompt file: {args.prompt_file}",
        ),
        cleanup_tmp=True,
        validate_after_run=True,
        rebuild_index=True,
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
