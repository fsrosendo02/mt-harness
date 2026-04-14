from harness.llm.providers.base import LLMProvider


class OllamaApiProvider(LLMProvider):
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

        try:
            import ollama

            result = ollama.generate(model=self.model_name, prompt=prompt, stream=False)
        except Exception as e:
            raise RuntimeError(
                f"Ollama call failed: {e}"
            )

        return result['response'].strip()

   
    def _build_prompt(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
    ) -> str:
        if system_prompt:
            return system_prompt.rstrip() + "\n\n" + user_prompt.lstrip()
        return user_prompt
