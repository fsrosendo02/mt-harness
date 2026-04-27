import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
import re

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target, TestObservation, TestRunResult


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

    def _parse_failing_tests(self, log_output: str) -> list[str]:
        failing_count_seen = False
        failing_tests: list[str] = []

        for line in log_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("Failing tests:"):
                failing_count_seen = True
                continue

            if not failing_count_seen:
                continue

            match = re.match(r"^-\s+(.+?::.+?)\s*$", stripped)
            if match:
                failing_tests.append(match.group(1).strip())
                continue

            if failing_tests and stripped and not stripped.startswith("-"):
                break

        return failing_tests

    def test_target(self, workdir: str, eligible_tests: list[str]) -> TestRunResult:
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
            message = f"TIMEOUT after {self.TEST_TIMEOUT}s\n{e}"
            observations = [
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=False,
                    outcome="NOT_RUN",
                    duration_ms=None,
                    failure_type="SUITE_TIMEOUT",
                    message=message,
                    execution_index=index,
                )
                for index, test_name in enumerate(eligible_tests, start=1)
            ]
            return TestRunResult(success=False, log=message, observations=observations)

        log(f"[test] finished in {time.time() - t:.2f}s")

        log_output = result.stdout + "\n" + result.stderr
        failing_tests = set(self._parse_failing_tests(log_output))

        failing_count = None
        for line in log_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("Failing tests:"):
                try:
                    failing_count = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    failing_count = None
                break

        if failing_count is None and result.returncode != 0:
            observations = [
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=False,
                    outcome="NOT_RUN",
                    duration_ms=None,
                    failure_type="SUITE_ERROR",
                    message="defects4j test failed before producing per-test results",
                    execution_index=index,
                )
                for index, test_name in enumerate(eligible_tests, start=1)
            ]
            return TestRunResult(success=False, log=log_output, observations=observations)

        observations = []
        for index, test_name in enumerate(eligible_tests, start=1):
            failed = test_name in failing_tests
            observations.append(
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=True,
                    outcome="FAIL" if failed else "PASS",
                    duration_ms=None,
                    failure_type="TEST_FAILURE" if failed else None,
                    message="Observed in defects4j failing-tests list" if failed else None,
                    execution_index=index,
                )
            )

        success = failing_count == 0 if failing_count is not None else result.returncode == 0
        return TestRunResult(success=success, log=log_output, observations=observations)

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
