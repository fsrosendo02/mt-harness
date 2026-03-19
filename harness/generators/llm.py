from harness.generators.base import MutantGenerator
from harness.llm.parsing import LLMResponseParser
from harness.llm.prompt_builder import PromptBuilder
from harness.models import Subject, Target, Mutant


class LLMMutantGenerator(MutantGenerator):
    def __init__(
        self,
        *,
        provider,
        prompt_builder: PromptBuilder | None = None,
        parser: LLMResponseParser | None = None,
    ):
        self.provider = provider
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.parser = parser or LLMResponseParser()

    def generate(
        self,
        *,
        subject: Subject,
        target: Target,
        target_code: str,
        context_code: str | None = None,
        num_mutants: int = 5,
        temperature: float = 0.2,
    ) -> tuple[list[Mutant], str]:
        built_prompt = self.prompt_builder.build(
            subject=subject,
            target=target,
            target_code=target_code,
            context_code=context_code,
            num_mutants=num_mutants,
        )

        raw_text = self.provider.generate(
            system_prompt=built_prompt.system_prompt,
            user_prompt=built_prompt.user_prompt,
            temperature=temperature,
        )

        mutants = self.parser.parse(
            raw_text=raw_text,
            requested_count=num_mutants,
            original_target_code=built_prompt.target_code,
        )

        return mutants, raw_text
