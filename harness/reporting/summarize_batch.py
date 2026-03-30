import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


RUNS_DIR = Path("harness/runs")
OUT_DIR = Path("harness/analysis/out")


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def infer_project(subject: str) -> str:
    if not subject:
        return "unknown"
    return subject.split("_")[0]


def collect_rejection_reasons(parse_report: dict) -> Counter:
    reasons = Counter()

    # Common shape: {"rejected_mutants": [{"reason": "..."}]}
    for item in parse_report.get("rejected_mutants", []):
        reason = item.get("reason", "unknown")
        reasons[reason] += 1

    # Alternate shape: {"rejections": [{"reason": "..."}]}
    for item in parse_report.get("rejections", []):
        reason = item.get("reason", "unknown")
        reasons[reason] += 1

    # Fallback counters if present directly
    for key in [
        "invalid_json_response",
        "precode_not_found",
        "duplicate_mutant",
        "non_executable_change",
        "non_executable_structural_change",
    ]:
        if key in parse_report and isinstance(parse_report[key], int):
            reasons[key] += parse_report[key]

    return reasons


def extract_summary_metrics(summary: dict):
    requested = safe_int(summary.get("requested_mutants", 0))
    accepted = safe_int(summary.get("accepted_mutants", 0))
    executable = safe_int(summary.get("executable_mutants", 0))
    killed = safe_int(summary.get("killed_mutants", 0))
    survived = safe_int(summary.get("survived_mutants", 0))

    return requested, accepted, executable, killed, survived


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    run_dirs = sorted([p for p in RUNS_DIR.iterdir() if p.is_dir()])

    overall = {
        "runs_total": 0,
        "runs_with_summary": 0,
        "runs_with_accepted": 0,
        "requested": 0,
        "accepted": 0,
        "executable": 0,
        "killed": 0,
        "survived": 0,
    }

    project_stats = defaultdict(lambda: {
        "runs": 0,
        "requested": 0,
        "accepted": 0,
        "executable": 0,
        "killed": 0,
        "survived": 0,
        "rejections": Counter(),
    })

    all_rejections = Counter()
    target_rows = []

    for run_dir in run_dirs:
        summary_path = run_dir / "summary.json"
        config_path = run_dir / "run_config.json"
        parse_report_path = run_dir / "generation" / "parse_report.json"

        if not summary_path.exists():
            continue

        summary = load_json(summary_path)
        cfg = load_json(config_path) if config_path.exists() else {}

        requested, accepted, executable, killed, survived = extract_summary_metrics(summary)

        subject = summary.get("subject") or cfg.get("subject", "unknown")
        target_id = summary.get("target_id") or cfg.get("target_id", run_dir.name)
        function = summary.get("function") or cfg.get("function", "unknown")
        dataset = summary.get("dataset") or cfg.get("dataset", "unknown")
        version = summary.get("version") or cfg.get("version", "unknown")
        project = infer_project(subject)

        overall["runs_total"] += 1
        overall["runs_with_summary"] += 1
        overall["requested"] += requested
        overall["accepted"] += accepted
        overall["executable"] += executable
        overall["killed"] += killed
        overall["survived"] += survived
        if accepted > 0:
            overall["runs_with_accepted"] += 1

        p = project_stats[project]
        p["runs"] += 1
        p["requested"] += requested
        p["accepted"] += accepted
        p["executable"] += executable
        p["killed"] += killed
        p["survived"] += survived

        reasons = Counter()
        if parse_report_path.exists():
            parse_report = load_json(parse_report_path)
            reasons = collect_rejection_reasons(parse_report)
            p["rejections"].update(reasons)
            all_rejections.update(reasons)

        target_rows.append({
            "run_name": run_dir.name,
            "dataset": dataset,
            "project": project,
            "subject": subject,
            "version": version,
            "target_id": target_id,
            "function": function,
            "requested_mutants": requested,
            "accepted_mutants": accepted,
            "acceptance_rate": round(accepted / requested, 4) if requested else 0.0,
            "executable_mutants": executable,
            "executable_yield": round(executable / accepted, 4) if accepted else 0.0,
            "killed_mutants": killed,
            "survived_mutants": survived,
            "mutation_score": round(killed / executable, 4) if executable else 0.0,
            "top_rejection_reason": reasons.most_common(1)[0][0] if reasons else "",
            "top_rejection_count": reasons.most_common(1)[0][1] if reasons else 0,
        })

    # Console summary
    print("\n=== GLOBAL RESULTS ===")
    print(f"Runs with summary: {overall['runs_with_summary']}")
    print(f"Runs with >=1 accepted mutant: {overall['runs_with_accepted']}")
    print(f"Requested mutants: {overall['requested']}")
    print(f"Accepted mutants: {overall['accepted']}")
    print(f"Executable mutants: {overall['executable']}")
    print(f"Killed mutants: {overall['killed']}")
    print(f"Survived mutants: {overall['survived']}")

    if overall["runs_with_summary"] > 0:
        print(f"Avg accepted per run: {overall['accepted'] / overall['runs_with_summary']:.2f}")
    if overall["requested"] > 0:
        print(f"Acceptance rate: {overall['accepted'] / overall['requested']:.3f}")
    if overall["accepted"] > 0:
        print(f"Executable yield: {overall['executable'] / overall['accepted']:.3f}")
    if overall["executable"] > 0:
        print(f"Mutation score: {overall['killed'] / overall['executable']:.3f}")

    print("\n=== PER PROJECT ===")
    for project in sorted(project_stats):
        p = project_stats[project]
        acceptance_rate = p["accepted"] / p["requested"] if p["requested"] else 0.0
        executable_yield = p["executable"] / p["accepted"] if p["accepted"] else 0.0
        mutation_score = p["killed"] / p["executable"] if p["executable"] else 0.0

        print(f"\n[{project}]")
        print(f"Runs: {p['runs']}")
        print(f"Requested: {p['requested']}")
        print(f"Accepted: {p['accepted']}")
        print(f"Executable: {p['executable']}")
        print(f"Killed: {p['killed']}")
        print(f"Survived: {p['survived']}")
        print(f"Acceptance rate: {acceptance_rate:.3f}")
        print(f"Executable yield: {executable_yield:.3f}")
        print(f"Mutation score: {mutation_score:.3f}")

        if p["rejections"]:
            print("Top rejection reasons:")
            for reason, count in p["rejections"].most_common(5):
                print(f"  - {reason}: {count}")

    if all_rejections:
        print("\n=== GLOBAL REJECTION REASONS ===")
        for reason, count in all_rejections.most_common():
            print(f"{reason}: {count}")

    # CSV outputs
    target_csv = OUT_DIR / "target_metrics.csv"
    with open(target_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_name",
                "dataset",
                "project",
                "subject",
                "version",
                "target_id",
                "function",
                "requested_mutants",
                "accepted_mutants",
                "acceptance_rate",
                "executable_mutants",
                "executable_yield",
                "killed_mutants",
                "survived_mutants",
                "mutation_score",
                "top_rejection_reason",
                "top_rejection_count",
            ],
        )
        writer.writeheader()
        writer.writerows(target_rows)

    project_csv = OUT_DIR / "project_metrics.csv"
    with open(project_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "project",
                "runs",
                "requested_mutants",
                "accepted_mutants",
                "acceptance_rate",
                "executable_mutants",
                "executable_yield",
                "killed_mutants",
                "survived_mutants",
                "mutation_score",
            ],
        )
        writer.writeheader()
        for project in sorted(project_stats):
            p = project_stats[project]
            writer.writerow({
                "project": project,
                "runs": p["runs"],
                "requested_mutants": p["requested"],
                "accepted_mutants": p["accepted"],
                "acceptance_rate": round(p["accepted"] / p["requested"], 4) if p["requested"] else 0.0,
                "executable_mutants": p["executable"],
                "executable_yield": round(p["executable"] / p["accepted"], 4) if p["accepted"] else 0.0,
                "killed_mutants": p["killed"],
                "survived_mutants": p["survived"],
                "mutation_score": round(p["killed"] / p["executable"], 4) if p["executable"] else 0.0,
            })

    rejection_csv = OUT_DIR / "rejection_reasons.csv"
    with open(rejection_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reason", "count"])
        writer.writeheader()
        for reason, count in all_rejections.most_common():
            writer.writerow({"reason": reason, "count": count})

    summary_json = OUT_DIR / "global_summary.json"
    global_summary = {
        "runs_with_summary": overall["runs_with_summary"],
        "runs_with_accepted": overall["runs_with_accepted"],
        "requested_mutants": overall["requested"],
        "accepted_mutants": overall["accepted"],
        "executable_mutants": overall["executable"],
        "killed_mutants": overall["killed"],
        "survived_mutants": overall["survived"],
        "acceptance_rate": round(overall["accepted"] / overall["requested"], 4) if overall["requested"] else 0.0,
        "executable_yield": round(overall["executable"] / overall["accepted"], 4) if overall["accepted"] else 0.0,
        "mutation_score": round(overall["killed"] / overall["executable"], 4) if overall["executable"] else 0.0,
        "projects": {},
        "rejection_reasons": dict(all_rejections),
    }

    for project in sorted(project_stats):
        p = project_stats[project]
        global_summary["projects"][project] = {
            "runs": p["runs"],
            "requested_mutants": p["requested"],
            "accepted_mutants": p["accepted"],
            "acceptance_rate": round(p["accepted"] / p["requested"], 4) if p["requested"] else 0.0,
            "executable_mutants": p["executable"],
            "executable_yield": round(p["executable"] / p["accepted"], 4) if p["accepted"] else 0.0,
            "killed_mutants": p["killed"],
            "survived_mutants": p["survived"],
            "mutation_score": round(p["killed"] / p["executable"], 4) if p["executable"] else 0.0,
            "rejection_reasons": dict(p["rejections"]),
        }

    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(global_summary, f, indent=2)

    print(f"\nWrote:")
    print(f"  - {target_csv}")
    print(f"  - {project_csv}")
    print(f"  - {rejection_csv}")
    print(f"  - {summary_json}")


if __name__ == "__main__":
    main()