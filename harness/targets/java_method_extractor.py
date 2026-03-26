import subprocess


def get_method_start_ctags(file_path, function_name):
    cmd = ["ctags", "-x", "--language-force=Java", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ctags failed: {result.stderr}")

    matches = []

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue

        name = parts[0]

        try:
            line_no = int(parts[2])
        except ValueError:
            continue

        if name == function_name:
            matches.append(line_no)

    if not matches:
        raise ValueError(f"Method '{function_name}' not found via ctags")

    if len(matches) > 1:
        raise ValueError(f"Ambiguous method '{function_name}' (overloads detected)")

    return matches[0]


def find_method_end(source_lines, start_idx):
    brace_count = 0
    started = False

    for i in range(start_idx, len(source_lines)):
        line = source_lines[i]

        if "{" in line:
            brace_count += line.count("{")
            started = True

        if "}" in line:
            brace_count -= line.count("}")

        if started and brace_count == 0:
            return i

    raise ValueError("Could not determine method end")


def extract_method(source_lines, function_name, file_path):
    start_line = get_method_start_ctags(file_path, function_name)
    start_idx = start_line - 1

    while start_idx > 0 and source_lines[start_idx - 1].strip().startswith("@"):
        start_idx -= 1

    end_idx = find_method_end(source_lines, start_idx)
    method_code = "".join(source_lines[start_idx:end_idx + 1])

    return start_idx + 1, end_idx + 1, method_code