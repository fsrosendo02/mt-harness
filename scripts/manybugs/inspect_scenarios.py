"""
Inspect ManyBugs Docker scenarios and emit catalog + target_tests rows.

Usage:
    python scripts/manybugs/inspect_scenarios.py <scenario1> [<scenario2> ...]

For each scenario:
  - Creates a container (linux/amd64)
  - Reads bug-info/fault.lines
  - Finds the enclosing function (name, start_line, end_line) via awk
  - Discovers oracle/positive test IDs from test.sh
  - Prints JSON catalog entry + CSV rows
"""
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

IMAGE_REGISTRY = "prosyslab/manybugs"
PLATFORM = "linux/amd64"
SRC_DIR = "/experiment/src"
TEST_SCRIPT = "/experiment/test.sh"


@dataclass
class ScenarioInfo:
    scenario: str
    fault_file: str
    fault_lines: list[int]
    function_name: str
    start_line: int
    end_line: int
    oracle_tests: list[str]
    positive_tests: list[str]
    notes: str = ""
    error: str = ""


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def docker_exec(cid: str, cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    return run(["docker", "exec", cid] + cmd, timeout=timeout)


def start_container(image: str) -> str:
    rc, out = run(["docker", "create", "--platform", PLATFORM, image, "tail", "-f", "/dev/null"])
    if rc != 0:
        raise RuntimeError(f"docker create failed: {out}")
    cid = out.strip()
    rc, out = run(["docker", "start", cid])
    if rc != 0:
        raise RuntimeError(f"docker start failed: {out}")
    return cid


def stop_container(cid: str):
    run(["docker", "stop", "-t", "3", cid])
    run(["docker", "rm", "-f", cid])


def read_fault_lines(cid: str) -> tuple[str, list[int]]:
    """Returns (relative_file_path, [line_numbers])"""
    rc, out = docker_exec(cid, ["cat", "/experiment/bug-info/fault.lines"])
    if rc != 0 or not out:
        raise RuntimeError(f"Could not read fault.lines: {out}")
    # Format: path/to/file.c,linenum,score
    entries = [line.strip() for line in out.splitlines() if line.strip()]
    if not entries:
        raise RuntimeError("fault.lines is empty")
    # Group by file (take the first file if multiple)
    first_file = entries[0].split(",")[0]
    lines = []
    for entry in entries:
        parts = entry.split(",")
        if parts[0] == first_file:
            lines.append(int(parts[1]))
    return first_file, sorted(lines)


def find_enclosing_function(cid: str, src_file: str, fault_line: int) -> tuple[str, int, int]:
    """
    Returns (function_name, start_line, end_line).
    Uses awk: tracks function-like lines (^[A-Za-z_]) as potential starts,
    then once past fault_line finds the closing }.
    """
    full_path = f"{SRC_DIR}/{src_file}"

    awk_script = r"""
BEGIN { fn_start=0; fn_name=""; found=0 }
/^[A-Za-z_]/ && !/^#/ {
    fn_start = NR
    fn_name = $0
    brace_depth = 0
    found = 0
}
{ if (/{/) brace_depth++ }
{ if (/}/) brace_depth-- }
NR == """ + str(fault_line) + r""" { found = 1 }
found && brace_depth == 0 && /}/ {
    print fn_name "|" fn_start "|" NR
    exit
}
"""
    rc, out = docker_exec(cid, ["awk", awk_script, full_path], timeout=30)
    if rc != 0 or "|" not in out:
        raise RuntimeError(f"awk function finder failed: {out!r}")

    # Take the last line (most specific match)
    last = [l for l in out.splitlines() if "|" in l][-1]
    parts = last.strip().split("|")
    raw_name = parts[0].strip()
    start = int(parts[1])
    end = int(parts[2])

    # Extract clean function name (first word that looks like an identifier after return type)
    # e.g. "static int foo(..." -> "foo"
    m = re.search(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', raw_name)
    fn_name = m.group(1) if m else raw_name.split("(")[0].split()[-1]

    return fn_name, start, end


def discover_tests(cid: str, kinds: tuple[str, ...]) -> list[str]:
    rc, out = docker_exec(cid, ["cat", TEST_SCRIPT])
    if rc != 0:
        return []
    pattern = r"^\s+([" + "".join(kinds) + r"]\d+)\)"
    return re.findall(pattern, out, re.MULTILINE)


def inspect_scenario(scenario: str) -> ScenarioInfo:
    image = f"{IMAGE_REGISTRY}:{scenario}"
    print(f"  pulling {image}...", flush=True)
    run(["docker", "pull", "--platform", PLATFORM, image], timeout=300)

    cid = start_container(image)
    try:
        fault_file, fault_lines = read_fault_lines(cid)
        # Use the median/first fault line for function lookup
        primary_fault = fault_lines[0]

        fn_name, start_line, end_line = find_enclosing_function(cid, fault_file, primary_fault)

        oracle_tests = discover_tests(cid, ("n",))
        positive_tests = discover_tests(cid, ("p",))

        parts = scenario.split("-")  # libtiff-YYYY-MM-DD-bughash-fixhash
        bug_rev = parts[-2]
        fix_rev = parts[-1]

        return ScenarioInfo(
            scenario=scenario,
            fault_file=fault_file,
            fault_lines=fault_lines,
            function_name=fn_name,
            start_line=start_line,
            end_line=end_line,
            oracle_tests=oracle_tests,
            positive_tests=positive_tests[:3],  # keep up to 3 broad tests
        )
    finally:
        stop_container(cid)


def make_target_id(scenario: str, fn_name: str, start: int, end: int) -> str:
    # libtiff-2005-12-14-6746b87-0d3d51d -> libtiff_2005_12_14
    parts = scenario.split("-")
    prefix = "_".join(parts[:4])  # libtiff_2005_12_14
    return f"{prefix}_{fn_name}__line{start}_{end}"


def emit_catalog_entry(info: ScenarioInfo) -> dict:
    parts = info.scenario.split("-")
    target_id = make_target_id(info.scenario, info.function_name, info.start_line, info.end_line)
    return {
        "dataset": "manybugs",
        "subject": info.scenario,
        "version": "f",
        "language": "c",
        "file": info.fault_file,
        "function": info.function_name,
        "start_line": info.start_line,
        "end_line": info.end_line,
        "target_id": target_id,
        "metadata": {
            "bugzoo_scenario": info.scenario,
            "bug_revision": parts[-2],
            "fix_revision": parts[-1],
            "oracle_tests": info.oracle_tests,
            "fault_lines": [f"{info.fault_file}:{l}" for l in info.fault_lines],
            "notes": info.notes or "Auto-generated by inspect_scenarios.py",
        }
    }


def emit_csv_rows(info: ScenarioInfo, catalog_name: str) -> list[str]:
    parts = info.scenario.split("-")
    project = parts[0]  # libtiff
    target_id = make_target_id(info.scenario, info.function_name, info.start_line, info.end_line)
    rows = []
    for test in info.oracle_tests:
        rows.append(
            f"{catalog_name},manybugs,{info.scenario},f,{project},{target_id},"
            f"{info.fault_file},{info.start_line},{info.end_line},{test},manual-curation,oracle"
        )
    for test in info.positive_tests:
        rows.append(
            f"{catalog_name},manybugs,{info.scenario},f,{project},{target_id},"
            f"{info.fault_file},{info.start_line},{info.end_line},{test},manual-curation,broad"
        )
    return rows


def main():
    if len(sys.argv) < 2:
        print("Usage: python inspect_scenarios.py <scenario1> [<scenario2> ...]")
        sys.exit(1)

    scenarios = sys.argv[1:]
    catalog_name = "manybugs_libtiff_pilot"

    catalog_entries = []
    csv_rows = []
    errors = []

    for scenario in scenarios:
        print(f"\n[{scenario}]", flush=True)
        try:
            info = inspect_scenario(scenario)
            entry = emit_catalog_entry(info)
            rows = emit_csv_rows(info, catalog_name)
            catalog_entries.append(entry)
            csv_rows.extend(rows)
            print(f"  -> {info.function_name} ({info.start_line}-{info.end_line})"
                  f" | oracle={info.oracle_tests} | fn_size={info.end_line - info.start_line + 1} lines")
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append((scenario, str(exc)))

    print("\n\n=== CATALOG ENTRIES (JSON) ===")
    print(json.dumps(catalog_entries, indent=2))

    print("\n\n=== TARGET_TESTS CSV ROWS ===")
    print("catalog,dataset,subject_id,version,project,target_id,file_path,start_line,end_line,test_name,coverage_source,match_mode")
    for row in csv_rows:
        print(row)

    if errors:
        print(f"\n\n=== ERRORS ({len(errors)}) ===")
        for scenario, err in errors:
            print(f"  {scenario}: {err}")


if __name__ == "__main__":
    main()
