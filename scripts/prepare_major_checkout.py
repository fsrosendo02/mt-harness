#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


COMPILE_TARGET_MARKER = '<target name="compile"'
COMPILE_TESTS_TARGET_MARKER = '<target name="compile.tests"'
TEST_CLASSPATH_MARKER = '<path id="test.classpath">'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Patch a Defects4J Ant checkout so Major is wired into compile and test classpaths."
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
        help="Write build.xml.bak before patching.",
    )
    return parser.parse_args()


def leading_whitespace(text: str) -> str:
    return text[: len(text) - len(text.lstrip(" \t"))]


def ensure_child_line(block_lines: list[str], anchor: str, line_to_add: str) -> list[str]:
    if any(existing.strip() == line_to_add.strip() for existing in block_lines):
        return block_lines

    for idx, line in enumerate(block_lines):
        if anchor in line:
            indent = leading_whitespace(line)
            block_lines.insert(idx + 1, f"{indent}{line_to_add}")
            return block_lines

    raise ValueError(f"Could not find anchor '{anchor}' inside expected XML block")


def patch_target_block(
    xml_text: str,
    target_marker: str,
    inject_lines: list[tuple[str, str]],
) -> str:
    start = xml_text.find(target_marker)
    if start == -1:
        raise ValueError(f"Could not find target marker '{target_marker}'")

    javac_start = xml_text.find("<javac ", start)
    if javac_start == -1:
        raise ValueError(f"Could not find <javac> block after '{target_marker}'")

    javac_end = xml_text.find("</javac>", javac_start)
    if javac_end == -1:
        raise ValueError(f"Could not find </javac> for target '{target_marker}'")

    javac_end += len("</javac>")
    block = xml_text[javac_start:javac_end]
    lines = block.splitlines()

    for anchor, line_to_add in inject_lines:
        lines = ensure_child_line(lines, anchor, line_to_add)

    patched_block = "\n".join(lines)
    return xml_text[:javac_start] + patched_block + xml_text[javac_end:]


def patch_test_classpath(xml_text: str, major_rt_jar: str) -> str:
    marker = TEST_CLASSPATH_MARKER
    start = xml_text.find(marker)
    if start == -1:
        raise ValueError(f"Could not find marker '{marker}'")

    end = xml_text.find("</path>", start)
    if end == -1:
        raise ValueError("Could not find closing </path> for test.classpath")

    end += len("</path>")
    block = xml_text[start:end]
    lines = block.splitlines()
    pathelement = f'<pathelement location="{major_rt_jar}"/>'

    if any(existing.strip() == pathelement for existing in lines):
        return xml_text

    closing_idx = None
    for idx, line in enumerate(lines):
        if "</path>" in line:
            closing_idx = idx
            break

    if closing_idx is None:
        raise ValueError("Malformed test.classpath block: missing </path>")

    indent = leading_whitespace(lines[closing_idx]) + "    "
    lines.insert(closing_idx, f"{indent}{pathelement}")
    patched_block = "\n".join(lines)
    return xml_text[:start] + patched_block + xml_text[end:]


def patch_checkout(
    *,
    checkout: Path,
    mml_bin: Path,
    major_home: Path,
    mutants_log: Path | None = None,
    write_backup: bool = False,
) -> dict[str, str]:
    checkout = checkout.resolve()
    build_xml = checkout / "build.xml"
    if not build_xml.exists():
        raise FileNotFoundError(f"build.xml not found at {build_xml}")

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

    compilerarg_value = (
        f'-Xplugin:MajorPlugin mml:{mml_bin} mutants.log:{mutants_log}'
    )

    xml_text = build_xml.read_text(encoding="utf-8")
    original_text = xml_text

    xml_text = patch_target_block(
        xml_text,
        COMPILE_TARGET_MARKER,
        [
            (
                '<classpath refid="compile.classpath"/>',
                f'<classpath location="{major_jar}"/>',
            ),
            (
                '<classpath refid="compile.classpath"/>',
                f'<compilerarg value="{compilerarg_value}"/>',
            ),
        ],
    )
    xml_text = patch_target_block(
        xml_text,
        COMPILE_TESTS_TARGET_MARKER,
        [
            (
                '<classpath refid="test.classpath"/>',
                f'<classpath location="{major_rt_jar}"/>',
            ),
        ],
    )
    xml_text = patch_test_classpath(xml_text, str(major_rt_jar))

    if write_backup:
        backup_path = build_xml.with_suffix(build_xml.suffix + ".bak")
        backup_path.write_text(original_text, encoding="utf-8")

    build_xml.write_text(xml_text, encoding="utf-8")

    return {
        "build_xml": str(build_xml),
        "major_jar": str(major_jar),
        "major_rt_jar": str(major_rt_jar),
        "mml_bin": str(mml_bin),
        "mutants_log": str(mutants_log),
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

    print(f"Patched {result['build_xml']}")
    print(f"  compile uses: {result['major_jar']}")
    print(f"  compile.tests/test classpath includes: {result['major_rt_jar']}")
    print(f"  compilerarg uses MML: {result['mml_bin']}")
    print(f"  mutants.log path: {result['mutants_log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
