## Remaining Scripts

- `test_defects4j_smoke.py`: manual smoke test for Defects4J execution behavior.

## Notes

- Operational modules now live under `harness/`:
  - `harness.reporting.kill_matrix`
  - `harness.targets.test_coverage_templates`
  - `harness.targets.test_coverage_collection`
  - `harness.executions.manual_mutants`
- The main entrypoints for normal operation are `run_llm.py` and `run_batch.py` at repo root.
- `scripts/` is reserved for ad hoc helpers and manual smoke checks.
- Automated regression coverage for the kill-matrix pipeline lives in `tests/test_kill_matrix_pipeline.py`.
