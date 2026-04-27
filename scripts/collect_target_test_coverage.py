#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

from harness.adapters.defects4j import Defects4JAdapter
from harness.models import Subject
from harness.targets.catalog import load_catalog_entries
from scripts.build_target_test_catalog import (
    CATALOGS_DIR,
    TARGET_TESTS_FIELDNAMES,
    catalog_name,
    project_from_subject,
)


WORK_ROOT = Path("tmp/target_coverage")


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def subject_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        entry.get("dataset", "defects4j"),
        entry.get("subject") or entry.get("subject_id") or "",
        entry.get("version", "f"),
        entry.get("language", "java"),
    )


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "subject"


def checkout_subject(
    *,
    dataset: str,
    subject_id: str,
    version: str,
    language: str,
    workdir: Path,
    reuse_checkout: bool,
) -> None:
    if reuse_checkout and (workdir / ".defects4j.config").exists():
        return

    adapter = Defects4JAdapter()
    adapter.checkout_subject(
        Subject(
            dataset=dataset,
            subject_id=subject_id,
            language=language,
            version=version,
        ),
        str(workdir),
    )


def export_property(workdir: Path, property_name: str) -> list[str]:
    result = run_cmd(["defects4j", "export", "-p", property_name, "-w", str(workdir)])
    if result.returncode != 0:
        raise RuntimeError(f"Failed to export {property_name} for {workdir}:\n{result.stderr}")

    values = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith("Running "):
            continue
        values.append(text)
    return sorted(set(values))


def export_tests(workdir: Path) -> list[str]:
    exported = export_property(workdir, "tests.all")
    if any("::" in item for item in exported):
        return sorted({item for item in exported if "::" in item})

    test_src_dirs = export_property(workdir, "dir.src.tests")
    return expand_test_classes_to_methods(workdir, exported, test_src_dirs)


def expand_test_classes_to_methods(
    workdir: Path,
    test_classes: list[str],
    test_src_dirs: list[str],
) -> list[str]:
    tests: set[str] = set()
    for test_class in test_classes:
        source_path = find_test_source(workdir, test_class, test_src_dirs)
        if source_path is None:
            continue
        for method in extract_test_methods(source_path):
            tests.add(f"{test_class}::{method}")
    return sorted(tests)


def find_test_source(
    workdir: Path,
    test_class: str,
    test_src_dirs: list[str],
) -> Path | None:
    rel_path = Path(*test_class.split(".")).with_suffix(".java")

    for test_src_dir in test_src_dirs:
        candidate = workdir / test_src_dir / rel_path
        if candidate.exists():
            return candidate

    simple_name = test_class.rsplit(".", 1)[-1] + ".java"
    for test_src_dir in test_src_dirs:
        matches = list((workdir / test_src_dir).rglob(simple_name))
        if len(matches) == 1:
            return matches[0]

    return None


def extract_test_methods(source_path: Path) -> list[str]:
    text = source_path.read_text(encoding="utf-8", errors="replace")
    methods: set[str] = set()

    annotation_pattern = re.compile(
        r"@\s*Test(?:\s*\([^)]*\))?\s+"
        r"(?:public|protected|private)?\s*"
        r"(?:static\s+)?(?:final\s+)?[\w<>\[\], ?]+\s+"
        r"([A-Za-z_]\w*)\s*\(",
        re.MULTILINE,
    )
    methods.update(annotation_pattern.findall(text))

    junit3_pattern = re.compile(
        r"(?:public|protected)\s+void\s+(test[A-Za-z_0-9]*)\s*\(",
        re.MULTILINE,
    )
    methods.update(junit3_pattern.findall(text))

    return sorted(methods)


def fqcn_for_source_file(workdir: Path, file_path: str) -> str:
    source_path = workdir / file_path
    text = source_path.read_text(encoding="utf-8", errors="replace")

    package_match = re.search(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", text, re.MULTILINE)
    package = package_match.group(1) if package_match else ""
    class_name = Path(file_path).stem
    return f"{package}.{class_name}" if package else class_name


def write_instrument_classes(path: Path, class_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(class_name + "\n", encoding="utf-8")


def covered_lines_from_xml(xml_path: Path, class_filename: str) -> set[int]:
    if not xml_path.exists():
        return set()

    root = ET.parse(xml_path).getroot()
    covered: set[int] = set()

    for class_el in root.findall(".//class"):
        filename = class_el.attrib.get("filename", "")
        if filename and Path(filename).name != Path(class_filename).name:
            continue

        for line_el in class_el.findall(".//line"):
            hits = int(line_el.attrib.get("hits", "0") or 0)
            if hits <= 0:
                continue
            number = line_el.attrib.get("number")
            if number and number.isdigit():
                covered.add(int(number))

    return covered


def target_is_covered(xml_path: Path, entry: dict[str, Any]) -> bool:
    start_line = int(entry["start_line"])
    end_line = int(entry["end_line"])
    covered = covered_lines_from_xml(xml_path, entry["file"])
    return any(start_line <= line <= end_line for line in covered)


def coverage_for_test(
    *,
    workdir: Path,
    test_name: str,
    instrument_classes: Path,
    timeout: int,
) -> tuple[bool, str]:
    result = run_cmd(
        [
            "defects4j",
            "coverage",
            "-w",
            str(workdir),
            "-t",
            test_name,
            "-i",
            str(instrument_classes),
        ],
        timeout=timeout,
    )
    return result.returncode == 0, result.stdout + "\n" + result.stderr


def coverage_row(
    *,
    catalog: str,
    entry: dict[str, Any],
    test_name: str,
    coverage_source: str,
) -> dict[str, Any]:
    subject_id = entry.get("subject") or entry.get("subject_id") or ""
    return {
        "catalog": catalog,
        "dataset": entry.get("dataset", ""),
        "subject_id": subject_id,
        "version": entry.get("version", ""),
        "project": project_from_subject(subject_id),
        "target_id": entry.get("target_id", ""),
        "file_path": entry.get("file", ""),
        "start_line": entry.get("start_line", ""),
        "end_line": entry.get("end_line", ""),
        "test_name": test_name,
        "coverage_source": coverage_source,
    }


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_TESTS_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def collect_for_catalog(
    *,
    catalog_path: Path,
    output_path: Path,
    work_root: Path,
    target_ids: set[str] | None,
    test_names: set[str] | None,
    test_limit: int | None,
    timeout: int,
    reuse_checkout: bool,
) -> Path:
    entries = load_catalog_entries(str(catalog_path))
    if target_ids:
        entries = [entry for entry in entries if entry.get("target_id") in target_ids]

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped[subject_key(entry)].append(entry)

    catalog = catalog_name(catalog_path)
    rows: list[dict[str, Any]] = []

    for (dataset, subject_id, version, language), subject_entries in sorted(grouped.items()):
        subject_workdir = work_root / catalog / safe_name(f"{subject_id}_{version}")
        print(f"[coverage] checkout {subject_id} {version} -> {subject_workdir}", flush=True)
        checkout_subject(
            dataset=dataset,
            subject_id=subject_id,
            version=version,
            language=language,
            workdir=subject_workdir,
            reuse_checkout=reuse_checkout,
        )

        tests = sorted(test_names) if test_names else export_tests(subject_workdir)
        if test_limit is not None:
            tests = tests[:test_limit]
        print(f"[coverage] {subject_id}: {len(tests)} test(s)", flush=True)

        entries_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in subject_entries:
            entries_by_class[fqcn_for_source_file(subject_workdir, entry["file"])].append(entry)

        for class_name, class_entries in sorted(entries_by_class.items()):
            instrument_path = subject_workdir / ".target_coverage" / f"{safe_name(class_name)}.classes"
            write_instrument_classes(instrument_path, class_name)

            target_list = ", ".join(entry.get("target_id", "") for entry in class_entries)
            print(f"[coverage] class {class_name}: {target_list}", flush=True)

            for test_name in tests:
                ok, log_text = coverage_for_test(
                    workdir=subject_workdir,
                    test_name=test_name,
                    instrument_classes=instrument_path,
                    timeout=timeout,
                )
                log_dir = subject_workdir / ".target_coverage" / "logs" / safe_name(class_name)
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / f"{safe_name(test_name)}.log").write_text(log_text, encoding="utf-8")

                if not ok:
                    continue

                xml_path = subject_workdir / "coverage.xml"
                for entry in class_entries:
                    if target_is_covered(xml_path, entry):
                        rows.append(
                            coverage_row(
                                catalog=catalog,
                                entry=entry,
                                test_name=test_name,
                                coverage_source="defects4j-coverage-per-test",
                            )
                        )

    rows.sort(
        key=lambda row: (
            str(row.get("subject_id", "")),
            str(row.get("target_id", "")),
            str(row.get("test_name", "")),
        )
    )
    write_rows(output_path, rows)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate per-catalog target_tests.csv using Defects4J per-test coverage."
    )
    parser.add_argument("catalog", help="Catalog JSON to analyze.")
    parser.add_argument("--output", help="Output target_tests.csv path.")
    parser.add_argument("--work-root", default=str(WORK_ROOT))
    parser.add_argument("--target-id", action="append", help="Restrict to one target id. Repeatable.")
    parser.add_argument(
        "--test-name",
        action="append",
        help="Restrict to one test, formatted Class::method. Repeatable.",
    )
    parser.add_argument("--test-limit", type=int, help="Debug limit for number of tests per subject.")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--reuse-checkout", action="store_true")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the catalog work directory before starting.",
    )
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    catalog = catalog_name(catalog_path)
    output_path = (
        Path(args.output)
        if args.output
        else CATALOGS_DIR / catalog / "target_tests.csv"
    )
    work_root = Path(args.work_root)

    if args.clean:
        shutil.rmtree(work_root / catalog, ignore_errors=True)

    target_ids = set(args.target_id) if args.target_id else None
    test_names = set(args.test_name) if args.test_name else None
    path = collect_for_catalog(
        catalog_path=catalog_path,
        output_path=output_path,
        work_root=work_root,
        target_ids=target_ids,
        test_names=test_names,
        test_limit=args.test_limit,
        timeout=args.timeout,
        reuse_checkout=args.reuse_checkout,
    )
    print(f"Wrote target-test coverage mapping: {path}")


if __name__ == "__main__":
    main()
