from __future__ import annotations

import shutil
from pathlib import Path


def cleanup_paths(paths: list[str | Path], print_to_stdout: bool = True) -> list[str]:
    removed = []

    for path in paths:
        p = Path(path)
        if not p.exists():
            continue

        if p.is_dir():
            shutil.rmtree(p)
            removed.append(str(p))
            if print_to_stdout:
                print(f"Removed temp directory: {p}")
        else:
            p.unlink()
            removed.append(str(p))
            if print_to_stdout:
                print(f"Removed temp file: {p}")

    return removed