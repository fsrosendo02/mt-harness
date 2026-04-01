from dataclasses import dataclass
import time
from datetime import datetime

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def log_duration(label: str, start: float) -> None:
    log(f"{label} finished in {time.time() - start:.2f}s")


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
        log("[baseline] build start")
        t = time.time()
        build_ok, build_log = self.adapter.build(workdir)
        log_duration("[baseline] build", t)

        if not build_ok:
            log("[baseline] build failed")
            return BaselineResult(
                build_ok=False,
                test_ok=False,
                build_log=build_log,
                test_log="NOT_RUN",
            )

        log("[baseline] test start")
        t = time.time()
        test_ok, test_log = self.adapter.test(workdir)
        log_duration("[baseline] test", t)

        return BaselineResult(
            build_ok=True,
            test_ok=test_ok,
            build_log=build_log,
            test_log=test_log,
        )