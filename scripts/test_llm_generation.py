from pathlib import Path

from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.providers.ollama_api_provider import OllamaApiProvider
from harness.models import Subject, Target


def main():
    subject = Subject(
        dataset="defects4j",
        subject_id="Lang_1",
        language="java",
    )

    target = Target(
        file_path="src/main/java/org/apache/commons/lang3/StringUtils.java",
        function_name="isAlpha",
        start_line=1,
        end_line=12,
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

    provider = OllamaApiProvider("qwen2.5-coder:7b")
    generator = LLMMutantGenerator(provider=provider)

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

    artifact_dir = Path("tmp/llm_generation_smoke")
    save_generation_artifacts(
        output_dir=artifact_dir,
        system_prompt=built_prompt.system_prompt,
        user_prompt=built_prompt.user_prompt,
        raw_response=raw_text,
        mutants=mutants,
    )

    print("=== RAW RESPONSE ===")
    print(raw_text)
    print()

    print("=== PARSED MUTANTS ===")
    print("count =", len(mutants))
    for m in mutants:
        print(f"- {m.mutant_id} ({m.source})")
        print(m.code)
        print("---")

    print()
    print("Artifacts saved to:", artifact_dir)


if __name__ == "__main__":
    main()
