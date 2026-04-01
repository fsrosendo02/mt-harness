import os
import signal
import subprocess
import time

from harness.llm.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
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

        cmd = ["ollama", "run", self.model_name]
        proc = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

            try:
                stdout, stderr = proc.communicate(
                    input=prompt,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                self._kill_process_tree(proc.pid)
                time.sleep(0.2)

                try:
                    proc.communicate(timeout=1)
                except Exception:
                    pass

                raise RuntimeError(
                    f"Ollama call timed out after {self.timeout_seconds} seconds "
                    f"for model '{self.model_name}'"
                )
            except KeyboardInterrupt:
                self._kill_process_tree(proc.pid)
                raise

            if proc.returncode != 0:
                raise RuntimeError(
                    f"Ollama call failed with exit code {proc.returncode}: {stderr.strip()}"
                )

            return stdout.strip()

        except FileNotFoundError as e:
            raise RuntimeError("ollama command not found in PATH") from e

    def _build_prompt(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
    ) -> str:
        if system_prompt:
            return system_prompt.rstrip() + "\n\n" + user_prompt.lstrip()
        return user_prompt