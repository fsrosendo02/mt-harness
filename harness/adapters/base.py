from abc import ABC, abstractmethod
from harness.models import Subject, Target, TestRunResult


class BenchmarkAdapter(ABC):
    @abstractmethod
    def checkout_subject(self, subject: Subject, workdir: str) -> None:
        pass

    @abstractmethod
    def build(self, workdir: str) -> tuple[bool, str]:
        pass

    @abstractmethod
    def test(self, workdir: str) -> tuple[bool, str]:
        pass

    def test_target(self, workdir: str, eligible_tests: list[str]) -> TestRunResult:
        raise NotImplementedError(
            f"{self.__class__.__name__} does not provide structured per-test results"
        )

    @abstractmethod
    def apply_mutant(self, workdir: str, target: Target, mutant_code: str) -> None:
        pass

    @abstractmethod
    def reset_subject(self, workdir: str) -> None:
        pass
