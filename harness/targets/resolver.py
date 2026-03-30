import os

from harness.models import Target
from harness.targets.catalog import get_target_by_id
from harness.targets.java_method_extractor import extract_method, extract_method_by_lines


def resolve_target(config, checkout_dir):
    """
    Returns:
        (resolved_target, method_code)
    """

    if "target_id" in config:
        entry = get_target_by_id(config["catalog_file"], config["target_id"])
    else:
        entry = config

    file_path = entry["file"]
    function = entry["function"]
    language = entry.get("language", config.get("language", "java"))

    abs_file = os.path.join(checkout_dir, file_path)
    if not os.path.exists(abs_file):
        raise FileNotFoundError(f"File not found: {abs_file}")

    with open(abs_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start_line = entry.get("start_line")
    end_line = entry.get("end_line")

    if (start_line is None) ^ (end_line is None):
        raise ValueError(
            "Catalog entry must define both start_line and end_line, or neither"
        )

    if start_line is not None and end_line is not None:
        method_code = extract_method_by_lines(lines, start_line, end_line)
    else:
        if language != "java":
            raise NotImplementedError(
                "Automatic method extraction currently supports only Java. "
                "For other languages, store start_line/end_line in the catalog."
            )

        start_line, end_line, method_code = extract_method(
            lines,
            function,
            abs_file,
        )

    target = Target(
        file_path=file_path,
        function_name=function,
        start_line=start_line,
        end_line=end_line,
        language=language,
        target_id=entry.get("target_id"),
    )

    return target, method_code
