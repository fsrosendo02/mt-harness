from dataclasses import dataclass

from harness.models import Subject, Target


@dataclass
class BuiltPrompt:
    system_prompt: str | None
    user_prompt: str
    target_code: str


def _add_line_numbers(code: str) -> str:
    lines = code.rstrip("\n").splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{i:>{width}}: {line}" for i, line in enumerate(lines, start=1))


class PromptBuilder:
    def build(
        self,
        *,
        subject: Subject,
        target: Target,
        target_code: str,
        context_code: str | None,
        num_mutants: int,
    ) -> BuiltPrompt:
        system_prompt = (
            "Return valid JSON only. "
            "Do not include markdown fences. "
            "Do not include any text outside the JSON."
        )

        numbered_method = _add_line_numbers(target_code)

        user_prompt = f"""You are an expert in software mutation testing.

Below is a Java method. Before generating mutants, reason briefly about
its behavior: identify the key computational properties and boundary
conditions that a test should verify.

Method:
{numbered_method}

Then, generate exactly {num_mutants} mutants. Each mutant must:
- Violate a distinct behavioral property identified in your reasoning
- Change executable code (not only comments)
- Compile successfully

The "line" field must refer to the numbered method lines shown above.
The "precode" field must match the original line content without the numeric prefix.

Output format (JSON):
{{
  "mutants": [
    {{
      "id": 1,
      "reasoning": "<one sentence: which behavioral property is violated>",
      "line": <line number>,
      "precode": "<original line>",
      "aftercode": "<mutated line>"
    }}
  ]
}}
"""
        return BuiltPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            target_code=target_code,
        )
