from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


@dataclass
class Summary:
    total_mutants: int
    build_successes: int
    executable_mutants: int
    killed_mutants: int
    baseline_failures: int

    @property
    def build_success_rate(self) -> float:
        return self.build_successes / self.total_mutants if self.total_mutants else 0.0

    @property
    def executable_yield(self) -> float:
        return self.executable_mutants / self.total_mutants if self.total_mutants else 0.0

    @property
    def mutation_score(self) -> float | None:
        if self.executable_mutants == 0:
            return None
        return self.killed_mutants / self.executable_mutants

    def to_dict(self) -> dict:
        data = asdict(self)
        data["build_success_rate"] = self.build_success_rate
        data["executable_yield"] = self.executable_yield
        data["mutation_score"] = self.mutation_score
        return data


KEY_FIELDS = ("dataset", "subject_id", "function_name", "mutant_id")


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"No result rows found in {csv_path}")
    return rows


def deduplicate_rows(rows: Iterable[dict]) -> list[dict]:
    latest: dict[tuple[str, str, str, str], dict] = {}
    for row in rows:
        key = tuple(row.get(field, "") for field in KEY_FIELDS)
        latest[key] = row
    return list(latest.values())


def build_summary(rows: Iterable[dict]) -> Summary:
    rows = list(rows)
    total_mutants = len(rows)
    build_successes = sum(row.get("build_status") == "SUCCESS" for row in rows)
    executable_mutants = sum(parse_bool(row.get("executable", "")) for row in rows)
    killed_mutants = sum(parse_bool(row.get("killed", "")) for row in rows)
    baseline_failures = sum(row.get("build_status") == "BASELINE_FAIL" for row in rows)

    return Summary(
        total_mutants=total_mutants,
        build_successes=build_successes,
        executable_mutants=executable_mutants,
        killed_mutants=killed_mutants,
        baseline_failures=baseline_failures,
    )


def format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def group_rows(rows: Iterable[dict], fields: tuple[str, ...]) -> dict[tuple[str, ...], list[dict]]:
    grouped: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(field, "") for field in fields)
        grouped[key].append(row)
    return dict(grouped)


def print_summary(title: str, summary: Summary) -> None:
    print(title)
    print(f"  Total mutants:       {summary.total_mutants}")
    print(f"  Build successes:     {summary.build_successes} ({format_pct(summary.build_success_rate)})")
    print(f"  Executable mutants:  {summary.executable_mutants} ({format_pct(summary.executable_yield)})")
    print(f"  Killed mutants:      {summary.killed_mutants}")
    print(f"  Mutation score:      {format_pct(summary.mutation_score)}")
    print(f"  Baseline failures:   {summary.baseline_failures}")


def default_json_path(csv_path: Path) -> Path:
    return csv_path.parent / "summary.json"


def summarize_results_csv(
    csv_path: Path,
    keep_duplicates: bool = False,
    json_out: Path | None = None,
    print_to_stdout: bool = True,
) -> dict:
    csv_path = Path(csv_path)
    json_path = Path(json_out) if json_out is not None else default_json_path(csv_path)

    rows = load_rows(csv_path)
    input_count = len(rows)

    if not keep_duplicates:
        rows = deduplicate_rows(rows)

    overall = build_summary(rows)
    grouped = group_rows(rows, ("dataset", "subject_id", "function_name"))

    grouped_output = []
    for key in sorted(grouped.keys()):
        dataset, subject_id, function_name = key
        summary = build_summary(grouped[key])

        if print_to_stdout:
            if not grouped_output:
                print_summary("Overall", overall)
                print()

            print_summary(
                f"Group: dataset={dataset}, subject={subject_id}, function={function_name}",
                summary,
            )
            print()

        grouped_output.append(
            {
                "dataset": dataset,
                "subject_id": subject_id,
                "function_name": function_name,
                **summary.to_dict(),
            }
        )

    if print_to_stdout and not grouped_output:
        print_summary("Overall", overall)

    if print_to_stdout and input_count != len(rows):
        print(f"Deduplicated repeated result rows: {input_count} -> {len(rows)}")
        print()

    payload = {
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "deduplicated": not keep_duplicates,
        "input_row_count": input_count,
        "used_row_count": len(rows),
        "overall": overall.to_dict(),
        "by_subject_function": grouped_output,
    }

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if print_to_stdout:
        print(f"JSON summary written to: {json_path}")

    return payload
