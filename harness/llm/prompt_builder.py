from dataclasses import dataclass
from pathlib import Path

from harness.models import Subject, Target


@dataclass
class BuiltPrompt:
    system_prompt: str | None
    user_prompt: str
    target_code: str
    prompt_file: str


def _add_line_numbers(code: str) -> str:
    lines = code.rstrip("\n").splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{i:>{width}}: {line}" for i, line in enumerate(lines, start=1))


class PromptBuilder:
    def __init__(self, prompt_file: str):
        self.prompt_file = prompt_file

    def build(
        self,
        *,
        subject: Subject,
        target: Target,
        target_code: str,
        context_code: str | None,
        num_mutants: int,
    ) -> BuiltPrompt:
        template = Path(self.prompt_file).read_text(encoding="utf-8")

        replacements = {
            "{WHOLEJAVAMETHOD}": target_code.rstrip(),
            "{WHOLEJAVAMETHOD_NUMBERED}": _add_line_numbers(target_code),
            "{MUTNUMBER}": str(num_mutants),
            "{DATASET}": str(subject.dataset),
            "{SUBJECT_ID}": str(subject.subject_id),
            "{FUNCTION_NAME}": str(target.function_name),
            "{FILE_PATH}": str(target.file_path),
            "{START_LINE}": str(target.start_line),
            "{END_LINE}": str(target.end_line),
        }

        prompt_text = template
        for placeholder, value in replacements.items():
            prompt_text = prompt_text.replace(placeholder, value)

        return BuiltPrompt(
            system_prompt=None,
            user_prompt=prompt_text,
            target_code=target_code,
            prompt_file=self.prompt_file,
        )
