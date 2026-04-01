import os
import signal
import subprocess
import tempfile
import time

from harness.llm.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, model_name: str | None = None, timeout_seconds: int = 180):
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.binary = "gemini_run"

    def _get_descendants(self, root_pid: int) -> list[int]:
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
        descendants = self._get_descendants(pid)

        for child_pid in reversed(descendants):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except Exception:
                pass

        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass

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
        full_prompt = ""
        if system_prompt:
            full_prompt += system_prompt.strip() + "\n\n"
        full_prompt += user_prompt.strip()

        prompt_path = None
        proc = None

        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
                f.write(full_prompt)
                prompt_path = f.name

            proc = subprocess.Popen(
                [self.binary, prompt_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

            try:
                stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self._kill_process_tree(proc.pid)
                time.sleep(0.2)
                try:
                    proc.communicate(timeout=1)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Gemini call timed out after {self.timeout_seconds} seconds"
                )
            except KeyboardInterrupt:
                self._kill_process_tree(proc.pid)
                raise

            if proc.returncode != 0:
                raise RuntimeError(
                    f"Gemini call failed with exit code {proc.returncode}: {stderr.strip()}"
                )

            return stdout.strip()

        except FileNotFoundError as e:
            raise RuntimeError("gemini_run command not found in PATH") from e

        finally:
            if prompt_path and os.path.exists(prompt_path):
                try:
                    os.remove(prompt_path)
                except OSError:
                    pass