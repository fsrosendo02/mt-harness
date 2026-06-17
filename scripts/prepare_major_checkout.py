#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


COMPILE_TARGET_NAMES = ("compile",)
COMPILE_TESTS_TARGET_NAMES = ("compile.tests", "compile-tests")
PATH_IDS_TO_PATCH = ("test.classpath", "build.test.classpath")
LEGACY_SOURCE_TARGETS = {
    'source="1.4"': 'source="1.6"',
    'target="1.4"': 'target="1.6"',
    'source="1.5"': 'source="1.6"',
    'target="1.5"': 'target="1.6"',
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Patch a Defects4J Ant checkout so Major is wired into the compile javac block."
        )
    )
    parser.add_argument(
        "--checkout",
        required=True,
        help="Path to the Defects4J checkout directory containing build.xml.",
    )
    parser.add_argument(
        "--mml-bin",
        required=True,
        help="Absolute path to the compiled Major .mml.bin file.",
    )
    parser.add_argument(
        "--major-home",
        default=str(Path("harness/llm/providers/major").resolve()),
        help="Absolute path to the local Major installation directory.",
    )
    parser.add_argument(
        "--mutants-log",
        help=(
            "Optional absolute path for mutants.log. "
            "Defaults to <checkout>/mutants.log."
        ),
    )
    parser.add_argument(
        "--write-backup",
        action="store_true",
        help="Write .bak files before patching.",
    )
    return parser.parse_args()


def leading_whitespace(text: str) -> str:
    return text[: len(text) - len(text.lstrip(" \t"))]


def find_target_bounds(xml_text: str, target_names: tuple[str, ...]) -> tuple[int, int, str] | None:
    for target_name in target_names:
        marker = f'<target name="{target_name}"'
        start = xml_text.find(marker)
        if start == -1:
            continue
        end = xml_text.find("</target>", start)
        if end == -1:
            continue
        end += len("</target>")
        return start, end, target_name
    return None


def target_depends(xml_text: str, target_name: str) -> list[str]:
    bounds = find_target_bounds(xml_text, (target_name,))
    if bounds is None:
        return []
    start, end, _ = bounds
    block = xml_text[start:end]
    match = re.search(r'depends="([^"]+)"', block)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def inject_before_closing_tag(block: str, line_to_add: str, closing_tag: str) -> str:
    lines = block.splitlines()
    if any(existing.strip() == line_to_add.strip() for existing in lines):
        return block

    closing_idx = None
    for idx, line in enumerate(lines):
        if closing_tag in line:
            closing_idx = idx
            break
    if closing_idx is None:
        raise ValueError(f"Closing tag '{closing_tag}' not found inside block")

    indent = leading_whitespace(lines[closing_idx]) + "  "
    lines.insert(closing_idx, f"{indent}{line_to_add}")
    return "\n".join(lines)


def patch_first_javac_in_target(
    xml_text: str,
    *,
    target_names: tuple[str, ...],
    lines_to_add: list[str],
) -> tuple[str, bool]:
    queue = list(target_names)
    seen: set[str] = set()

    while queue:
        target_name = queue.pop(0)
        if target_name in seen:
            continue
        seen.add(target_name)

        bounds = find_target_bounds(xml_text, (target_name,))
        if bounds is None:
            continue

        target_start, target_end, _ = bounds
        target_block = xml_text[target_start:target_end]
        javac_start_rel = target_block.find("<javac ")
        if javac_start_rel == -1:
            queue.extend(target_depends(xml_text, target_name))
            continue

        javac_end_rel = target_block.find("</javac>", javac_start_rel)
        if javac_end_rel == -1:
            raise ValueError("Could not find closing </javac> inside target block")
        javac_end_rel += len("</javac>")

        javac_block = target_block[javac_start_rel:javac_end_rel]
        for line_to_add in lines_to_add:
            javac_block = inject_before_closing_tag(javac_block, line_to_add, "</javac>")

        patched_target_block = (
            target_block[:javac_start_rel] + javac_block + target_block[javac_end_rel:]
        )
        patched_xml = xml_text[:target_start] + patched_target_block + xml_text[target_end:]
        return patched_xml, True

    return xml_text, False


def patch_path_block(xml_text: str, *, path_id: str, pathelement: str) -> tuple[str, bool]:
    marker = f'<path id="{path_id}">'
    start = xml_text.find(marker)
    if start == -1:
        return xml_text, False

    end = xml_text.find("</path>", start)
    if end == -1:
        raise ValueError(f"Could not find closing </path> for path id '{path_id}'")
    end += len("</path>")
    block = xml_text[start:end]
    patched_block = inject_before_closing_tag(block, pathelement, "</path>")
    return xml_text[:start] + patched_block + xml_text[end:], True


def imported_build_files(build_xml: Path) -> list[Path]:
    text = build_xml.read_text(encoding="utf-8", errors="replace")
    imports = re.findall(r'<import file="([^"]+)"', text)
    return [(build_xml.parent / rel_path).resolve() for rel_path in imports]


def candidate_build_files(checkout: Path) -> list[Path]:
    candidates = [
        checkout / "build.xml",
        checkout / "ant" / "build.xml",
        checkout / "build-ant.xml",
    ]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    for pattern in ("**/build.xml", "**/build-*.xml"):
        for candidate in sorted(checkout.glob(pattern)):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            ordered.append(resolved)
    return ordered


def choose_patch_file(checkout: Path) -> tuple[Path, Path]:
    for build_xml in candidate_build_files(checkout):
        if not build_xml.exists():
            continue
        text = build_xml.read_text(encoding="utf-8", errors="replace")
        patched_xml, compile_patched = patch_first_javac_in_target(
            text,
            target_names=COMPILE_TARGET_NAMES,
            lines_to_add=[],
        )
        if compile_patched:
            return build_xml, build_xml

        for imported in imported_build_files(build_xml):
            if not imported.exists():
                continue
            imported_text = imported.read_text(encoding="utf-8", errors="replace")
            patched_import, compile_patched = patch_first_javac_in_target(
                imported_text,
                target_names=COMPILE_TARGET_NAMES,
                lines_to_add=[],
            )
            if compile_patched:
                return build_xml, imported

    for build_xml in candidate_build_files(checkout):
        if build_xml.exists():
            return build_xml, build_xml

    raise FileNotFoundError(
        f"Could not find a supported Ant build file under {checkout}"
    )


def choose_compile_build_file(checkout: Path) -> Path:
    for build_xml in candidate_build_files(checkout):
        if not build_xml.exists():
            continue
        text = build_xml.read_text(encoding="utf-8", errors="replace")
        if find_target_bounds(text, COMPILE_TARGET_NAMES) is not None:
            return build_xml.resolve()
    raise FileNotFoundError(f"Could not find a supported Ant build file under {checkout}")


def patch_checkout(
    *,
    checkout: Path,
    mml_bin: Path,
    major_home: Path,
    mutants_log: Path | None = None,
    write_backup: bool = False,
) -> dict[str, str]:
    checkout = checkout.resolve()
    mml_bin = mml_bin.resolve()
    if not mml_bin.exists():
        raise FileNotFoundError(f"Compiled MML file not found at {mml_bin}")

    major_home = major_home.resolve()
    major_jar = major_home / "lib" / "major.jar"
    major_rt_jar = major_home / "lib" / "major-rt.jar"
    if not major_jar.exists():
        raise FileNotFoundError(f"major.jar not found at {major_jar}")
    if not major_rt_jar.exists():
        raise FileNotFoundError(f"major-rt.jar not found at {major_rt_jar}")

    mutants_log = mutants_log.resolve() if mutants_log else (checkout / "mutants.log").resolve()
    compilerarg_value = f'-Xplugin:MajorPlugin mml:{mml_bin} mutants.log:{mutants_log}'

    build_xml, patch_file = choose_patch_file(checkout)
    compile_build_file = choose_compile_build_file(checkout)
    xml_text = patch_file.read_text(encoding="utf-8")
    original_text = xml_text
    legacy_replacements: dict[str, int] = {}

    xml_text, compile_patched = patch_first_javac_in_target(
        xml_text,
        target_names=COMPILE_TARGET_NAMES,
        lines_to_add=[
            f'<classpath location="{major_jar}"/>',
            f'<compilerarg value="{compilerarg_value}"/>',
        ],
    )
    if not compile_patched:
        raise ValueError(
            f"Could not find a <javac> block inside compile target in {patch_file}"
        )

    xml_text, _ = patch_first_javac_in_target(
        xml_text,
        target_names=COMPILE_TESTS_TARGET_NAMES,
        lines_to_add=[f'<classpath location="{major_rt_jar}"/>'],
    )

    path_patched = False
    for path_id in PATH_IDS_TO_PATCH:
        xml_text, patched = patch_path_block(
            xml_text,
            path_id=path_id,
            pathelement=f'<pathelement location="{major_rt_jar}"/>',
        )
        path_patched = path_patched or patched

    for old, new in LEGACY_SOURCE_TARGETS.items():
        count = xml_text.count(old)
        if count:
            xml_text = xml_text.replace(old, new)
            legacy_replacements[old] = count

    if write_backup:
        backup_path = patch_file.with_suffix(patch_file.suffix + ".bak")
        backup_path.write_text(original_text, encoding="utf-8")

    patch_file.write_text(xml_text, encoding="utf-8")

    return {
        "build_xml": str(build_xml),
        "compile_build_file": str(compile_build_file),
        "patch_file": str(patch_file),
        "major_jar": str(major_jar),
        "major_rt_jar": str(major_rt_jar),
        "mml_bin": str(mml_bin),
        "mutants_log": str(mutants_log),
        "path_block_patched": str(path_patched),
        "legacy_replacements": str(legacy_replacements),
    }


def main() -> int:
    args = parse_args()
    result = patch_checkout(
        checkout=Path(args.checkout),
        mml_bin=Path(args.mml_bin),
        major_home=Path(args.major_home),
        mutants_log=Path(args.mutants_log) if args.mutants_log else None,
        write_backup=args.write_backup,
    )

    print(f"Patched checkout root {result['build_xml']}")
    print(f"  patched file: {result['patch_file']}")
    print(f"  compile uses: {result['major_jar']}")
    print(f"  compile.tests/test classpath includes: {result['major_rt_jar']}")
    print(f"  compilerarg uses MML: {result['mml_bin']}")
    print(f"  mutants.log path: {result['mutants_log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
