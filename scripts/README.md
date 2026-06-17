## Remaining Scripts

- `test_defects4j_smoke.py`: manual smoke test for Defects4J execution behavior.
- `prepare_major_checkout.py`: patches a Defects4J `build.xml` checkout so Major is injected into
  `compile`, `compile.tests`, and the test classpath.
- `run_major_subject.py`: checks out a Defects4J subject, patches `build.xml` for Major, and runs
  `ant clean compile` to generate mutants.
- `normalize_major_mml.py`: rewrites a source `.mml` into a local-Major-compatible version by
  dropping unsupported operators or known-bad scopes.
- `run_major_catalog.py`: runs the Major compile-generation flow directly from a catalog at
  target granularity, storing normalized MML, per-target `mmlc` output, per-target logs,
  `mutants.log`, `results.csv`, and summaries under `harness/executions/java/major/`.

Example:

```bash
python3 scripts/normalize_major_mml.py \
  --input scripts/major_dsl/defects4j_final_catalog_major_signature_scoped_bcr.mml \
  --output /tmp/major/defects4j_final_catalog_major_compatible.mml
```

```bash
python3 scripts/prepare_major_checkout.py \
  --checkout /tmp/major/Lang_1 \
  --mml-bin /home/francisco/mt-harness/scripts/major_dsl/defects4j_final_catalog_major_signature_scoped_no_bcr_no_disable_no_gson_dollar.mml.bin \
  --major-home /home/francisco/mt-harness/harness/llm/providers/major \
  --write-backup
```

```bash
python3 scripts/run_major_subject.py \
  --subject Lang_1 \
  --mml-bin /home/francisco/mt-harness/scripts/major_dsl/defects4j_final_catalog_major_signature_scoped_no_bcr_no_disable_no_gson_dollar.mml.bin \
  --major-home /home/francisco/mt-harness/harness/llm/providers/major \
  --summary-json /tmp/major/lang1_summary.json
```

```bash
python3 scripts/run_major_catalog.py \
  --catalog harness/datasets/catalogs/defects4j_final_catalog.json \
  --source-mml scripts/major_dsl/defects4j_final_catalog_major_signature_scoped_bcr.mml \
  --major-home /home/francisco/mt-harness/harness/llm/providers/major \
  --target-id lang_1f_getFittedText__line113_206
```

## Notes

- Operational modules now live under `harness/`:
  - `harness.reporting.kill_matrix`
  - `harness.targets.test_coverage_templates`
  - `harness.targets.test_coverage_collection`
  - `harness.executions.manual_mutants`
- The main entrypoints for normal operation are `run_llm.py` and `run_batch.py` at repo root.
- `scripts/` is reserved for ad hoc helpers and manual smoke checks.
- Automated regression coverage for the kill-matrix pipeline lives in `tests/test_kill_matrix_pipeline.py`.
