import shutil
import subprocess
from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target


class Defects4JAdapter(BenchmarkAdapter):

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
        result = subprocess.run(
            ["defects4j", "compile"],
            cwd=workdir,
            capture_output=True,
            text=True,
        )

        log = result.stdout + "\n" + result.stderr
        return result.returncode == 0, log

    def test(self, workdir: str) -> tuple[bool, str]:
        result = subprocess.run(
            ["defects4j", "test"],
            cwd=workdir,
            capture_output=True,
            text=True,
        )

        log = result.stdout + "\n" + result.stderr

        failing_tests = None
        for line in log.splitlines():
            line = line.strip()
            if line.startswith("Failing tests:"):
                try:
                    failing_tests = int(line.split(":", 1)[1].strip())
                except ValueError:
                    failing_tests = None
                break

        if failing_tests is not None:
            return failing_tests == 0, log

        return result.returncode == 0, log

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