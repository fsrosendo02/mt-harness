import os
import signal
import subprocess
import time
import ollama

from harness.llm.providers.base import LLMProvider


class OllamaApiProvider(LLMProvider):
    def __init__(self, model_name: str, timeout_seconds: int = 120):
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def _get_descendants(self, root_pid: int) -> list[int]:
        """Return all descendant PIDs of root_pid."""
        try:
            result = subprocess.run(
                ["ps", "-e", "-o", "pid=", "-o", "ppid="],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            return []

        children_by_parent: dict[int, list[int]] = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            children_by_parent.setdefault(ppid, []).append(pid)

        descendants = []
        stack = [root_pid]
        seen = set()

        while stack:
            parent = stack.pop()
            for child in children_by_parent.get(parent, []):
                if child not in seen:
                    seen.add(child)
                    descendants.append(child)
                    stack.append(child)

        return descendants

    def _kill_process_tree(self, pid: int) -> None:
        """Best-effort kill of pid and all descendants."""
        descendants = self._get_descendants(pid)

        # Kill descendants first, deepest last is not critical with SIGKILL.
        for child_pid in reversed(descendants):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except Exception:
                pass

        # Then try the process group
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass

        # Then the root process directly
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

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
