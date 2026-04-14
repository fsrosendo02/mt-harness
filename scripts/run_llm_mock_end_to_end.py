from pathlib import Path

from harness.adapters.mock import MockAdapter
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.providers.ollama_api_provider import OllamaApiProvider
from harness.models import Subject, Target
from harness.runners.mutation_runner import MutationRunner


def main():
    subject = Subject(
        dataset="mock",
        subject_id="Mock_1",
        language="java",
    )

    target = Target(
        file_path="mock_source.java",
        function_name="isAlpha",
        start_line=1,
        end_line=11,
    )

    target_code = """public static boolean isAlpha(String str) {
    if (str == null) {
        return false;
    }
    int sz = str.length();
    for (int i = 0; i < sz; i++) {
        if (!Character.isLetter(str.charAt(i))) {
            return false;
        }
    }
    return true;
}"""

    provider = OllamaApiProvider("qwen2.5-coder:7b", timeout_seconds=120)
    generator = LLMMutantGenerator(provider=provider)
    adapter = MockAdapter()
    runner = MutationRunner(adapter)

    built_prompt = generator.prompt_builder.build(
        subject=subject,
        target=target,
        target_code=target_code,
        context_code=None,
        num_mutants=2,
    )

    mutants, raw_text = generator.generate(
        subject=subject,
        target=target,
        target_code=target_code,
        context_code=None,
        num_mutants=2,
        temperature=0.0,
    )

    generation_dir = Path("tmp/llm_mock_generation")
    save_generation_artifacts(
        output_dir=generation_dir,
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        raw_response=raw_text,
        mutants=mutants,
    )

    print("Generated mutants:", len(mutants))
    for m in mutants:
        print(f"- {m.mutant_id}")

    if not mutants:
        raise RuntimeError("No valid mutants were parsed; aborting runner smoke test.")

    run_dir = "harness/runs/run_mock_llm_smoke"
    workdir_base = "tmp/mock_llm_subject"

    runner.run(
        subject=subject,
        target=target,
        mutants=mutants,
        run_dir=run_dir,
        workdir_base=workdir_base,
        run_mode="overwrite",
        extra_metadata={
            "experiment_name": "mock_llm_smoke",
            "mutant_source": "llm",
            "model_name": "qwen2.5-coder:7b",
            "model_provider": "ollama",
            "prompt_name": "line_edit_prompt",
            "prompt_version": "v1",
            "temperature": 0.0,
            "n_requested_mutants": 2,
            "generation_mode": "batch_once",
            "dataset_split": "n/a",
            "notes": "Smoke test for LLM -> parser -> runner integration on mock adapter",
        },
        cleanup_tmp=True,
        validate_after_run=True,
        rebuild_index=True,
    )

    print()
    print("Run completed.")
    print("Generation artifacts:", generation_dir)
    print("Run directory:", run_dir)


if __name__ == "__main__":
    main()
