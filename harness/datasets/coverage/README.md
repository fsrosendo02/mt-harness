# Target Coverage Analysis

This directory holds artifacts for target-aware kill matrices.

## Files

- `catalogs/<catalog_name>/target_tests.csv`: source-of-truth target-test mapping for one catalog.
- `global_target_tests.csv`: optional combined index across all populated catalogs.
- `kill_matrices/`: generated kill matrix outputs.

## Coverage CSV schema

```csv
catalog,dataset,subject_id,version,project,target_id,file_path,start_line,end_line,test_name,coverage_source
```

Each row means `test_name` covers the target identified by `target_id`.

`execution/test_results.csv` is now the source of truth for the kill matrix.
Each row records one `mutant × test` observation with explicit `eligible`,
`executed`, `outcome`, `failure_type`, `worker_id`, and ordering metadata.

`execution/results.csv` remains the per-mutant summary layer, and
`experiment_index.csv` remains the run-level aggregate layer.

Until a coverage backend is added, `harness.reporting.kill_matrix` still creates
an empty `global_target_tests.csv` template and keeps a legacy fallback for older runs
that only have `results.csv` plus logs.

Create one target-test template per catalog with:

```bash
python3 -m harness.targets.test_coverage_templates
```

Existing per-catalog files are preserved by default. Use `--force` only when
you intentionally want to regenerate templates.

Combine populated per-catalog mappings into the optional global index with:

```bash
python3 -m harness.targets.test_coverage_templates --combine
```

Populate one catalog with Defects4J per-test coverage:

```bash
python3 -m harness.targets.test_coverage_collection \
  harness/datasets/catalogs/defects4j_pilot_catalog.json \
  --reuse-checkout
```

For a small smoke test:

```bash
python3 -m harness.targets.test_coverage_collection \
  harness/datasets/catalogs/defects4j_pilot_catalog.json \
  --target-id lang_1f_arrayMemberEquals__line287_316 \
  --test-name org.apache.commons.lang3.AnnotationUtilsTest::testEquivalence \
  --output /tmp/target_tests_smoke.csv \
  --reuse-checkout
```

The coverage backend checks out each subject, exports `tests.all`, runs:

```bash
defects4j coverage -t <test> -i <instrument_classes>
```

and records a row when `coverage.xml` shows at least one covered line inside
the target range.

Coverage is run once per instrumented class and test, then reused for all
targets in that class.

Once a per-catalog `target_tests.csv` is populated, the execution pipeline persists one row in
`execution/test_results.csv` for every eligible target-covering test of every
mutant, and the kill matrix builder reads that table directly.

## Build

```bash
python3 -m harness.reporting.kill_matrix --group-by project
```
