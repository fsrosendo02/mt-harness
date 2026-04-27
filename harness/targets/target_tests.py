from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from harness.models import Subject, Target
from harness.storage.layout import (
    catalog_target_tests_csv_path,
    global_target_tests_csv_path,
)


DEFAULT_TARGET_TESTS_CSV = global_target_tests_csv_path()


def _key(dataset: str, subject_id: str, target_id: str) -> tuple[str, str, str]:
    return (
        str(dataset or "").strip(),
        str(subject_id or "").strip(),
        str(target_id or "").strip(),
    )


def load_target_test_map(
    csv_path: str | Path | None = None,
    *,
    catalog_file: str | Path | None = None,
) -> dict[tuple[str, str, str], list[str]]:
    if csv_path is not None:
        path = Path(csv_path)
    elif catalog_file is not None:
        path = catalog_target_tests_csv_path(catalog_file)
    else:
        path = Path(DEFAULT_TARGET_TESTS_CSV)

    if not path.exists():
        return {}

    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            test_name = str(row.get("test_name") or "").strip()
            if not test_name:
                continue
            grouped[_key(row.get("dataset", ""), row.get("subject_id", ""), row.get("target_id", ""))].add(
                test_name
            )

    return {key: sorted(values) for key, values in grouped.items()}


def target_tests_for(
    subject: Subject,
    target: Target,
    target_test_map: dict[tuple[str, str, str], list[str]] | None,
) -> list[str]:
    if not target_test_map:
        return []
    return list(
        target_test_map.get(
            _key(subject.dataset, subject.subject_id, getattr(target, "target_id", None) or ""),
            [],
        )
    )
