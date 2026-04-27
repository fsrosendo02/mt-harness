from __future__ import annotations


def format_target_id(
    subject: str,
    version: str,
    function: str,
    start_line: int,
    end_line: int,
) -> str:
    subject_prefix = str(subject).strip().lower()
    version_suffix = str(version).strip()
    function_name = str(function).strip()
    return (
        f"{subject_prefix}{version_suffix}_{function_name}"
        f"__line{int(start_line)}_{int(end_line)}"
    )
