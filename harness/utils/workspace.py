import shutil
from pathlib import Path


def recreate_dir(path: str) -> None:
    path_obj = Path(path).resolve()

    if path_obj.exists():
        shutil.rmtree(path_obj)

    path_obj.mkdir(parents=True, exist_ok=True)
