import subprocess

from harness.llm.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model_name: str, timeout_seconds: int = 120):
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        prompt = self._build_prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        cmd = [
            "ollama",
            "run",
            self.model_name,
            prompt,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as e:
            raise RuntimeError("ollama command not found in PATH") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Ollama call timed out after {self.timeout_seconds} seconds "
                f"for model '{self.model_name}'"
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Ollama call failed with exit code {e.returncode}: {e.stderr.strip()}"
            ) from e

        return result.stdout.strip()

    def _build_prompt(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
    ) -> str:
        if system_prompt:
            return system_prompt.rstrip() + "\n\n" + user_prompt.lstrip()
        return user_prompt
