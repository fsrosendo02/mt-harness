from abc import ABC, abstractmethod
from harness.models import Subject, Target, Mutant


class MutantGenerator(ABC):
    @abstractmethod
    def generate(self, subject: Subject, target: Target, num_mutants: int) -> list[Mutant]:
        pass
