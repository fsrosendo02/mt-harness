#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.adapters.defects4j import Defects4JAdapter
from harness.models import Subject
from harness.storage.layout import (
    catalogs_root,
    catalog_name_from_path,
    catalog_target_tests_csv_path,
)
from harness.targets.validation import validate_catalog
from harness.targets.test_coverage_templates import (
    TARGET_TESTS_FIELDNAMES,
    project_from_subject,
)


WORK_ROOT = Path("tmp/target_coverage")
LOGS_ROOT = Path("logs/coverage")


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def timestamp_lines(text: str) -> list[str]:
    lines = text.splitlines() or [text]
    return [f"[{timestamp()}] {line}" for line in lines]


def log(message: str, *, log_path: Path | None = None) -> None:
    lines = timestamp_lines(message)
    for line in lines:
        print(line, flush=True)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")


def write_timestamped_text_log(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in timestamp_lines(text):
            f.write(line + "\n")


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
    log_path: Path | None = None,
) -> None:
    if reuse_checkout and (workdir / ".defects4j.config").exists():
        log(f"[coverage] reuse checkout {subject_id} {version} -> {workdir}", log_path=log_path)
        return

    if dataset != "defects4j":
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
        return

    project, bug_id = subject_id.split("_", 1)
    checkout_version = f"{bug_id}{version}"

    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "defects4j",
        "checkout",
        "-p",
        project,
        "-v",
        checkout_version,
        "-w",
        str(workdir.resolve()),
    ]
    result = run_cmd(cmd)
    checkout_output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
    if checkout_output.strip():
        log(checkout_output.rstrip(), log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(
            f"defects4j checkout failed for {subject_id} {version} with exit code {result.returncode}"
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


def export_tests_with_metadata(workdir: Path) -> tuple[list[str], dict[str, Any]]:
    exported = export_property(workdir, "tests.all")
    metadata: dict[str, Any] = {
        "mode": "direct_methods" if any("::" in item for item in exported) else "class_expansion",
        "exported_count": len(exported),
        "exported_preview": exported[:5],
    }

    if metadata["mode"] == "direct_methods":
        tests = sorted({item for item in exported if "::" in item})
        metadata["resolved_count"] = len(tests)
        return tests, metadata

    test_src_dirs = export_property(workdir, "dir.src.tests")
    tests, expansion_meta = expand_test_classes_to_methods_with_metadata(
        workdir,
        exported,
        test_src_dirs,
    )
    metadata["test_src_dirs"] = test_src_dirs
    metadata.update(expansion_meta)
    return tests, metadata


def expand_test_classes_to_methods(
    workdir: Path,
    test_classes: list[str],
    test_src_dirs: list[str],
) -> list[str]:
    tests, _ = expand_test_classes_to_methods_with_metadata(workdir, test_classes, test_src_dirs)
    return tests


def expand_test_classes_to_methods_with_metadata(
    workdir: Path,
    test_classes: list[str],
    test_src_dirs: list[str],
) -> tuple[list[str], dict[str, Any]]:
    tests: set[str] = set()
    unresolved_classes: list[str] = []
    classes_without_methods: list[str] = []
    for test_class in test_classes:
        source_path = find_test_source(workdir, test_class, test_src_dirs)
        if source_path is None:
            unresolved_classes.append(test_class)
            continue
        methods = extract_test_methods(source_path)
        if not methods:
            classes_without_methods.append(test_class)
            continue
        for method in methods:
            tests.add(f"{test_class}::{method}")
    return sorted(tests), {
        "resolved_count": len(tests),
        "unresolved_class_count": len(unresolved_classes),
        "unresolved_classes_preview": unresolved_classes[:5],
        "classes_without_methods_count": len(classes_without_methods),
        "classes_without_methods_preview": classes_without_methods[:5],
    }


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


def write_instrument_classes(path: Path, class_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    unique_names = sorted({name.strip() for name in class_names if name.strip()})
    path.write_text("\n".join(unique_names) + "\n", encoding="utf-8")


def normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


def class_name_matches(xml_class_name: str, expected_class_name: str) -> bool:
    normalized_xml = xml_class_name.replace("/", ".").strip()
    expected = expected_class_name.strip()
    expected_simple = expected.rsplit(".", 1)[-1]
    xml_simple = normalized_xml.rsplit(".", 1)[-1]
    return (
        normalized_xml == expected
        or normalized_xml.endswith(f".{expected_simple}")
        or xml_simple == expected_simple
    )


def filename_matches(xml_filename: str, class_filename: str) -> bool:
    normalized_xml = normalize_rel_path(xml_filename)
    normalized_target = normalize_rel_path(class_filename)
    return (
        normalized_xml == normalized_target
        or normalized_target.endswith(normalized_xml)
        or normalized_xml.endswith(normalized_target)
        or Path(normalized_xml).name == Path(normalized_target).name
    )


def covered_lines_from_xml(
    xml_path: Path,
    class_filename: str,
    *,
    expected_class_name: str | None = None,
) -> tuple[set[int], dict[str, Any]]:
    if not xml_path.exists():
        return set(), {"match_count": 0, "matched_classes": [], "match_mode": "missing_xml"}

    root = ET.parse(xml_path).getroot()
    covered: set[int] = set()
    matched_classes: list[dict[str, str]] = []
    matched_by_strict = False

    for class_el in root.findall(".//class"):
        filename = class_el.attrib.get("filename", "")
        xml_class_name = class_el.attrib.get("name", "")

        filename_ok = filename_matches(filename, class_filename) if filename else False
        class_ok = (
            class_name_matches(xml_class_name, expected_class_name)
            if expected_class_name and xml_class_name
            else expected_class_name is None
        )

        # Prefer strong matches that use both class and filename when available.
        if expected_class_name and xml_class_name and filename:
            if not (filename_ok and class_ok):
                continue
            matched_by_strict = True
        elif filename:
            if not filename_ok:
                continue
        elif expected_class_name and xml_class_name:
            if not class_ok:
                continue
        else:
            continue

        matched_classes.append({"name": xml_class_name, "filename": filename})
        for line_el in class_el.findall(".//line"):
            hits = int(line_el.attrib.get("hits", "0") or 0)
            if hits <= 0:
                continue
            number = line_el.attrib.get("number")
            if number and number.isdigit():
                covered.add(int(number))

    match_mode = "strict_class_and_file" if matched_by_strict else "fallback_filename_or_class"
    return covered, {
        "match_count": len(matched_classes),
        "matched_classes": matched_classes,
        "match_mode": match_mode,
    }


def target_is_covered(
    xml_path: Path,
    entry: dict[str, Any],
    *,
    expected_class_name: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    start_line = int(entry["start_line"])
    end_line = int(entry["end_line"])
    covered, match_meta = covered_lines_from_xml(
        xml_path,
        entry["file"],
        expected_class_name=expected_class_name,
    )
    matched_lines = sorted(line for line in covered if start_line <= line <= end_line)
    return bool(matched_lines), {
        **match_meta,
        "covered_line_count": len(covered),
        "matched_lines": matched_lines,
    }


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
    match_mode: str,
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
        "match_mode": match_mode,
    }


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_TESTS_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def persist_rows(path: Path, rows: list[dict[str, Any]], *, log_path: Path, reason: str) -> None:
    normalized_rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("subject_id", "")),
            str(row.get("target_id", "")),
            str(row.get("test_name", "")),
            str(row.get("match_mode", "")),
        ),
    )
    write_rows(path, normalized_rows)
    log(
        f"[coverage] checkpoint wrote {len(normalized_rows)} row(s) to {path} reason={reason}",
        log_path=log_path,
    )


def collect_for_catalog(
    *,
    catalog_path: Path,
    output_path: Path,
    work_root: Path,
    log_path: Path,
    target_ids: set[str] | None,
    test_names: set[str] | None,
    test_limit: int | None,
    timeout: int,
    reuse_checkout: bool,
) -> Path:
    entries = validate_catalog(catalog_path)
    if target_ids:
        entries = [entry for entry in entries if entry.get("target_id") in target_ids]

    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped[subject_key(entry)].append(entry)

    catalog = catalog_name_from_path(catalog_path)
    rows: list[dict[str, Any]] = []

    log(
        f"[coverage] start catalog={catalog} output={output_path} work_root={work_root}",
        log_path=log_path,
    )

    for (dataset, subject_id, version, language), subject_entries in sorted(grouped.items()):
        subject_workdir = work_root / catalog / safe_name(f"{subject_id}_{version}")
        log(
            f"[coverage] checkout {subject_id} {version} -> {subject_workdir}",
            log_path=log_path,
        )
        checkout_subject(
            dataset=dataset,
            subject_id=subject_id,
            version=version,
            language=language,
            workdir=subject_workdir,
            reuse_checkout=reuse_checkout,
            log_path=log_path,
        )

        if test_names:
            tests = sorted(test_names)
            tests_meta = {
                "mode": "manual_filter",
                "exported_count": len(tests),
                "resolved_count": len(tests),
            }
        else:
            tests, tests_meta = export_tests_with_metadata(subject_workdir)
        if test_limit is not None:
            tests = tests[:test_limit]
            tests_meta = dict(tests_meta)
            tests_meta["limited_to"] = test_limit
        log(
            f"[coverage] {subject_id}: {len(tests)} test(s) "
            f"(mode={tests_meta.get('mode')} exported={tests_meta.get('exported_count')} "
            f"resolved={tests_meta.get('resolved_count', len(tests))})",
            log_path=log_path,
        )
        if tests_meta.get("unresolved_class_count"):
            log(
                f"[coverage] WARN {subject_id}: unresolved test classes="
                f"{tests_meta['unresolved_class_count']} preview={tests_meta.get('unresolved_classes_preview')}",
                log_path=log_path,
            )
        if tests_meta.get("classes_without_methods_count"):
            log(
                f"[coverage] WARN {subject_id}: classes without discovered test methods="
                f"{tests_meta['classes_without_methods_count']} preview={tests_meta.get('classes_without_methods_preview')}",
                log_path=log_path,
            )
        if not tests:
            log(f"[coverage] WARN {subject_id}: no tests resolved for subject", log_path=log_path)

        entries_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in subject_entries:
            entries_by_class[fqcn_for_source_file(subject_workdir, entry["file"])].append(entry)

        class_items = sorted(entries_by_class.items())
        instrument_path = subject_workdir / ".target_coverage" / "instrumented_subject.classes"
        write_instrument_classes(instrument_path, [class_name for class_name, _ in class_items])
        log(
            f"[coverage] subject {subject_id}: instrumenting {len(class_items)} class(es) in one coverage run per test",
            log_path=log_path,
        )

        class_stats: dict[str, dict[str, Any]] = {}
        warned_missing_match_targets: dict[str, set[str]] = defaultdict(set)
        warned_fallback_match_targets: dict[str, set[str]] = defaultdict(set)
        for class_index, (class_name, class_entries) in enumerate(class_items, start=1):
            target_list = ", ".join(entry.get("target_id", "") for entry in class_entries)
            log(
                f"[coverage] class {class_index}/{len(class_items)} {class_name}: {target_list}",
                log_path=log_path,
            )
            class_stats[class_name] = {
                "ok_tests": 0,
                "failed_tests": 0,
                "coverage_hits": 0,
                "targets_with_hits": set(),
            }

        for test_index, test_name in enumerate(tests, start=1):
            log(
                f"[coverage] subject-test {test_index}/{len(tests)} start {subject_id} :: {test_name}",
                log_path=log_path,
            )
            ok, log_text = coverage_for_test(
                workdir=subject_workdir,
                test_name=test_name,
                instrument_classes=instrument_path,
                timeout=timeout,
            )
            test_log_dir = subject_workdir / ".target_coverage" / "logs" / "subject"
            write_timestamped_text_log(test_log_dir / f"{safe_name(test_name)}.log", log_text)

            for class_name in class_stats:
                if ok:
                    class_stats[class_name]["ok_tests"] += 1
                else:
                    class_stats[class_name]["failed_tests"] += 1

            log(
                f"[coverage] subject-test {test_index}/{len(tests)} finished ok={ok} {subject_id} :: {test_name}",
                log_path=log_path,
            )

            if not ok:
                failure_excerpt = next(
                    (
                        line.strip()
                        for line in log_text.splitlines()
                        if line.strip() and not line.strip().startswith("Running ")
                    ),
                    "no diagnostic output",
                )
                log(
                    f"[coverage] note ok=False means this defects4j coverage invocation "
                    f"did not produce usable coverage; test skipped for mapping. "
                    f"reason={failure_excerpt}",
                    log_path=log_path,
                )
                persist_rows(
                    output_path,
                    rows,
                    log_path=log_path,
                    reason=f"{subject_id}:failed_test:{test_index}",
                )
                continue

            xml_path = subject_workdir / "coverage.xml"
            hits_for_test = 0
            for class_name, class_entries in class_items:
                any_hit_for_class = False
                for entry in class_entries:
                    is_covered, coverage_meta = target_is_covered(
                        xml_path,
                        entry,
                        expected_class_name=class_name,
                    )
                    target_id = str(entry.get("target_id", ""))
                    if (
                        coverage_meta.get("match_count", 0) == 0
                        and target_id not in warned_missing_match_targets[class_name]
                    ):
                        warned_missing_match_targets[class_name].add(target_id)
                        log(
                            f"[coverage] WARN no XML class match for target {target_id} "
                            f"class={class_name} file={entry.get('file')}",
                            log_path=log_path,
                        )
                    elif (
                        coverage_meta.get("match_mode") != "strict_class_and_file"
                        and target_id not in warned_fallback_match_targets[class_name]
                    ):
                        warned_fallback_match_targets[class_name].add(target_id)
                        log(
                            f"[coverage] WARN fallback XML match for target {target_id} "
                            f"class={class_name} mode={coverage_meta.get('match_mode')}",
                            log_path=log_path,
                        )

                    if is_covered:
                        any_hit_for_class = True
                        hits_for_test += 1
                        class_stats[class_name]["coverage_hits"] += 1
                        class_stats[class_name]["targets_with_hits"].add(target_id)
                        rows.append(
                            coverage_row(
                                catalog=catalog,
                                entry=entry,
                                test_name=test_name,
                                coverage_source="defects4j-coverage-per-test",
                                match_mode=str(coverage_meta.get("match_mode") or ""),
                            )
                        )
                if not any_hit_for_class:
                    log(
                        f"[coverage] no target hit for test {test_name} in class {class_name}",
                        log_path=log_path,
                    )

            persist_rows(
                output_path,
                rows,
                log_path=log_path,
                reason=f"{subject_id}:test:{test_index}",
            )
            log(
                f"[coverage] subject-test {test_index}/{len(tests)} summary hits={hits_for_test}",
                log_path=log_path,
            )

        for class_name, stats in class_stats.items():
            log(
                f"[coverage] class summary {class_name}: ok_tests={stats['ok_tests']} "
                f"failed_tests={stats['failed_tests']} coverage_hits={stats['coverage_hits']} "
                f"targets_with_hits={len(stats['targets_with_hits'])}",
                log_path=log_path,
            )

    persist_rows(output_path, rows, log_path=log_path, reason="final")
    log(
        f"[coverage] wrote {len(rows)} target-test row(s) to {output_path} "
        f"(overwrite mode: file regenerated from scratch)",
        log_path=log_path,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate per-catalog target_tests.csv using Defects4J per-test coverage."
    )
    parser.add_argument(
        "catalog",
        help="Catalog JSON path or catalog stem under harness/datasets/catalogs.",
    )
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
    reuse_group = parser.add_mutually_exclusive_group()
    reuse_group.add_argument(
        "--reuse-checkout",
        dest="reuse_checkout",
        action="store_true",
        help="Reuse an existing subject checkout when available.",
    )
    reuse_group.add_argument(
        "--no-reuse-checkout",
        dest="reuse_checkout",
        action="store_false",
        help="Force a fresh checkout even if one already exists.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the catalog work directory before starting.",
    )
    parser.set_defaults(reuse_checkout=True)
    args = parser.parse_args()

    catalog_arg = Path(args.catalog)
    catalog_path = catalog_arg if catalog_arg.exists() else catalogs_root() / f"{args.catalog}.json"
    catalog = catalog_name_from_path(catalog_path)
    output_path = (
        Path(args.output)
        if args.output
        else catalog_target_tests_csv_path(catalog_path)
    )
    work_root = Path(args.work_root)
    log_path = LOGS_ROOT / f"{catalog}__coverage_{timestamp_slug()}.log"

    if args.clean:
        shutil.rmtree(work_root / catalog, ignore_errors=True)
        log(f"[coverage] cleaned work directory {work_root / catalog}", log_path=log_path)

    target_ids = set(args.target_id) if args.target_id else None
    test_names = set(args.test_name) if args.test_name else None
    path = collect_for_catalog(
        catalog_path=catalog_path,
        output_path=output_path,
        work_root=work_root,
        log_path=log_path,
        target_ids=target_ids,
        test_names=test_names,
        test_limit=args.test_limit,
        timeout=args.timeout,
        reuse_checkout=args.reuse_checkout,
    )
    log(f"Wrote target-test coverage mapping: {path}", log_path=log_path)
    log(f"Coverage log saved to: {log_path}", log_path=log_path)


if __name__ == "__main__":
    main()
