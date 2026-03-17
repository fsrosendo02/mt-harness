from pathlib import Path

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target


class MockAdapter(BenchmarkAdapter):
    def checkout_subject(self, subject: Subject, workdir: str) -> None:
        Path(workdir).mkdir(parents=True, exist_ok=True)

    def build(self, workdir: str) -> tuple[bool, str]:
        source_file = Path(workdir) / "mock_source.java"
        content = source_file.read_text(encoding="utf-8")

        if "SYNTAX_ERROR" in content:
            return False, "Mock build failed: syntax error detected"

        return True, "Mock build succeeded"

    def test(self, workdir: str) -> tuple[bool, str]:
        source_file = Path(workdir) / "mock_source.java"
        content = source_file.read_text(encoding="utf-8")

        if "return true;" in content and "str == null" in content:
            return False, "Mock test failed: mutant killed"

        return True, "Mock tests passed: mutant survived"

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
