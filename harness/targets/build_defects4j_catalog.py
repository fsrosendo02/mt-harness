import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List

from harness.targets.java_method_extractor import find_method_end, normalize_start_idx, parse_ctags_methods


def run(cmd: List[str], cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout


def list_bug_ids(project: str) -> List[str]:
    out = run(["defects4j", "bids", "-p", project])
    return [line.strip() for line in out.splitlines() if line.strip().isdigit()]


def checkout(project: str, bug_id: str, version: str, workdir: Path) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    run(["defects4j", "checkout", "-p", project, "-v", f"{bug_id}{version}", "-w", str(workdir)])


def iter_java_files(repo_root: Path) -> Iterable[Path]:
    for path in repo_root.rglob("*.java"):
        rel = path.relative_to(repo_root).as_posix().lower()
        if "/src/test/" in rel or "/test/" in rel or rel.endswith("test.java"):
            continue
        yield path


def method_loc(start_line: int, end_line: int) -> int:
    return end_line - start_line + 1


def count_branch_tokens(code: str) -> int:
    needles = ["if (", "if(", "switch(", "switch (", "for(", "for (", "while(", "while (", "catch(", "catch (", "&&", "||", "?"]
    return sum(code.count(n) for n in needles)


def count_params(signature_line: str) -> int:
    if "(" not in signature_line or ")" not in signature_line:
        return 0
    inside = signature_line.split("(", 1)[1].rsplit(")", 1)[0].strip()
    if not inside:
        return 0
    return len([part for part in inside.split(",") if part.strip()])


def looks_trivial(name: str, code: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(("get", "set")):
        return True
    if lowered in {"tostring", "hashcode", "equals"}:
        return True

    compact = " ".join(code.split())
    return (
        compact.startswith("return this.")
        or compact.startswith("this.") and compact.count(";") == 1
    )


def compute_score(name: str, loc: int, branches: int, params: int) -> float:
    score = 0.0
    if 8 <= loc <= 60:
        score += 0.4
    elif 5 <= loc <= 100:
        score += 0.2

    score += min(branches, 8) * 0.08
    score += 0.1 if params <= 3 else 0.05 if params <= 5 else 0.0

    lowered = name.lower()
    for token in ["parse", "validate", "check", "create", "convert", "normalize", "compare"]:
        if token in lowered:
            score += 0.08
    return round(min(score, 1.0), 3)


def build_target_id(project: str, bug_id: str, version: str, function: str, start_line: int) -> str:
    return f"{project.lower()}_{bug_id}{version}_{function}__line{start_line}"


def extract_candidates(subject: str, version: str, repo_root: Path) -> List[Dict[str, object]]:
    project, bug_id = subject.split("_", 1)
    targets: List[Dict[str, object]] = []

    for java_file in iter_java_files(repo_root):
        rel_file = java_file.relative_to(repo_root).as_posix()
        lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)

        for entry in parse_ctags_methods(str(java_file)):
            if entry["kind"] != "method":
                continue

            start_idx = normalize_start_idx(lines, int(entry["line"]) - 1)
            try:
                end_idx = find_method_end(lines, start_idx)
            except ValueError:
                continue

            start_line = start_idx + 1
            end_line = end_idx + 1
            code = "".join(lines[start_idx : end_idx + 1])
            loc = method_loc(start_line, end_line)
            if loc < 5 or loc > 120:
                continue

            name = str(entry["name"])
            if looks_trivial(name, code):
                continue

            branches = count_branch_tokens(code)
            sig_line = lines[int(entry["line"]) - 1] if int(entry["line"]) - 1 < len(lines) else ""
            params = count_params(sig_line)
            score = compute_score(name, loc, branches, params)
            if score < 0.30:
                continue

            targets.append({
                "target_id": build_target_id(project, bug_id, version, name, start_line),
                "dataset": "defects4j",
                "subject": subject,
                "version": version,
                "language": "java",
                "file": rel_file,
                "function": name,
                "start_line": start_line,
                "end_line": end_line,
                "metadata": {
                    "loc": loc,
                    "branch_count": branches,
                    "param_count": params,
                    "score": score,
                },
            })

    targets.sort(key=lambda t: (-float(t["metadata"]["score"]), str(t["file"]), int(t["start_line"])))
    return targets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--projects", required=True, help="Comma-separated, e.g. Lang,Math,Cli")
    parser.add_argument("--versions", default="f")
    parser.add_argument("--bug-ids", default="", help="Optional comma-separated bug IDs to limit the build")
    parser.add_argument("--max-per-project", type=int, default=50)
    args = parser.parse_args()

    projects = [part.strip() for part in args.projects.split(",") if part.strip()]
    versions = [part.strip() for part in args.versions.split(",") if part.strip()]
    forced_bug_ids = [part.strip() for part in args.bug_ids.split(",") if part.strip()]

    final_catalog: List[Dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="d4j_catalog_") as tmp_dir:
        tmp_root = Path(tmp_dir)

        for project in projects:
            bug_ids = forced_bug_ids or list_bug_ids(project)
            project_targets: List[Dict[str, object]] = []

            for bug_id in bug_ids:
                for version in versions:
                    subject = f"{project}_{bug_id}"
                    checkout_dir = tmp_root / f"{project}_{bug_id}{version}"
                    try:
                        checkout(project, bug_id, version, checkout_dir)
                        project_targets.extend(extract_candidates(subject, version, checkout_dir))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[WARN] Skipping {subject}{version}: {exc}")

            project_targets.sort(
                key=lambda t: (
                    -float(t["metadata"]["score"]),
                    str(t["subject"]),
                    str(t["file"]),
                    int(t["start_line"]),
                )
            )
            final_catalog.extend(project_targets[: args.max_per_project])

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(final_catalog, indent=2), encoding="utf-8")
    print(f"Wrote {len(final_catalog)} targets to {output_path}")


if __name__ == "__main__":
    main()
