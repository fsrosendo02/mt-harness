from abc import ABC, abstractmethod
from harness.models import Subject, Target


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

    @abstractmethod
    def apply_mutant(self, workdir: str, target: Target, mutant_code: str) -> None:
        pass

    @abstractmethod
    def reset_subject(self, workdir: str) -> None:
        pass
