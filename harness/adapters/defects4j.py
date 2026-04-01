import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


class Defects4JAdapter(BenchmarkAdapter):

    BUILD_TIMEOUT = 120
    TEST_TIMEOUT = 300

    def checkout_subject(self, subject: Subject, workdir: str) -> None:
        project, bug_id = subject.subject_id.split("_", 1)
        version = f"{bug_id}{subject.version}"

        workdir_path = Path(workdir).resolve()

        if workdir_path.exists():
            shutil.rmtree(workdir_path)

        workdir_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "defects4j",
            "checkout",
            "-p", project,
            "-v", version,
            "-w", str(workdir_path),
        ]

        subprocess.run(cmd, check=True)

    def build(self, workdir: str) -> tuple[bool, str]:
        log("[build] start")
        t = time.time()

        try:
            result = subprocess.run(
                ["defects4j", "compile"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.BUILD_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            log("[build] TIMEOUT")
            return False, f"TIMEOUT after {self.BUILD_TIMEOUT}s\n{e}"

        log(f"[build] finished in {time.time() - t:.2f}s")

        log_output = result.stdout + "\n" + result.stderr
        return result.returncode == 0, log_output

    def test(self, workdir: str) -> tuple[bool, str]:
        log("[test] start")
        t = time.time()

        try:
            result = subprocess.run(
                ["defects4j", "test"],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.TEST_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            log("[test] TIMEOUT")
            return False, f"TIMEOUT after {self.TEST_TIMEOUT}s\n{e}"

        log(f"[test] finished in {time.time() - t:.2f}s")

        log_output = result.stdout + "\n" + result.stderr

        failing_tests = None
        for line in log_output.splitlines():
            line = line.strip()
            if line.startswith("Failing tests:"):
                try:
                    failing_tests = int(line.split(":", 1)[1].strip())
                except ValueError:
                    failing_tests = None
                break

        if failing_tests is not None:
            return failing_tests == 0, log_output

        return result.returncode == 0, log_output

    def apply_mutant(self, workdir: str, target: Target, mutant_code: str) -> None:
        file_path = Path(workdir) / target.file_path

        original = file_path.read_text(encoding="utf-8")
        lines = original.splitlines()

        new_lines = (
            lines[: target.start_line - 1]
            + mutant_code.splitlines()
            + lines[target.end_line :]
        )

        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def reset_subject(self, workdir: str) -> None:
        raise NotImplementedError("Reset not implemented for Defects4JAdapter yet.")