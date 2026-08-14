## Remaining Scripts

### C fault coupling (LLM + Mull)

`manybugs/compute_fault_coupling.py` computes fault coupling for any ManyBugs
catalog with a catalog-specific `target_tests.csv`. Oracle tests are the rows
whose `match_mode` is `oracle`; all mapped tests are used to validate that each
mutant has a complete test vector.

```bash
python3 scripts/manybugs/compute_fault_coupling.py \
  --catalog harness/datasets/catalogs/manybugs_gzip_pilot.json \
  --llm-root harness/executions/c/llm \
  --mull-root harness/executions/c/mull/execution/mull_gzip_pilot_full \
  --output-dir harness/reports/c/fault_coupling/gzip_pilot
```

The roots are scanned recursively and both options are repeatable. To expand
Mull, run a catalog campaign in a new directory and pass that directory through
another `--mull-root`; the analysis code itself does not contain gzip target
names. Every catalog target must have at least one oracle mapping. Empty runs
remain present in campaign/target outputs with zero executable mutants, while
incomplete mutant test vectors are excluded and listed in `audit.json`.

Outputs include mutant-, target-, bug-, campaign-, and condition-level CSVs.
Mutant identities include tool, model, requested count, campaign, run, target,
and mutant ID, so repeated `m01` identifiers and 8/16/32 conditions do not
collide. Condition summaries average complete campaign metrics rather than
pooling repetitions.

Before running Mull for a new ManyBugs catalog, use the static preflight:

```bash
python3 scripts/manybugs/mull/run_mull_catalog.py \
  --catalog harness/datasets/catalogs/manybugs_all_pilots.json \
  --preflight-only \
  --plan-output /tmp/manybugs_all_pilots_mull_preflight.json
```

It performs no Docker, build, or test work. A non-zero exit means at least one
target is blocked; the JSON identifies missing oracle mappings, toolchain
Dockerfiles, binary profiles, or project-specific execution adapters. The
execution driver also refuses unsupported projects before checkout, preventing
the gzip-specific wrapper from being applied to another test protocol.

Validated C/Mull execution adapters currently cover gzip and libtiff. The
libtiff adapter resolves library targets to `libtiff/.libs/libtiff.so` and tool
targets to the corresponding ELF under `tools/.libs/`; its four artifact
classes were smoke-tested separately before catalog-wide execution was enabled.

New Mull runs apply every ManyBugs `diffs/**/*-diff` patch before compilation
and record `source_revision: fixed` plus the applied-patch count in
`run_config.json`. Fault-coupling analysis excludes older Mull runs without
this provenance. This is intentional: gzip/lighttpd images contain buggy source
and store the fixed revision as separate patches, so unverified historical runs
cannot be compared safely with LLM mutants executed against the fixed program.

The lighttpd execution profile is implemented but explicitly blocked with
`runtime_baseline_incompatible`. Its rebuilt Clang/bionic server fails oracle baselines such as
`mod-cgi.t` and `core-condition.t`; the previously known equivalent `p1` is a
broad test and is therefore insufficient for fault coupling.

GMP and Python also have structural profiles but remain marked
`unvalidated_execution_adapter` until final execution. GMP inspects
`.libs/libgmp.so`, configures with `--disable-assembly` so the catalog C target
owns the LLVM IR, and runs the scenario test protocol. Python inspects the
instrumented `python` executable and uses the same baseline-gated scenario
protocol. Their Dockerfiles are candidates, not claims of runtime equivalence.

Generate the final staged operation plan without executing anything:

```bash
python3 scripts/manybugs/mull/prepare_execution_plan.py \
  --json-output /tmp/mull_execution_plan.json \
  --shell-output /tmp/mull_execution_plan.sh
```

The shell output contains executable commands only for validated projects.
Commands for required smoke tests are comments and blocked projects receive no
command. Campaign commands use `--resume`: a target is skipped only when all
normalized artifacts and `run_config.json` exist and provenance says
`source_revision=fixed`. Each invocation writes `campaign_manifest.json`, so a
machine interruption leaves an auditable per-target state.

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
