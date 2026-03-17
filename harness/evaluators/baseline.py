from dataclasses import dataclass

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject


@dataclass
class BaselineResult:
    build_ok: bool
    test_ok: bool
    build_log: str
    test_log: str


class BaselineEvaluator:
    def __init__(self, adapter: BenchmarkAdapter):
        self.adapter = adapter

    def evaluate(self, subject: Subject, workdir: str) -> BaselineResult:
        build_ok, build_log = self.adapter.build(workdir)

        if not build_ok:
            return BaselineResult(
                build_ok=False,
                test_ok=False,
                build_log=build_log,
                test_log="NOT_RUN",
            )

        test_ok, test_log = self.adapter.test(workdir)

        return BaselineResult(
            build_ok=True,
            test_ok=test_ok,
            build_log=build_log,
            test_log=test_log,
        )
