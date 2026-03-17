import shutil
from pathlib import Path


def clean_tmp():
    tmp_path = Path("tmp")

    if not tmp_path.exists():
        print("No tmp directory found.")
        return

    print(f"Cleaning tmp directory: {tmp_path}")

    for item in tmp_path.iterdir():
        if item.is_dir():
            print(f"Removing {item}")
            shutil.rmtree(item)


def clean_empty_runs():
    runs_path = Path("harness/runs")

    if not runs_path.exists():
        return

    print(f"Checking for empty/broken runs in {runs_path}")

    for run in runs_path.iterdir():
        if not run.is_dir():
            continue

        results = run / "results.csv"

        if not results.exists():
            print(f"Removing incomplete run: {run}")
            shutil.rmtree(run)


def main():
    print("=== CLEAN WORKSPACE ===\n")

    clean_tmp()
    print()
    clean_empty_runs()

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
