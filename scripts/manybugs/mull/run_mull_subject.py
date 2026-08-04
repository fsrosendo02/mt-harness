#!/usr/bin/env python3
"""Fase 2 of the mull baseline pipeline (see mull_baseline_plan.md).

Given a ManyBugs subject_id + target (file/line range/target_id), runs a
real mull mutation campaign scoped to that target, one mull-runner
invocation per eligible test_id (not one combined run — see "Correção
quanto à granularidade por teste" in the plan), and saves the raw SQLite
report per test_id for Fase 3 to normalize/aggregate.

Critical gotcha this script exists to avoid repeating (see "Achado crítico"
in the plan): the ManyBugs `test.sh <id> dummy` entry point routes through
`gzip-run-tests.pl` -> `make <test>.log` (Autotools/Automake). That chain
silently breaks mull's mutant-selection signal — every mutant reports
Survived regardless of what changed, with no error. The fix is to invoke
the underlying Autotools test *script* directly (e.g. `bash tests/hufts`),
bypassing test.sh/perl/make entirely. This script resolves test_id -> test
script name by parsing test.sh and the project's *-run-tests.pl the same
way ManyBugsAdapter._discover_test_ids does, then builds a direct wrapper.

This has only been validated for `gzip`. Confirm the test.sh/make bug and
the test_id -> script-name resolution before reusing for other families.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness.models import Subject, Target
from harness.targets.target_tests import load_target_test_map, target_tests_for

from prepare_mull_checkout import prepare_mull_checkout, MullCheckout, _exec

DEFAULT_MUTATORS = [
    "cxx_add_to_sub",
    "cxx_sub_to_add",
    "cxx_lt_to_le",
    "cxx_le_to_lt",
    "cxx_eq_to_ne",
    "cxx_ne_to_eq",
]
DEFAULT_TIMEOUT_MS = 120_000


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


@dataclass
class TestRunReport:
    test_id: str
    script_name: str
    exit_code: int
    sqlite_host_path: str


def resolve_test_script_name(container_id: str, test_id: str) -> str:
    """Map a ManyBugs test_id (e.g. "n1") to the underlying Autotools test
    script name (e.g. "hufts"), by parsing test.sh + the project's
    *-run-tests.pl the same way ManyBugsAdapter._discover_test_ids does.
    """
    test_sh = subprocess.run(
        ["docker", "exec", container_id, "cat", "/experiment/test.sh"],
        capture_output=True, text=True,
    ).stdout
    m = re.search(rf"^\s*{re.escape(test_id)}\)\s*run_test\s+(\d+)", test_sh, re.MULTILINE)
    if not m:
        raise ValueError(f"test_id '{test_id}' not found in /experiment/test.sh")
    run_test_n = int(m.group(1))

    find_pl = subprocess.run(
        ["docker", "exec", container_id, "bash", "-lc", "ls /experiment/*-run-tests.pl"],
        capture_output=True, text=True,
    ).stdout.strip().splitlines()
    if not find_pl:
        raise ValueError("No *-run-tests.pl found under /experiment")
    pl_content = subprocess.run(
        ["docker", "exec", container_id, "cat", find_pl[0]],
        capture_output=True, text=True,
    ).stdout
    tests_match = re.search(r"@tests\s*=\s*\((.*?)\);", pl_content, re.DOTALL)
    if not tests_match:
        raise ValueError(f"Could not find @tests array in {find_pl[0]}")
    names = re.findall(r'"([^"]+)"', tests_match.group(1))
    if run_test_n < 1 or run_test_n > len(names):
        raise ValueError(f"run_test index {run_test_n} out of range for @tests ({len(names)} entries)")
    log_name = names[run_test_n - 1]  # e.g. "hufts.log"
    return log_name[:-4] if log_name.endswith(".log") else log_name


def write_direct_wrapper(container_id: str, script_name: str, wrapper_path: str) -> None:
    """Write a --test-program wrapper that invokes the Autotools test
    script directly, bypassing test.sh/gzip-run-tests.pl/make (see module
    docstring). Cleans up known stray artifacts some test scripts leave in
    tests/ (their own gt-<name>.XXXX tmpdirs self-clean via init.sh).
    """
    script = (
        "#!/bin/bash\n"
        "cd /experiment/src/tests\n"
        f"rm -f {script_name}.log out err exp k\n"
        f"bash ./{script_name}\n"
        "exit $?\n"
    )
    _exec(
        _FakeContainer(container_id),
        ["bash", "-lc", f"cat > {wrapper_path} <<'MULL_WRAPPER_EOF'\n{script}MULL_WRAPPER_EOF\nchmod +x {wrapper_path}"],
    )


class _FakeContainer:
    """_exec() (imported from prepare_mull_checkout) only needs .id."""
    def __init__(self, container_id: str):
        self.id = container_id


def run_mull_for_test(
    container_id: str,
    src_dir: str,
    test_id: str,
    script_name: str,
    *,
    timeout_ms: int,
    binary_name: str = "gzip",
) -> TestRunReport:
    wrapper_path = f"/tmp/mull_wrapper_{test_id}.sh"
    write_direct_wrapper(container_id, script_name, wrapper_path)

    report_dir = "/experiment/mull_reports"
    cmd = (
        f"mkdir -p {report_dir} && cd {src_dir} && "
        f"mull-runner-9 {binary_name} --test-program={wrapper_path} "
        f"--reporters=SQLite --report-name={test_id} --report-dir={report_dir} "
        f"--timeout={timeout_ms}"
    )
    result = _exec(_FakeContainer(container_id), ["bash", "-lc", cmd])
    if result["exit_code"] != 0:
        log(f"[mull] WARNING: mull-runner exited {result['exit_code']} for test_id={test_id}")
        log(result["output"][-2000:])

    return TestRunReport(
        test_id=test_id,
        script_name=script_name,
        exit_code=result["exit_code"],
        sqlite_host_path="",  # filled by caller after docker cp
    )


def write_mull_yml(container_id: str, src_dir: str, mutators: list[str]) -> None:
    yml = "mutators:\n" + "\n".join(f"  - {m}" for m in mutators) + "\nquiet: false\n"
    _exec(
        _FakeContainer(container_id),
        ["bash", "-lc", f"cat > {src_dir}/mull.yml <<'MULL_YML_EOF'\n{yml}MULL_YML_EOF"],
    )


def run_mull_subject(
    *,
    subject_id: str,
    target_id: str,
    target_file: str,
    start_line: int,
    end_line: int,
    catalog_file: str | None,
    mutators: list[str],
    timeout_ms: int,
    report_dir: Path,
    binary_name: str = "gzip",
) -> dict:
    subject = Subject(dataset="manybugs", subject_id=subject_id, language="c")
    target = Target(
        file_path=target_file, function_name="", start_line=start_line,
        end_line=end_line, language="c", target_id=target_id,
    )
    test_map = load_target_test_map(catalog_file=catalog_file) if catalog_file else load_target_test_map()
    eligible_tests = target_tests_for(subject, target, test_map)
    if not eligible_tests:
        raise ValueError(
            f"No eligible tests found for {subject_id}/{target_id} "
            f"(catalog_file={catalog_file}) — check target_tests.csv"
        )
    log(f"[targets] eligible tests: {eligible_tests}")

    checkout: MullCheckout = prepare_mull_checkout(subject_id)
    if not checkout.build_ok or not checkout.mutants_embedded:
        subprocess.run(["docker", "rm", "-f", checkout.container_id], capture_output=True, text=True)
        raise RuntimeError(f"Checkout not qualified for mull: {checkout}")

    report_dir.mkdir(parents=True, exist_ok=True)
    per_test_reports = []
    try:
        write_mull_yml(checkout.container_id, checkout.source_dir, mutators)
        for test_id in eligible_tests:
            script_name = resolve_test_script_name(checkout.container_id, test_id)
            log(f"[mull] running test_id={test_id} (script={script_name})")
            report = run_mull_for_test(
                checkout.container_id, checkout.source_dir, test_id, script_name,
                timeout_ms=timeout_ms, binary_name=binary_name,
            )
            host_sqlite = report_dir / f"{target_id}__{test_id}.sqlite"
            cp = subprocess.run(
                ["docker", "cp", f"{checkout.container_id}:/experiment/mull_reports/{test_id}.sqlite", str(host_sqlite)],
                capture_output=True, text=True,
            )
            if cp.returncode != 0:
                log(f"[mull] WARNING: failed to copy report for {test_id}: {cp.stderr}")
            else:
                report.sqlite_host_path = str(host_sqlite)
            per_test_reports.append(report)
    finally:
        # Always remove the container, even if a test run raised — leaving
        # dangling containers around was a real bug found in the Fase 4
        # batch run (a build-time failure earlier in this function already
        # cleans up separately; this covers failures during the test loop).
        subprocess.run(["docker", "rm", "-f", checkout.container_id], capture_output=True, text=True)

    return {
        "subject_id": subject_id,
        "target_id": target_id,
        "target_file": target_file,
        "start_line": start_line,
        "end_line": end_line,
        "eligible_tests": eligible_tests,
        "mutators": mutators,
        "reports": [asdict(r) for r in per_test_reports],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--target-file", required=True, help="e.g. inflate.c")
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--end-line", type=int, required=True)
    parser.add_argument("--catalog-file", help="Passed through to load_target_test_map()")
    parser.add_argument("--mutators", nargs="*", default=DEFAULT_MUTATORS)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--report-dir", default="harness/executions/c/mull/execution/adhoc/raw")
    parser.add_argument("--binary-name", default="gzip")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_mull_subject(
        subject_id=args.subject_id,
        target_id=args.target_id,
        target_file=args.target_file,
        start_line=args.start_line,
        end_line=args.end_line,
        catalog_file=args.catalog_file,
        mutators=args.mutators,
        timeout_ms=args.timeout_ms,
        report_dir=Path(args.report_dir),
        binary_name=args.binary_name,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
