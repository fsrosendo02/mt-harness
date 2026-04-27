from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target, TestObservation, TestRunResult


class MockAdapter(BenchmarkAdapter):
    def checkout_subject(self, subject: Subject, workdir: str) -> None:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        self.reset_subject(workdir)

    def build(self, workdir: str) -> tuple[bool, str]:
        source_file = Path(workdir) / "mock_source.java"
        content = source_file.read_text(encoding="utf-8")

        if "SYNTAX_ERROR" in content:
            return False, "Mock build failed: syntax error detected"

        return True, "Mock build succeeded"

    def test(self, workdir: str) -> tuple[bool, str]:
        source_file = Path(workdir) / "mock_source.java"
        content = source_file.read_text(encoding="utf-8")

        # Null-handling mutant: changed baseline guard to return true
        if "if (str == null) {" in content and "return true;" in content:
            null_block = """if (str == null) {
        return true;"""
            if null_block in content:
                return False, "Mock test failed: mutant killed (null handling)"

        # Character-check mutant: accepts digits instead of rejecting non-letters
        if "Character.isDigit(str.charAt(i))" in content:
            return False, "Mock test failed: mutant killed (digit handling)"

        return True, "Mock tests passed: mutant survived"

    def test_target(self, workdir: str, eligible_tests: list[str]) -> TestRunResult:
        test_ok, log = self.test(workdir)
        observations: list[TestObservation] = []

        failing_tests: set[str] = set()
        if not test_ok and eligible_tests:
            failing_tests.add(eligible_tests[0])

        for index, test_name in enumerate(eligible_tests, start=1):
            observations.append(
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=True,
                    outcome="FAIL" if test_name in failing_tests else "PASS",
                    duration_ms=None,
                    failure_type="ASSERTION" if test_name in failing_tests else None,
                    message=log if test_name in failing_tests else None,
                    execution_index=index,
                )
            )

        return TestRunResult(success=test_ok, log=log, observations=observations)

    def apply_mutant(self, workdir: str, target: Target, mutant_code: str) -> None:
        source_file = Path(workdir) / "mock_source.java"
        source_file.write_text(mutant_code, encoding="utf-8")

    def reset_subject(self, workdir: str) -> None:
        source_file = Path(workdir) / "mock_source.java"
        source_file.write_text(
            """
public static boolean isAlpha(String str) {
    if (str == null) {
        return false;
    }
    int sz = str.length();
    for (int i = 0; i < sz; i++) {
        if (!Character.isLetter(str.charAt(i))) {
            return false;
        }
    }
    return true;
}
""".strip() + "\n",
            encoding="utf-8",
        )
