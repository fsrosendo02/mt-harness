import argparse
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

# ---- Fix imports when running as script ----
import sys
if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.targets.java_method_extractor import (
    find_method_end,
    normalize_start_idx,
    parse_ctags_methods,
)


# -----------------------------
# Utils
# -----------------------------

def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def checkout_defects4j(project, bug_id, version, out_dir):
    run([
        "defects4j", "checkout",
        "-p", project,
        "-v", f"{bug_id}{version}",
        "-w", str(out_dir)
    ])


def list_java_files(root: Path):
    files = []
    for p in root.rglob("*.java"):
        low = str(p).lower().replace("\\", "/")
        if "/src/test/" in low or "/test/" in low or p.name.endswith("Test.java"):
            continue
        files.append(p)
    return files


# -----------------------------
# Feature extraction
# -----------------------------

def count_branches(code: str) -> int:
    patterns = [
        r"\bif\b",
        r"\belse\b",
        r"\bswitch\b",
        r"\bcase\b",
        r"\bfor\b",
        r"\bwhile\b",
        r"\bcatch\b",
        r"\?",
        r"&&",
        r"\|\|",
    ]
    return sum(len(re.findall(p, code)) for p in patterns)


def count_params(signature: str) -> int:
    m = re.search(r"\((.*)\)", signature, flags=re.DOTALL)
    if not m:
        return 0

    inside = m.group(1).strip()
    if not inside:
        return 0

    depth_angle = 0
    depth_paren = 0
    depth_brack = 0
    current = []
    parts = []

    for ch in inside:
        if ch == "<":
            depth_angle += 1
        elif ch == ">":
            depth_angle = max(0, depth_angle - 1)
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            depth_brack = max(0, depth_brack - 1)

        if ch == "," and depth_angle == 0 and depth_paren == 0 and depth_brack == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)

    return len(parts)


def compute_score(loc: int, branches: int, params: int) -> float:
    score = 0.0

    # size
    if 12 <= loc <= 35:
        score += 0.30
    elif 8 <= loc <= 50:
        score += 0.22
    elif 6 <= loc <= 70:
        score += 0.12

    # branch richness
    if branches >= 10:
        score += 0.30
    elif branches >= 7:
        score += 0.22
    elif branches >= 4:
        score += 0.14
    elif branches >= 2:
        score += 0.06

    # params
    if 1 <= params <= 3:
        score += 0.14
    elif params == 0:
        score += 0.05
    elif 4 <= params <= 5:
        score += 0.09

    return round(score, 3)


# -----------------------------
# Declaration / signature helpers
# -----------------------------

def collect_signature(lines, raw_idx):
    """
    Reconstruct a possibly multiline Java method declaration starting at raw_idx.
    Stops when it sees '{' or a terminating ';'.
    """
    parts = []
    for i in range(raw_idx, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        parts.append(stripped)

        joined = " ".join(parts)
        if "{" in stripped or stripped.endswith(";"):
            joined = joined.split("{")[0].strip()
            if joined.endswith(";"):
                joined = joined[:-1].strip()
            return joined

    return " ".join(parts).strip()


def looks_trivial(function_name: str, code: str) -> bool:
    lowered = function_name.lower().strip()

    if lowered.startswith("get") or lowered.startswith("set"):
        return True
    if lowered in {"tostring", "hashcode"}:
        return True

    compact = re.sub(r"\s+", " ", code).strip()

    trivial_patterns = [
        r"^\{?\s*return\s+this\.[A-Za-z_][A-Za-z0-9_]*\s*;\s*\}?$",
        r"^\{?\s*this\.[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*\s*;\s*\}?$",
    ]
    return any(re.search(p, compact) for p in trivial_patterns)


# -----------------------------
# Main extraction
# -----------------------------

def extract_methods(file_path: Path):
    lines = file_path.read_text(errors="ignore").splitlines()
    methods = []
    class_name = file_path.stem

    for entry in parse_ctags_methods(str(file_path)):
        if entry["kind"].lower() != "method":
            continue

        if entry["name"] and entry["name"][0].isupper():
            continue

        raw_idx = entry["line"] - 1
        if raw_idx < 0 or raw_idx >= len(lines):
            continue

        # Exclude constructors for this phase
        if entry["name"] == class_name:
            continue

        start = normalize_start_idx(lines, raw_idx)

        signature = collect_signature(lines, raw_idx)
        if not signature:
            continue

        # Skip declarations without body
        if signature.endswith(";"):
            continue

        try:
            end = find_method_end(lines, start)
        except ValueError:
            continue

        if not end or end <= start:
            continue

        code = "\n".join(lines[start:end])
        loc = end - start
        params = count_params(signature)
        branches = count_branches(code)

        # Hard filters
        if loc < 6 or loc > 100:
            continue
        if looks_trivial(entry["name"], code):
            continue

        score = compute_score(loc, branches, params)

        methods.append({
            "function": entry["name"],
            "start_line": start + 1,
            "end_line": end + 1,
            "signature": signature,
            "metadata": {
                "loc": loc,
                "branch_count": branches,
                "param_count": params,
                "score": score,
            }
        })

    return methods


# -----------------------------
# Deduplication / selection
# -----------------------------

def deduplicate(methods, max_per_function: int, max_per_file: int):
    by_function = defaultdict(list)
    for m in methods:
        key = (m["file"], m["function"])
        by_function[key].append(m)

    # First collapse overload families / repeated same-name functions per file
    collapsed = []
    for group in by_function.values():
        group_sorted = sorted(
            group,
            key=lambda x: (
                -x["metadata"]["score"],
                x["metadata"]["loc"],
                x["start_line"],
            )
        )
        collapsed.extend(group_sorted[:max_per_function])

    # Then cap number of selected methods per file
    by_file = defaultdict(list)
    for m in collapsed:
        by_file[m["file"]].append(m)

    final = []
    for group in by_file.values():
        group_sorted = sorted(
            group,
            key=lambda x: (
                -x["metadata"]["score"],
                x["function"],
                x["start_line"],
            )
        )
        final.extend(group_sorted[:max_per_file])

    return final


# -----------------------------
# Main builder
# -----------------------------

def build_catalog(project, bug_ids, version, max_per_project, max_per_function, max_per_file):
    all_targets = []

    for bug_id in bug_ids:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            checkout_defects4j(project, bug_id, version, tmp_path)

            for java_file in list_java_files(tmp_path):
                methods = extract_methods(java_file)

                for m in methods:
                    m["file"] = str(java_file.relative_to(tmp_path))
                    m["dataset"] = "defects4j"
                    m["subject"] = f"{project}_{bug_id}"
                    m["version"] = version
                    m["language"] = "java"
                    m["target_id"] = (
                        f"{project.lower()}_{bug_id}{version}_"
                        f"{m['function']}__line{m['start_line']}"
                    )
                    all_targets.append(m)

    all_targets = deduplicate(all_targets, max_per_function, max_per_file)

    all_targets = sorted(
        all_targets,
        key=lambda x: (
            -x["metadata"]["score"],
            x["file"],
            x["function"],
            x["start_line"],
        )
    )[:max_per_project]

    return all_targets


# -----------------------------
# CLI
# -----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--projects", required=True, help="Single Defects4J project for now, e.g. Lang")
    parser.add_argument("--bug-ids", required=True, help="Comma-separated bug ids, e.g. 1,2,3")
    parser.add_argument("--versions", default="f")
    parser.add_argument("--max-per-project", type=int, default=20)
    parser.add_argument("--max-per-function", type=int, default=1)
    parser.add_argument("--max-per-file", type=int, default=2)

    args = parser.parse_args()

    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    bug_ids = [b.strip() for b in args.bug_ids.split(",") if b.strip()]
    version = args.versions.strip()

    all_catalog = []

    for project in projects:
        project_catalog = build_catalog(
            project=project,
            bug_ids=bug_ids,
            version=version,
            max_per_project=args.max_per_project,
            max_per_function=args.max_per_function,
            max_per_file=args.max_per_file,
        )
        all_catalog.extend(project_catalog)

    Path(args.output).write_text(json.dumps(all_catalog, indent=2), encoding="utf-8")
    print(f"Saved {len(all_catalog)} targets to {args.output}")


if __name__ == "__main__":
    main()