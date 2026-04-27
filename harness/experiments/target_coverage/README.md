# Target Coverage Analysis

This directory holds artifacts for target-aware kill matrices.

## Files

- `target_tests.csv`: input mapping from each target to the tests that cover it.
- `catalogs/<catalog_name>/target_tests.csv`: per-catalog target-test mappings.
- `kill_matrices/`: generated kill matrix outputs.

## `target_tests.csv` schema

```csv
catalog,dataset,subject_id,version,project,target_id,file_path,start_line,end_line,test_name,coverage_source
```

Each row means `test_name` covers the target identified by `target_id`.

Until a coverage backend is added, `scripts/build_kill_matrix.py` creates an
empty `target_tests.csv` template and falls back to the tests observed as
failing in mutant logs.

Create one target-test template per catalog with:

```bash
python3 -m scripts.build_target_test_catalog
```

Existing per-catalog files are preserved by default. Use `--force` only when
you intentionally want to regenerate templates.

Combine populated per-catalog mappings into the global input with:

```bash
python3 -m scripts.build_target_test_catalog --combine
```

Populate one catalog with Defects4J per-test coverage:

```bash
python3 -m scripts.collect_target_test_coverage \
  harness/targets/defects4j_multi_project_pilot.json \
  --reuse-checkout
```

For a small smoke test:

```bash
python3 -m scripts.collect_target_test_coverage \
  harness/targets/defects4j_multi_project_pilot.json \
  --target-id lang_1f_arrayMemberEquals__line287 \
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

Once `target_tests.csv` is populated, the kill matrix uses those target-covering
tests as the test universe for each mutant.

## Build

```bash
python3 scripts/build_kill_matrix.py --group-by project
```
