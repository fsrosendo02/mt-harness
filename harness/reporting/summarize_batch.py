import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from harness.storage.layout import (
    batches_root,
    discover_batch_manifest_paths,
    llm_batch_manifest_path,
    llm_batch_summary_dir,
    manifest_path,
    reports_root,
    resolve_run_dir,
    resolve_summary_path,
)

BATCHES_DIR = batches_root()
REPORTING_DIR = reports_root() / "batch_summaries"


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def infer_project(subject: str) -> str:
    if not subject:
        return "unknown"
    return subject.split("_")[0]


def collect_rejection_reasons(parse_report: dict) -> Counter:
    reasons = Counter()

    for item in parse_report.get("rejections", []):
        reason = item.get("reason", "unknown")
        reasons[reason] += 1

    for item in parse_report.get("rejected_mutants", []):
        reason = item.get("reason", "unknown")
        reasons[reason] += 1

    return reasons


def extract_generation_metrics(parse_report: dict):
    requested = safe_int(parse_report.get("requested_count", 0))
    accepted = safe_int(parse_report.get("accepted_count", 0))
    rejected = safe_int(parse_report.get("rejected_count", 0))
    return requested, accepted, rejected


def extract_execution_metrics(summary: dict):
    overall = summary.get("overall", {})

    total_mutants = safe_int(overall.get("total_mutants", 0))
    build_successes = safe_int(overall.get("build_successes", 0))
    executable = safe_int(overall.get("executable_mutants", 0))
    killed = safe_int(overall.get("killed_mutants", 0))
    survived = safe_int(overall.get("survived_mutants", 0))
    build_success_rate = safe_float(overall.get("build_success_rate", 0.0))
    executable_yield = safe_float(overall.get("executable_yield", 0.0))
    mutation_score = safe_float(overall.get("mutation_score", 0.0))

    return {
        "total_mutants": total_mutants,
        "build_successes": build_successes,
        "executable_mutants": executable,
        "killed_mutants": killed,
        "survived_mutants": survived,
        "build_success_rate": build_success_rate,
        "executable_yield": executable_yield,
        "mutation_score": mutation_score,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True, help="Batch id, e.g. batch01")
    parser.add_argument("--runs-subdir", default=None, help="Runs subdir, e.g. c/llm")
    args = parser.parse_args()

    batch_id = args.batch_id
    runs_subdir = args.runs_subdir or None
    batch_manifest_path = llm_batch_manifest_path(batch_id, runs_subdir)
    if not batch_manifest_path.exists():
        legacy_path = BATCHES_DIR / f"{batch_id}.json"
        batch_manifest_path = legacy_path if legacy_path.exists() else batch_manifest_path

    if not batch_manifest_path.exists():
        discovered = [
            p for p in discover_batch_manifest_paths()
            if p.parent.name == batch_id or p.parent.name == batch_id.replace("-", "_")
        ]
        if discovered:
            batch_manifest_path = discovered[0]

    if not batch_manifest_path.exists():
        raise FileNotFoundError(f"Batch manifest not found: {batch_manifest_path}")

    batch_manifest = load_json(batch_manifest_path)
    batch_runs = batch_manifest.get("runs", [])

    out_dir = llm_batch_summary_dir(batch_id, runs_subdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    overall = {
        "runs_total_in_manifest": len(batch_runs),
        "runs_with_summary": 0,
        "runs_with_parse_report": 0,
        "runs_with_accepted": 0,
        "run_status_counts": Counter(),
        "requested": 0,
        "accepted": 0,
        "rejected": 0,
        "executed_total_mutants": 0,
        "build_successes": 0,
        "executable": 0,
        "killed": 0,
        "survived": 0,
    }

    project_stats = defaultdict(lambda: {
        "runs": 0,
        "run_status_counts": Counter(),
        "requested": 0,
        "accepted": 0,
        "rejected": 0,
        "executed_total_mutants": 0,
        "build_successes": 0,
        "executable": 0,
        "killed": 0,
        "survived": 0,
        "rejections": Counter(),
    })

    all_rejections = Counter()
    target_rows = []

    for run_info in batch_runs:
        run_name = run_info["run_name"]
        run_dir = resolve_run_dir(run_name, batch_id)

        summary_path = resolve_summary_path(run_dir)
        config_path = run_dir / "run_config.json"
        parse_report_path = run_dir / "generation" / "parse_report.json"
        manifest_file = manifest_path(run_dir)

        cfg = load_json(config_path) if config_path.exists() else {}
        manifest = load_json(manifest_file) if manifest_file.exists() else {}

        subject = cfg.get("subject", run_info.get("subject", "unknown"))
        target_id = cfg.get("target_id", run_info.get("target_id", run_name))
        function = cfg.get("function", run_info.get("function", "unknown"))
        dataset = cfg.get("dataset", "unknown")
        version = cfg.get("version", "unknown")
        project = infer_project(subject)
        run_status = (
            manifest.get("status")
            or run_info.get("status")
            or "unknown"
        )
        failure_reason = (
            manifest.get("failure_reason")
            or run_info.get("failure_reason")
            or ""
        )

        requested = accepted = rejected = 0
        reasons = Counter()

        if parse_report_path.exists():
            parse_report = load_json(parse_report_path)
            requested, accepted, rejected = extract_generation_metrics(parse_report)
            reasons = collect_rejection_reasons(parse_report)
            overall["runs_with_parse_report"] += 1
            overall["requested"] += requested
            overall["accepted"] += accepted
            overall["rejected"] += rejected
            if accepted > 0:
                overall["runs_with_accepted"] += 1

        exec_metrics = {
            "total_mutants": 0,
            "build_successes": 0,
            "executable_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "build_success_rate": 0.0,
            "executable_yield": 0.0,
            "mutation_score": 0.0,
        }

        if summary_path.exists():
            summary = load_json(summary_path)
            exec_metrics = extract_execution_metrics(summary)
            overall["runs_with_summary"] += 1
            overall["executed_total_mutants"] += exec_metrics["total_mutants"]
            overall["build_successes"] += exec_metrics["build_successes"]
            overall["executable"] += exec_metrics["executable_mutants"]
            overall["killed"] += exec_metrics["killed_mutants"]
            overall["survived"] += exec_metrics["survived_mutants"]

        overall["run_status_counts"][run_status] += 1

        p = project_stats[project]
        p["runs"] += 1
        p["run_status_counts"][run_status] += 1
        p["requested"] += requested
        p["accepted"] += accepted
        p["rejected"] += rejected
        p["executed_total_mutants"] += exec_metrics["total_mutants"]
        p["build_successes"] += exec_metrics["build_successes"]
        p["executable"] += exec_metrics["executable_mutants"]
        p["killed"] += exec_metrics["killed_mutants"]
        p["survived"] += exec_metrics["survived_mutants"]
        p["rejections"].update(reasons)
        all_rejections.update(reasons)

        target_rows.append({
            "batch_id": batch_id,
            "run_name": run_name,
            "dataset": dataset,
            "project": project,
            "subject": subject,
            "version": version,
            "target_id": target_id,
            "function": function,
            "run_status": run_status,
            "failure_reason": failure_reason,
            "requested_mutants": requested,
            "accepted_mutants": accepted,
            "rejected_mutants": rejected,
            "acceptance_rate": round(accepted / requested, 4) if requested else 0.0,
            "executed_total_mutants": exec_metrics["total_mutants"],
            "build_successes": exec_metrics["build_successes"],
            "build_success_rate": exec_metrics["build_success_rate"],
            "executable_mutants": exec_metrics["executable_mutants"],
            "executable_yield": exec_metrics["executable_yield"],
            "killed_mutants": exec_metrics["killed_mutants"],
            "survived_mutants": exec_metrics["survived_mutants"],
            "mutation_score": exec_metrics["mutation_score"],
            "top_rejection_reason": reasons.most_common(1)[0][0] if reasons else "",
            "top_rejection_count": reasons.most_common(1)[0][1] if reasons else 0,
        })

    print(f"\n=== BATCH {batch_id} RESULTS ===")
    print(f"Runs in manifest: {overall['runs_total_in_manifest']}")
    print(f"Runs with parse report: {overall['runs_with_parse_report']}")
    print(f"Runs with summary: {overall['runs_with_summary']}")
    print(f"Runs with >=1 accepted mutant: {overall['runs_with_accepted']}")
    print(f"Run status counts: {dict(overall['run_status_counts'])}")
    print(f"Requested mutants: {overall['requested']}")
    print(f"Accepted mutants: {overall['accepted']}")
    print(f"Rejected mutants: {overall['rejected']}")
    print(f"Executed total mutants: {overall['executed_total_mutants']}")
    print(f"Build successes: {overall['build_successes']}")
    print(f"Executable mutants: {overall['executable']}")
    print(f"Killed mutants: {overall['killed']}")
    print(f"Survived mutants: {overall['survived']}")

    if overall["requested"] > 0:
        print(f"Acceptance rate: {overall['accepted'] / overall['requested']:.3f}")
    if overall["accepted"] > 0:
        print(f"Execution retention: {overall['executed_total_mutants'] / overall['accepted']:.3f}")
    if overall["executed_total_mutants"] > 0:
        print(f"Build success rate: {overall['build_successes'] / overall['executed_total_mutants']:.3f}")
    if overall["accepted"] > 0:
        print(f"Executable yield: {overall['executable'] / overall['accepted']:.3f}")
    if overall["executable"] > 0:
        print(f"Mutation score: {overall['killed'] / overall['executable']:.3f}")

    print("\n=== PER PROJECT ===")
    for project in sorted(project_stats):
        p = project_stats[project]
        acceptance_rate = p["accepted"] / p["requested"] if p["requested"] else 0.0
        execution_retention = p["executed_total_mutants"] / p["accepted"] if p["accepted"] else 0.0
        build_success_rate = p["build_successes"] / p["executed_total_mutants"] if p["executed_total_mutants"] else 0.0
        executable_yield = p["executable"] / p["accepted"] if p["accepted"] else 0.0
        mutation_score = p["killed"] / p["executable"] if p["executable"] else 0.0

        print(f"\n[{project}]")
        print(f"Runs: {p['runs']}")
        print(f"Run status counts: {dict(p['run_status_counts'])}")
        print(f"Requested: {p['requested']}")
        print(f"Accepted: {p['accepted']}")
        print(f"Rejected: {p['rejected']}")
        print(f"Executed total mutants: {p['executed_total_mutants']}")
        print(f"Build successes: {p['build_successes']}")
        print(f"Executable: {p['executable']}")
        print(f"Killed: {p['killed']}")
        print(f"Survived: {p['survived']}")
        print(f"Acceptance rate: {acceptance_rate:.3f}")
        print(f"Execution retention: {execution_retention:.3f}")
        print(f"Build success rate: {build_success_rate:.3f}")
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

    target_csv = out_dir / "target_metrics.csv"
    with open(target_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "batch_id",
                "run_name",
                "dataset",
                "project",
                "subject",
                "version",
                "target_id",
                "function",
                "run_status",
                "failure_reason",
                "requested_mutants",
                "accepted_mutants",
                "rejected_mutants",
                "acceptance_rate",
                "executed_total_mutants",
                "build_successes",
                "build_success_rate",
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

    project_csv = out_dir / "project_metrics.csv"
    with open(project_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "batch_id",
                "project",
                "runs",
                "requested_mutants",
                "accepted_mutants",
                "rejected_mutants",
                "acceptance_rate",
                "executed_total_mutants",
                "build_successes",
                "build_success_rate",
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
                "batch_id": batch_id,
                "project": project,
                "runs": p["runs"],
                "requested_mutants": p["requested"],
                "accepted_mutants": p["accepted"],
                "rejected_mutants": p["rejected"],
                "acceptance_rate": round(p["accepted"] / p["requested"], 4) if p["requested"] else 0.0,
                "executed_total_mutants": p["executed_total_mutants"],
                "build_successes": p["build_successes"],
                "build_success_rate": round(p["build_successes"] / p["executed_total_mutants"], 4) if p["executed_total_mutants"] else 0.0,
                "executable_mutants": p["executable"],
                "executable_yield": round(p["executable"] / p["accepted"], 4) if p["accepted"] else 0.0,
                "killed_mutants": p["killed"],
                "survived_mutants": p["survived"],
                "mutation_score": round(p["killed"] / p["executable"], 4) if p["executable"] else 0.0,
            })

    rejection_csv = out_dir / "rejection_reasons.csv"
    with open(rejection_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["reason", "count"])
        writer.writeheader()
        for reason, count in all_rejections.most_common():
            writer.writerow({"reason": reason, "count": count})

    summary_json = out_dir / "global_summary.json"
    global_summary = {
        "batch_id": batch_id,
        "runs_total_in_manifest": overall["runs_total_in_manifest"],
        "runs_with_parse_report": overall["runs_with_parse_report"],
        "runs_with_summary": overall["runs_with_summary"],
        "runs_with_accepted": overall["runs_with_accepted"],
        "run_status_counts": dict(overall["run_status_counts"]),
        "requested_mutants": overall["requested"],
        "accepted_mutants": overall["accepted"],
        "rejected_mutants": overall["rejected"],
        "executed_total_mutants": overall["executed_total_mutants"],
        "build_successes": overall["build_successes"],
        "executable_mutants": overall["executable"],
        "killed_mutants": overall["killed"],
        "survived_mutants": overall["survived"],
        "acceptance_rate": round(overall["accepted"] / overall["requested"], 4) if overall["requested"] else 0.0,
        "execution_retention": round(overall["executed_total_mutants"] / overall["accepted"], 4) if overall["accepted"] else 0.0,
        "build_success_rate": round(overall["build_successes"] / overall["executed_total_mutants"], 4) if overall["executed_total_mutants"] else 0.0,
        "executable_yield": round(overall["executable"] / overall["accepted"], 4) if overall["accepted"] else 0.0,
        "mutation_score": round(overall["killed"] / overall["executable"], 4) if overall["executable"] else 0.0,
        "projects": {},
        "rejection_reasons": dict(all_rejections),
    }

    for project in sorted(project_stats):
        p = project_stats[project]
        global_summary["projects"][project] = {
            "runs": p["runs"],
            "run_status_counts": dict(p["run_status_counts"]),
            "requested_mutants": p["requested"],
            "accepted_mutants": p["accepted"],
            "rejected_mutants": p["rejected"],
            "acceptance_rate": round(p["accepted"] / p["requested"], 4) if p["requested"] else 0.0,
            "executed_total_mutants": p["executed_total_mutants"],
            "build_successes": p["build_successes"],
            "build_success_rate": round(p["build_successes"] / p["executed_total_mutants"], 4) if p["executed_total_mutants"] else 0.0,
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
