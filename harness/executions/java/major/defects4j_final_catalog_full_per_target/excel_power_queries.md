# Excel Power Query Guide

This folder contains ready-to-paste Power Query scripts for analyzing the `major` run.

## Recommended workbook structure

Create these queries in Excel:

1. `BaseFolder` as a text parameter
2. `ResultsCsv`
3. `TargetsCsv`
4. `RunSummary`
5. `TargetMetrics`
6. `SubjectMetrics`

Set `BaseFolder` to:

`/home/francisco/mt-harness/harness/executions/java/major/defects4j_final_catalog_full_per_target`

## How to load in Excel

1. Open Excel
2. Go to `Data` -> `Get Data` -> `Launch Power Query Editor`
3. Create a `Blank Query`
4. Open `Advanced Editor`
5. Paste each script from the `power_queries` folder
6. Name each query using the filename without `.pq`
7. Load `ResultsCsv`, `TargetsCsv`, `RunSummary`, `TargetMetrics`, and `SubjectMetrics` as tables

## Suggested analysis views

Use `TargetMetrics` for:

- targets with most mutants
- targets with zero mutants
- compile failures by target
- elapsed time per target
- mutants per executable target

Use `SubjectMetrics` for:

- mutants by project or subject
- compile failures by subject
- target coverage by subject
- average mutants per target

Use `ResultsCsv` for:

- operator distribution
- build status distribution
- executable distribution
- mutant density by source line

## Suggested pivot tables

1. Mutants by subject
   - Rows: `subject_id`
   - Values: `mutant_count`

2. Mutants by operator
   - Rows: `operator`
   - Values: `mutant_count`

3. Compile failures by subject
   - Rows: `subject`
   - Values: `compile_failed_targets`

4. Generated mutants per target
   - Rows: `target_id`
   - Values: `generated_mutants`
   - Filter: top 20

5. Zero-mutant coverage by subject
   - Rows: `subject`
   - Values: `zero_mutant_targets`, `target_count`

## Useful calculated columns in Excel

If you want a few extra formulas in the sheet:

- `PctNonZeroTargets = nonzero_mutant_targets / target_count`
- `PctCompileFailed = compile_failed_targets / target_count`
- `AvgElapsedMin = avg_elapsed_sec / 60`
- `PctRowsExecutable = executable_true_rows / mutant_count`

## Important interpretation note

In this run, `results.csv` contains mutant rows and `targets.csv` contains one row per target. For target-level analysis, prefer `TargetMetrics` or `TargetsCsv`. For mutant-level analysis, prefer `ResultsCsv`.
