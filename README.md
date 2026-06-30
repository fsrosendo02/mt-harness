# MT Harness

A harness for generating, running, and analysing mutants against code targets, currently focused on **Defects4J**.

The repository supports three main pipeline modes:

- `full` — generate mutants with an LLM and execute them
- `generate_only` — generate mutants without executing
- `execute_only` — execute mutants produced in a previous run

## Overview

The normal workflow is:

1. Create or choose a target catalog
2. Ensure the `target → covering tests` mapping exists
3. Run a single target with `run_llm.py` or a batch with `run_batch.py`
4. Inspect results under `harness/executions/` and `harness/reports/`

## Repository Layout

```text
.
├── configs/                      # example configuration files
├── harness/
│   ├── adapters/                 # benchmark integrations (e.g. Defects4J)
│   ├── datasets/
│   │   ├── catalogs/             # target catalogs (JSON)
│   │   └── coverage/             # target_tests.csv mappings
│   ├── executions/
│   │   ├── runs/                 # individual run artifacts
│   │   └── batches/              # batch manifests
│   ├── llm/                      # parsing, prompts, and providers
│   ├── reporting/                # indexes, summaries, and kill matrices
│   ├── reports/                  # aggregated harness artifacts
│   └── targets/                  # target discovery, validation, and coverage
├── prompts/                      # generation prompts
├── tests/                        # automated tests
├── run_llm.py                    # entry point for a single run
├── run_batch.py                  # entry point for batches
└── summarize_results.py          # quick summary of a results.csv
```

## Prerequisites

Before using the harness the environment must have:

- `python3`
- `openjdk-11`
- `defects4j`
- `git`, `svn`, `ctags`
- the LLM provider you intend to use

Supported providers:

| Provider | Mechanism |
|---|---|
| `ollama` | `ollama run <model>` command |
| `ollama_api` | Python `ollama` library |
| `gpt4o` | external `gpt_run` binary |
| `gemini` | external `gemini_run` binary |

The `Dockerfile` documents a base environment with Defects4J, Java, Ollama, and all system dependencies.

## Environment Setup

Minimal manual verification:

```bash
python3 --version
java -version
defects4j pids
ctags --version
```

If using Ollama:

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

If using `gpt4o` or `gemini`, confirm the wrappers exist on `PATH`:

```bash
which gpt_run
which gemini_run
```

## Configuration

Configuration files in `configs/` control every execution. The key fields are:

| Field | Description |
|---|---|
| `dataset` | Target dataset; the main flow assumes `defects4j` |
| `catalog_file` | JSON catalog of targets |
| `target_id` | Specific target within the catalog |
| `subject` | Benchmark subject, e.g. `Lang_1` |
| `version` | Subject version, normally `f` |
| `file` / `function` | Manual target override (when not using `target_id`) |
| `model` | Model name |
| `provider` / `model_provider` | Provider to use |
| `prompt_file` | Generation prompt |
| `num_mutants` | Number of mutants requested from the model |
| `timeout` | Generation timeout per call |
| `pipeline_mode` | `full`, `generate_only`, or `execute_only` |
| `run_mode` | `fresh`, `overwrite`, or `resume` |
| `mutant_workers` | Parallelism for mutant execution |
| `missing_target_tests_policy` | `fail` or `report_and_skip` |
| `cleanup_tmp` | Clean up temporary directories after the run |
| `validate_after_run` | Validate artifacts after execution |
| `rebuild_index` | Rebuild the global index after the run |

Included examples:

- `configs/sample_config_full_pipeline.json` — generate and execute mutants
- `configs/sample_config_generation.json` — generate without executing
- `configs/sample_config_execution.json` — re-execute an existing batch
- `configs/debugging/test_gpt.json` — simple manual run with the `gpt4o` provider
- `configs/debugging/test_gemini.json` — simple manual run with the `gemini` provider

## Running the Harness

### Single run

```bash
python3 run_llm.py configs/sample_config_full_pipeline.json
```

Reads the JSON config, resolves the target, generates mutants (if the mode includes generation), builds and tests each mutant (if the mode includes execution), and writes results, artifacts, and run metadata.

Use this when testing a specific target or an isolated configuration.

### Batch of targets

```bash
python3 run_batch.py configs/sample_config_full_pipeline.json
```

Reads a base config, expands it across all targets in the catalog, assigns a `batch_id`, launches one run per target, and writes the batch manifest to `harness/executions/batches/`.

Use this when processing an entire catalog or running multiple targets.

### Generate only

```bash
python3 run_llm.py configs/sample_config_generation.json
```

Runs only the generation phase, saving accepted and rejected mutants. No build or test execution. Useful for inspecting generation quality before spending time on execution.

### Execute previously generated mutants

```bash
python3 run_batch.py configs/sample_config_execution.json
```

Reuses a prior batch via `source_batch_id` or `source_batch_manifest`, re-launching runs in `execute_only` mode against the mutants already stored in the original runs.

## Preparing Target Coverage

The harness fails strictly if no test mapping exists for a target. The preparation flow is:

```bash
# 1. validate the catalog
python3 -m harness.targets.validation harness/datasets/catalogs/defects4j_final_catalog.json

# 2. create target_tests.csv templates
python3 -m harness.targets.test_coverage_templates

# 3. collect coverage (checks out subjects, runs defects4j coverage, writes mappings)
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/defects4j_final_catalog.json

# 4. merge all per-catalog CSVs into the global index
python3 -m harness.targets.test_coverage_templates --combine
```

Coverage logs are written to `logs/coverage/`. The collector writes intermediate checkpoints to `target_tests.csv` as it runs, so progress is visible before it finishes.

Key behaviours:

- The per-catalog CSV is always **overwritten** (consistent snapshot, not appended).
- `ok=False` in the log means a particular `defects4j coverage` call produced no usable coverage; that test is skipped.
- The final CSV includes a `match_mode` column (`strict_class_and_file` vs. weaker fallback matches).
- Existing checkouts are reused by default; pass `--no-reuse-checkout` to force a clean checkout.

## Reporting

### Summarise a single run

```bash
python3 summarize_results.py harness/executions/runs/<run_name>/execution/results.csv
```

Reads a `results.csv`, deduplicates entries, and prints an aggregated summary. Optionally writes `summary.json`:

```bash
python3 summarize_results.py harness/executions/runs/<run_name>/execution/results.csv \
  --json-out /tmp/summary.json
```

### Rebuild the global experiment index

```bash
python3 -m harness.reporting.experiment_index
```

Scans `harness/executions/runs/`, reads each `run_manifest.json`, `results.csv`, and `summary.json`, and aggregates everything into `harness/reports/experiment_index.csv`.

### Generate kill matrices

**Step 1 — base matrices from run results:**

```bash
python3 -m harness.reporting.kill_matrix --group-by run
```

Reads `results.csv` and `test_results.csv` from each run directory, outputs base files to `harness/reports/matrices/base/`.

**Step 2 — final matrices by run, target, and model:**

```bash
python3 -m harness.reporting.build_kill_matrices
```

Reads all `*_kill_matrix_long.csv` files in `harness/reports/matrices/base/`, joins with the experiment index, and writes:

- `harness/reports/matrices/per_run/<run_name>.csv`
- `harness/reports/matrices/per_target/<target_id>.csv`
- `harness/reports/matrices/per_model/model_kill_rates.csv`

Add `--format excel` to produce `.xlsx` files (requires `openpyxl`).

### Summarise a batch

```bash
python3 -m harness.reporting.summarize_batch --batch-id batch01
```

Reads the batch manifest, aggregates generation, execution, and rejection stats, and writes reports to `harness/reports/batch_summaries/`.

## Recommended Workflows

### A — Run a complete catalog

```bash
python3 -m harness.targets.validation harness/datasets/catalogs/defects4j_final_catalog.json
python3 -m harness.targets.test_coverage_templates
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/defects4j_final_catalog.json
python3 -m harness.targets.test_coverage_templates --combine
python3 run_batch.py configs/sample_config_full_pipeline.json
python3 -m harness.reporting.experiment_index
python3 -m harness.reporting.kill_matrix --group-by run
python3 -m harness.reporting.build_kill_matrices
```

### B — Test a single target

```bash
# edit a config in configs/debugging/, then:
python3 run_llm.py configs/debugging/test_gpt.json
# inspect:
#   harness/executions/runs/<run_name>/execution/results.csv
#   harness/executions/runs/<run_name>/execution/test_results.csv
#   harness/executions/runs/<run_name>/execution/summary.json
```

### C — Validate the runner without an LLM

```bash
python3 -m harness.executions.manual_mutants configs/debugging/manual_mutants_sample.json
```

Loads manually defined mutants from a JSON file and runs the full execution pipeline against them, skipping the LLM generation phase.

## Where Results Are Stored

| Path | Contents |
|---|---|
| `harness/executions/runs/<run_name>/` | Full run artifact directory |
| `harness/executions/runs/<run_name>/generation/` | Generated and rejected mutants |
| `harness/executions/runs/<run_name>/execution/results.csv` | Per-mutant execution results |
| `harness/executions/runs/<run_name>/execution/test_results.csv` | Per-test results |
| `harness/executions/runs/<run_name>/execution/summary.json` | Run summary |
| `harness/executions/batches/batchNN.json` | Batch manifest |
| `harness/reports/experiment_index.csv` | Global experiment index |
| `harness/reports/matrices/base/` | Base kill matrix files |
| `harness/reports/matrices/per_run/` | Kill matrices per run |
| `harness/reports/matrices/per_target/` | Kill matrices per target |
| `harness/reports/matrices/per_model/` | Kill rates per model |

## Tests

```bash
python3 -m unittest tests.test_kill_matrix_pipeline
```

Validates fast-failure when the target tests mapping is missing, correct generation of `test_results.csv`, and kill matrix construction from structured results.

## Notes

- Both `run_llm.py` and `run_batch.py` expect a JSON config file as their first argument; they do not support `--help` flags.
- `execute_only` mode requires `run_name` (single run) or `source_batch_id` / `source_batch_manifest` (batch).
- With `missing_target_tests_policy: "report_and_skip"`, a run without coverage data closes with status `no_coverage` instead of failing hard.
- The harness is currently centred on Defects4J; the internal structure is already abstracted by dataset but other adapters are not yet complete.
