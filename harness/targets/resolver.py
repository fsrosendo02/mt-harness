import os

from harness.models import Target
from harness.targets.catalog import get_target_by_id
from harness.targets.java_method_extractor import extract_method


def resolve_target(config, checkout_dir):
    """
    Returns:
        (resolved_target, method_code)
    """

    if "target_id" in config:
        entry = get_target_by_id(config["catalog_file"], config["target_id"])
    else:
        entry = config

    dataset = entry["dataset"]
    if dataset != "defects4j":
        raise NotImplementedError(f"Dataset '{dataset}' not supported yet")

    file_path = entry["file"]
    function = entry["function"]
    language = entry.get("language", config.get("language", "java"))

    abs_file = os.path.join(checkout_dir, file_path)
    if not os.path.exists(abs_file):
        raise FileNotFoundError(f"File not found: {abs_file}")

    with open(abs_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if "start_line" in entry and "end_line" in entry:
        start_line = entry["start_line"]
        end_line = entry["end_line"]
        method_code = "".join(lines[start_line - 1:end_line])
    else:
        if language != "java":
            raise NotImplementedError(
                "Automatic method extraction currently supports only Java"
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