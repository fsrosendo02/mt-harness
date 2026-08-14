from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from harness.storage.layout import catalog_target_tests_csv_path


MUTANT_LEVEL_FIELDS = [
    "tool", "model", "num_mutants_requested", "campaign_id", "run_name",
    "subject_id", "target_id", "mutant_id", "mutant_hash", "killing_tests",
    "oracle_tests", "n_killing_tests", "n_oracle_tests", "coupled",
    "exact_match", "ochiai", "source_run_dir",
]


def parse_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


@dataclass(frozen=True)
class Campaign:
    tool: str
    model: str
    num_mutants_requested: str
    campaign_id: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.tool, self.model, self.num_mutants_requested, self.campaign_id)


@dataclass
class RunData:
    campaign: Campaign
    run_name: str
    subject_id: str
    target_id: str
    run_dir: Path
    results: list[dict[str, str]]
    tests: list[dict[str, str]]


def load_catalog(catalog_file: Path) -> tuple[list[dict], dict[str, dict]]:
    payload = json.loads(catalog_file.read_text(encoding="utf-8"))
    targets = payload.get("targets", [])
    if not targets:
        raise ValueError(f"Catalog has no targets: {catalog_file}")
    by_id = {str(target["target_id"]): target for target in targets}
    if len(by_id) != len(targets):
        raise ValueError(f"Catalog contains duplicate target_id values: {catalog_file}")
    return targets, by_id


def load_oracles(
    catalog_file: Path,
    target_tests_file: Path | None = None,
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[dict[str, str]]]:
    path = target_tests_file or catalog_target_tests_csv_path(catalog_file)
    if not path.exists():
        raise FileNotFoundError(f"Target-test mapping not found: {path}")

    oracle_tests: dict[str, set[str]] = defaultdict(set)
    eligible_tests: dict[str, set[str]] = defaultdict(set)
    rows = read_csv(path)
    for row in rows:
        target_id = str(row.get("target_id") or "").strip()
        test_name = str(row.get("test_name") or "").strip()
        if not target_id or not test_name:
            continue
        eligible_tests[target_id].add(test_name)
        if str(row.get("match_mode") or "").strip().lower() == "oracle":
            oracle_tests[target_id].add(test_name)
    return dict(oracle_tests), dict(eligible_tests), rows


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_run_metadata(run_dir: Path) -> dict:
    """Combine execution and generation metadata, preferring run-level data."""
    config = _load_json(run_dir / "run_config.json")
    manifest = _load_json(run_dir / "run_manifest.json")
    extra = manifest.get("extra_metadata") or {}
    return {
        **config,
        **extra,
        "run_name": manifest.get("run_name") or config.get("run_name"),
        "target_id": (
            (manifest.get("target") or {}).get("target_id")
            or extra.get("target_id")
            or config.get("target_id")
        ),
        "subject": (
            (manifest.get("subject") or {}).get("subject_id")
            or config.get("subject")
        ),
    }


def _campaign_for_run(tool: str, run_dir: Path, config: dict) -> Campaign:
    if tool == "llm":
        model = str(config.get("model_name") or config.get("model") or "unknown")
        requested = str(
            config.get("n_requested_mutants")
            or config.get("num_mutants")
            or ""
        )
        campaign_id = str(config.get("batch_id") or run_dir.name.split("__", 1)[0])
    else:
        model = "mull"
        requested = ""
        campaign_id = run_dir.parent.name
    return Campaign(tool, model, requested, campaign_id)


def discover_runs(
    *,
    tool: str,
    roots: Iterable[Path],
    catalog_target_ids: set[str],
) -> tuple[list[RunData], list[dict]]:
    runs: list[RunData] = []
    audit: list[dict] = []
    seen_dirs: set[Path] = set()

    for root in roots:
        if not root.exists():
            audit.append({"kind": "missing_source_root", "tool": tool, "path": str(root)})
            continue
        for results_path in sorted(root.rglob("execution/results.csv")):
            run_dir = results_path.parent.parent
            resolved = run_dir.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            test_path = run_dir / "execution" / "test_results.csv"
            if not test_path.exists():
                audit.append({
                    "kind": "missing_test_results", "tool": tool,
                    "run_dir": str(run_dir),
                })
                continue
            try:
                results = read_csv(results_path)
                tests = read_csv(test_path)
            except (OSError, csv.Error) as exc:
                audit.append({
                    "kind": "unreadable_run", "tool": tool,
                    "run_dir": str(run_dir), "message": str(exc),
                })
                continue

            config = _load_run_metadata(run_dir)
            if tool == "mull" and config.get("source_revision") != "fixed":
                audit.append({
                    "kind": "unverified_mull_source_revision",
                    "tool": tool,
                    "run_dir": str(run_dir),
                    "source_revision": config.get("source_revision"),
                    "message": "Run excluded: Mull coupling requires source_revision=fixed",
                })
                continue
            target_candidates = {
                str(row.get("target_id") or "").strip() for row in results + tests
                if str(row.get("target_id") or "").strip()
            }
            configured_target = str(config.get("target_id") or "").strip()
            if configured_target:
                target_candidates.add(configured_target)
            if not target_candidates:
                # Mull has no generation manifest. This fallback also preserves
                # empty Mull runs, provided their directory follows the standard
                # catalog-oriented ``...__<target_id>`` naming convention.
                target_candidates.update(
                    target_id for target_id in catalog_target_ids
                    if run_dir.name.endswith(f"__{target_id}")
                )
            in_catalog = sorted(target_candidates & catalog_target_ids)
            if not in_catalog:
                continue
            if len(in_catalog) != 1:
                audit.append({
                    "kind": "ambiguous_run_targets", "tool": tool,
                    "run_dir": str(run_dir), "targets": in_catalog,
                })
                continue
            target_id = in_catalog[0]
            results = [row for row in results if row.get("target_id") == target_id]
            tests = [row for row in tests if row.get("target_id") == target_id]
            first = (results or tests or [{}])[0]
            run_name = str(first.get("run_name") or config.get("run_name") or run_dir.name)
            subject_id = str(first.get("subject_id") or config.get("subject") or "")
            runs.append(RunData(
                campaign=_campaign_for_run(tool, run_dir, config),
                run_name=run_name,
                subject_id=subject_id,
                target_id=target_id,
                run_dir=run_dir,
                results=results,
                tests=tests,
            ))
    return runs, audit


def compute_mutant_rows(
    runs: Iterable[RunData],
    oracle_tests: dict[str, set[str]],
    eligible_tests: dict[str, set[str]],
) -> tuple[list[dict], list[dict]]:
    output: list[dict] = []
    audit: list[dict] = []
    seen_keys: set[tuple] = set()

    for run in runs:
        tests_by_mutant: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in run.tests:
            tests_by_mutant[str(row.get("mutant_id") or "")].append(row)

        for mutant in run.results:
            mutant_id = str(mutant.get("mutant_id") or "")
            if not mutant_id:
                audit.append({"kind": "missing_mutant_id", "run_dir": str(run.run_dir)})
                continue
            if not (parse_bool(mutant.get("executable")) and mutant.get("build_status") == "SUCCESS"):
                continue

            identity = (*run.campaign.key, run.run_name, run.target_id, mutant_id)
            if identity in seen_keys:
                audit.append({
                    "kind": "duplicate_mutant_identity", "identity": list(identity),
                    "run_dir": str(run.run_dir),
                })
                continue
            seen_keys.add(identity)

            mapped = eligible_tests.get(run.target_id, set())
            observations = tests_by_mutant.get(mutant_id, [])
            observed_names = {
                str(row.get("test_name") or "") for row in observations
                if parse_bool(row.get("eligible"))
            }
            missing = sorted(mapped - observed_names)
            unexpected = sorted(observed_names - mapped)
            if missing or unexpected:
                audit.append({
                    "kind": "incomplete_mutant_tests",
                    "tool": run.campaign.tool,
                    "campaign_id": run.campaign.campaign_id,
                    "run_name": run.run_name,
                    "target_id": run.target_id,
                    "mutant_id": mutant_id,
                    "missing_tests": missing,
                    "unexpected_tests": unexpected,
                })
                continue

            killing = {
                str(row.get("test_name") or "")
                for row in observations
                if parse_bool(row.get("eligible"))
                and parse_bool(row.get("executed"))
                and str(row.get("outcome") or "").upper() == "FAIL"
            }
            oracle = oracle_tests.get(run.target_id, set())
            coupled = bool(killing & oracle)
            exact = bool(killing and oracle and killing == oracle)
            ochiai = (
                len(killing & oracle) / math.sqrt(len(killing) * len(oracle))
                if killing and oracle else 0.0
            )
            output.append({
                "tool": run.campaign.tool,
                "model": run.campaign.model,
                "num_mutants_requested": run.campaign.num_mutants_requested,
                "campaign_id": run.campaign.campaign_id,
                "run_name": run.run_name,
                "subject_id": run.subject_id,
                "target_id": run.target_id,
                "mutant_id": mutant_id,
                "mutant_hash": mutant.get("mutant_hash", ""),
                "killing_tests": "|".join(sorted(killing)),
                "oracle_tests": "|".join(sorted(oracle)),
                "n_killing_tests": len(killing),
                "n_oracle_tests": len(oracle),
                "coupled": coupled,
                "exact_match": exact,
                "ochiai": round(ochiai, 8),
                "source_run_dir": str(run.run_dir),
            })
    return output, audit


def _campaign_dict(campaign: Campaign) -> dict[str, str]:
    return {
        "tool": campaign.tool,
        "model": campaign.model,
        "num_mutants_requested": campaign.num_mutants_requested,
        "campaign_id": campaign.campaign_id,
    }


def aggregate_campaigns(
    *,
    campaigns: Iterable[Campaign],
    mutant_rows: list[dict],
    targets: list[dict],
    oracle_tests: dict[str, set[str]],
    discovered_runs: Iterable[RunData],
) -> tuple[list[dict], list[dict], list[dict]]:
    target_ids = [str(t["target_id"]) for t in targets if oracle_tests.get(str(t["target_id"]))]
    target_to_subject = {str(t["target_id"]): str(t["subject"]) for t in targets}
    subjects = sorted({target_to_subject[tid] for tid in target_ids})
    rows_by_campaign: dict[tuple, list[dict]] = defaultdict(list)
    present_by_campaign: dict[tuple, set[str]] = defaultdict(set)
    for row in mutant_rows:
        key = (row["tool"], row["model"], row["num_mutants_requested"], row["campaign_id"])
        rows_by_campaign[key].append(row)
    for run in discovered_runs:
        present_by_campaign[run.campaign.key].add(run.target_id)

    campaign_rows: list[dict] = []
    target_rows: list[dict] = []
    bug_rows: list[dict] = []
    for campaign in sorted(set(campaigns), key=lambda value: value.key):
        rows = rows_by_campaign.get(campaign.key, [])
        by_target: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_target[row["target_id"]].append(row)

        for target_id in target_ids:
            target_mutants = by_target.get(target_id, [])
            target_rows.append({
                **_campaign_dict(campaign),
                "subject_id": target_to_subject[target_id],
                "target_id": target_id,
                "run_present": target_id in present_by_campaign.get(campaign.key, set()),
                "n_mutants_executable": len(target_mutants),
                "n_mutants_coupled": sum(bool(r["coupled"]) for r in target_mutants),
                "n_mutants_exact": sum(bool(r["exact_match"]) for r in target_mutants),
                "target_coupled": any(bool(r["coupled"]) for r in target_mutants),
                "target_exact": any(bool(r["exact_match"]) for r in target_mutants),
            })

        for subject_id in subjects:
            subject_targets = [tid for tid in target_ids if target_to_subject[tid] == subject_id]
            subject_mutants = [r for tid in subject_targets for r in by_target.get(tid, [])]
            bug_rows.append({
                **_campaign_dict(campaign),
                "subject_id": subject_id,
                "n_targets": len(subject_targets),
                "n_mutants_executable": len(subject_mutants),
                "bug_coupled": any(bool(r["coupled"]) for r in subject_mutants),
                "bug_exact": any(bool(r["exact_match"]) for r in subject_mutants),
            })

        n_exec = len(rows)
        n_coupled = sum(bool(row["coupled"]) for row in rows)
        n_exact = sum(bool(row["exact_match"]) for row in rows)
        coupled_targets = sum(any(bool(r["coupled"]) for r in by_target.get(tid, [])) for tid in target_ids)
        exact_targets = sum(any(bool(r["exact_match"]) for r in by_target.get(tid, [])) for tid in target_ids)
        coupled_subjects = sum(
            any(bool(r["coupled"]) for tid in target_ids if target_to_subject[tid] == sid for r in by_target.get(tid, []))
            for sid in subjects
        )
        exact_subjects = sum(
            any(bool(r["exact_match"]) for tid in target_ids if target_to_subject[tid] == sid for r in by_target.get(tid, []))
            for sid in subjects
        )
        campaign_rows.append({
            **_campaign_dict(campaign),
            "n_targets_total": len(target_ids),
            "n_targets_with_run": len(present_by_campaign.get(campaign.key, set()) & set(target_ids)),
            "n_targets_coupled": coupled_targets,
            "n_targets_exact": exact_targets,
            "rbdr_target_coupled": round(coupled_targets / len(target_ids), 8) if target_ids else 0.0,
            "rbdr_target_exact": round(exact_targets / len(target_ids), 8) if target_ids else 0.0,
            "n_bugs_total": len(subjects),
            "n_bugs_coupled": coupled_subjects,
            "n_bugs_exact": exact_subjects,
            "rbdr_bug_coupled": round(coupled_subjects / len(subjects), 8) if subjects else 0.0,
            "rbdr_bug_exact": round(exact_subjects / len(subjects), 8) if subjects else 0.0,
            "n_mutants_executable": n_exec,
            "n_mutants_coupled": n_coupled,
            "n_mutants_exact": n_exact,
            "coupling_rate": round(n_coupled / n_exec, 8) if n_exec else 0.0,
            "exact_match_rate": round(n_exact / n_exec, 8) if n_exec else 0.0,
            "avg_ochiai": round(sum(float(r["ochiai"]) for r in rows) / n_exec, 8) if n_exec else 0.0,
        })
    return campaign_rows, target_rows, bug_rows


def aggregate_conditions(campaign_rows: list[dict]) -> list[dict]:
    """Summarize campaign metrics without merging mutant identities.

    A condition is (tool, model, requested count). Multiple repetitions remain
    visible through n_campaigns and are averaged rather than pooled, avoiding
    collisions between repeated m01/m02 identifiers.
    """
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in campaign_rows:
        groups[(row["tool"], row["model"], row["num_mutants_requested"])].append(row)
    output = []
    metric_fields = [
        "rbdr_target_coupled", "rbdr_target_exact", "rbdr_bug_coupled",
        "rbdr_bug_exact", "coupling_rate", "exact_match_rate", "avg_ochiai",
    ]
    for (tool, model, requested), rows in sorted(groups.items()):
        item = {
            "tool": tool, "model": model, "num_mutants_requested": requested,
            "n_campaigns": len(rows),
            "n_mutants_executable_total": sum(int(r["n_mutants_executable"]) for r in rows),
        }
        for field in metric_fields:
            item[field] = round(sum(float(r[field]) for r in rows) / len(rows), 8)
        output.append(item)
    return output


def build_fault_coupling(
    *,
    catalog_file: Path,
    output_dir: Path,
    llm_roots: Iterable[Path] = (),
    mull_roots: Iterable[Path] = (),
    target_tests_file: Path | None = None,
) -> dict:
    targets, targets_by_id = load_catalog(catalog_file)
    oracle_tests, eligible_tests, mapping_rows = load_oracles(catalog_file, target_tests_file)
    missing_oracles = sorted(set(targets_by_id) - set(oracle_tests))
    if missing_oracles:
        raise ValueError(
            "Every catalog target must have at least one match_mode=oracle test; "
            f"missing for {len(missing_oracles)} target(s): {', '.join(missing_oracles)}"
        )

    llm_runs, llm_audit = discover_runs(
        tool="llm", roots=llm_roots, catalog_target_ids=set(targets_by_id)
    )
    mull_runs, mull_audit = discover_runs(
        tool="mull", roots=mull_roots, catalog_target_ids=set(targets_by_id)
    )
    runs = llm_runs + mull_runs
    mutant_rows, mutant_audit = compute_mutant_rows(runs, oracle_tests, eligible_tests)
    campaigns = {run.campaign for run in runs}
    campaign_rows, target_rows, bug_rows = aggregate_campaigns(
        campaigns=campaigns, mutant_rows=mutant_rows, targets=targets,
        oracle_tests=oracle_tests, discovered_runs=runs,
    )
    condition_rows = aggregate_conditions(campaign_rows)

    evaluable_rows = [{
        "target_id": str(target["target_id"]),
        "subject_id": str(target["subject"]),
        "function_name": str(target.get("function") or ""),
        "oracle_tests": "|".join(sorted(oracle_tests[str(target["target_id"])])),
        "eligible_tests": "|".join(sorted(eligible_tests.get(str(target["target_id"]), set()))),
    } for target in targets]
    oracle_rows = [
        {"target_id": target_id, "test_name": test_name, "match_mode": "oracle"}
        for target_id in sorted(oracle_tests) for test_name in sorted(oracle_tests[target_id])
    ]

    write_csv(output_dir / "evaluable_targets.csv", list(evaluable_rows[0]), evaluable_rows)
    write_csv(output_dir / "oracle_tests.csv", ["target_id", "test_name", "match_mode"], oracle_rows)
    write_csv(output_dir / "mutant_level.csv", MUTANT_LEVEL_FIELDS, mutant_rows)
    write_csv(output_dir / "target_level.csv", list(target_rows[0]) if target_rows else [], target_rows)
    write_csv(output_dir / "bug_level.csv", list(bug_rows[0]) if bug_rows else [], bug_rows)
    write_csv(output_dir / "campaign_results.csv", list(campaign_rows[0]) if campaign_rows else [], campaign_rows)
    write_csv(output_dir / "aggregate_results.csv", list(condition_rows[0]) if condition_rows else [], condition_rows)

    issues = llm_audit + mull_audit + mutant_audit
    audit = {
        "catalog_file": str(catalog_file),
        "target_tests_file": str(target_tests_file or catalog_target_tests_csv_path(catalog_file)),
        "n_catalog_targets": len(targets),
        "n_oracle_tests": sum(len(value) for value in oracle_tests.values()),
        "n_mapping_rows": len(mapping_rows),
        "n_llm_runs": len(llm_runs),
        "n_mull_runs": len(mull_runs),
        "n_campaigns": len(campaigns),
        "n_executable_mutants_included": len(mutant_rows),
        "n_issues": len(issues),
        "issues": issues,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return {
        "audit": audit,
        "mutant_rows": mutant_rows,
        "campaign_rows": campaign_rows,
        "condition_rows": condition_rows,
        "target_rows": target_rows,
        "bug_rows": bug_rows,
    }
