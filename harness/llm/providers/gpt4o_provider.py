import subprocess
import tempfile
import os

class GPT4oProvider:
    def __init__(self, model=None, timeout_seconds=180):
        self.model = model
        self.timeout = timeout_seconds
        self.binary = "gpt_run"

    def generate(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
        prompt_path = None

        try:
            full_prompt = ""
            if system_prompt:
                full_prompt += system_prompt.strip() + "\n\n"
            full_prompt += user_prompt.strip()

            with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
                f.write(full_prompt)
                prompt_path = f.name

            result = subprocess.run(
                [self.binary, prompt_path],
                capture_output=True,
                text=True,
                timeout=self.timeout
            )

            if result.returncode != 0:
                raise RuntimeError(f"GPT4o_ERROR: {result.stderr}")

            output = result.stdout.strip()
            if not output:
                return "{}"

            return output

        except subprocess.TimeoutExpired:
            raise RuntimeError("GPT4o_TIMEOUT")

        finally:
            if prompt_path and os.path.exists(prompt_path):
                os.remove(prompt_path)