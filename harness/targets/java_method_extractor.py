import subprocess
from typing import Dict, List, Optional


CTAGS_CMD = ["ctags", "-x", "--language-force=Java"]


def parse_ctags_methods(file_path: str) -> List[Dict[str, object]]:
    cmd = [*CTAGS_CMD, file_path]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ctags failed: {result.stderr}")

    entries: List[Dict[str, object]] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue

        name = parts[0]
        kind = parts[1].lower()

        try:
            line_no = int(parts[2])
        except ValueError:
            continue

        entries.append(
            {
                "name": name,
                "kind": kind,
                "line": line_no,
                "raw": line,
            }
        )

    return entries


def get_method_start_ctags(file_path: str, function_name: str) -> int:
    matches = [
        entry["line"]
        for entry in parse_ctags_methods(file_path)
        if entry["name"] == function_name and entry["kind"] == "method"
    ]

    if not matches:
        raise ValueError(f"Method '{function_name}' not found via ctags")

    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous method '{function_name}' (overloads detected). "
            "Store start_line/end_line in the catalog to disambiguate this target."
        )

    return int(matches[0])


def find_method_end(source_lines: List[str], start_idx: int) -> int:
    brace_count = 0
    started = False

    for i in range(start_idx, len(source_lines)):
        line = source_lines[i]
        brace_count += line.count("{")
        if "{" in line:
            started = True

        brace_count -= line.count("}")

        if started and brace_count == 0:
            return i

    raise ValueError("Could not determine method end")


def normalize_start_idx(source_lines: List[str], start_idx: int) -> int:
    while start_idx > 0 and source_lines[start_idx - 1].strip().startswith("@"):
        start_idx -= 1
    return start_idx


def extract_method_by_lines(
    source_lines: List[str], start_line: int, end_line: int
) -> str:
    if start_line < 1 or end_line < start_line or end_line > len(source_lines):
        raise ValueError(
            f"Invalid line range [{start_line}, {end_line}] for file with {len(source_lines)} lines"
        )

    return "".join(source_lines[start_line - 1 : end_line])


def extract_method(
    source_lines: List[str], function_name: str, file_path: str
):
    start_line = get_method_start_ctags(file_path, function_name)
    start_idx = normalize_start_idx(source_lines, start_line - 1)
    end_idx = find_method_end(source_lines, start_idx)
    method_code = "".join(source_lines[start_idx : end_idx + 1])

    return start_idx + 1, end_idx + 1, method_code
