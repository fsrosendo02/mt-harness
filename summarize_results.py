#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from harness.reporting.summary import summarize_results_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize mutation-testing harness results.csv")
    parser.add_argument("csv_path", type=Path, help="Path to results.csv")
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Keep repeated rows instead of deduplicating by dataset/subject/function/mutant_id",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional output path for JSON summary (default: same folder as results.csv, file summary.json)",
    )
    args = parser.parse_args()

    summarize_results_csv(
        csv_path=args.csv_path,
        keep_duplicates=args.keep_duplicates,
        json_out=args.json_out,
        print_to_stdout=True,
    )


if __name__ == "__main__":
    main()