from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.experiments.metadata import build_experiment_metadata
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.parsing import parse_report_to_dict
from harness.llm.providers.ollama_provider import OllamaProvider
from harness.models import Subject, Target
from harness.runners.mutation_runner import MutationRunner
from harness.utils.source import extract_target_code


def main():
    subject = Subject(
        dataset="defects4j",
        subject_id="Lang_1",
        language="java",
        version="f",
    )

    target = Target(
        file_path="src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
        function_name="createNumber",
        start_line=450,
        end_line=623,
    )

    run_dir = "harness/runs/run_defects4j_llm_lang1"

    adapter = Defects4JAdapter()
    provider = OllamaProvider("qwen2.5-coder:7b", timeout_seconds=420)
    generator = LLMMutantGenerator(provider=provider)
    runner = MutationRunner(adapter)

    extract_workdir = "tmp/llm_extract_lang1"
    adapter.checkout_subject(subject, extract_workdir)
    target_code = extract_target_code(extract_workdir, target)

    built_prompt = generator.prompt_builder.build(
        subject=subject,
        target=target,
        target_code=target_code,
        context_code=None,
        num_mutants=1,
    )

    print(f"Prompt length: {len(built_prompt.user_prompt)} chars")

    raw_text = provider.generate(
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        temperature=0.0,
    )

    mutants, report = generator.parser.parse_with_report(
        raw_text=raw_text,
        requested_count=1,
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

    workdir_base = "tmp/Lang_1_llm"

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=run_dir,
        workdir_base=workdir_base,
        run_mode="overwrite",
        extra_metadata=build_experiment_metadata(
            experiment_name="defects4j_lang1_llm_smoke",
            mutant_source="llm",
            model_name="qwen2.5-coder:7b",
            model_provider="ollama",
            prompt_name="line_edit_prompt",
            prompt_version="v1",
            temperature=0.0,
            n_requested_mutants=1,
            generation_mode="batch_once",
            dataset_split=None,
            notes="First end-to-end LLM mutant generation test on Defects4J Lang_1 createNumber.",
        ),
        cleanup_tmp=True,
        validate_after_run=True,
        rebuild_index=True,
    )

    # runner.run(..., run_mode="overwrite") recreates the run directory,
    # so save generation artifacts again after the run to keep them with the run.
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
