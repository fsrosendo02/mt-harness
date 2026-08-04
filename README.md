# MT Harness

A harness for generating, running, and analysing mutants against code targets, currently focused on **Defects4J**.

Three pipeline modes are supported:

- `full` — generate mutants with an LLM and execute them
- `generate_only` — generate mutants without executing
- `execute_only` — execute mutants produced in a previous run

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

- `python3`, `openjdk-11`, `git`, `svn`, `ctags`
- `defects4j`
- the LLM provider you intend to use

Supported providers:

| Provider | Mechanism |
|---|---|
| `ollama` | `ollama run <model>` command |
| `ollama_api` | Python `ollama` library |
| `gpt4o` | external `gpt_run` binary |
| `gemini` | external `gemini_run` binary |

The `Dockerfile` documents a ready-to-use base environment.

## Configuration

All executions are driven by a JSON config file. The most commonly used fields:

| Field | Description |
|---|---|
| `catalog_file` | JSON catalog of targets |
| `target_id` | Specific target within the catalog |
| `model` / `provider` | Model name and provider |
| `prompt_file` | Generation prompt |
| `num_mutants` | Number of mutants requested from the model |
| `pipeline_mode` | `full`, `generate_only`, or `execute_only` |
| `run_mode` | `fresh`, `overwrite`, or `resume` |
| `mutant_workers` | Parallelism for mutant execution |
| `missing_target_tests_policy` | `fail` or `report_and_skip` |

Included examples in `configs/`:

- `sample_config_full_pipeline.json` — generate and execute
- `sample_config_generation.json` — generate only
- `sample_config_execution.json` — re-execute an existing batch
- `debugging/test_gpt.json` / `debugging/test_gemini.json` — single target, manual

## Running

### Single target

```bash
python3 run_llm.py configs/sample_config_full_pipeline.json
```

Resolves the target, generates mutants (if the mode includes generation), builds and tests each mutant (if the mode includes execution), and writes results and metadata.

### Batch of targets

```bash
python3 run_batch.py configs/sample_config_full_pipeline.json
```

Expands the config across all targets in the catalog, assigns a `batch_id`, runs one execution per target, and writes the batch manifest to `harness/executions/batches/`.

### Validate the runner without an LLM

```bash
python3 -m harness.executions.manual_mutants configs/debugging/manual_mutants_sample.json
```

Runs the full execution pipeline against manually defined mutants, skipping LLM generation.

## Preparing Target Coverage

The harness fails strictly if no test mapping exists for a target. Run this flow before executing a catalog:

```bash
python3 -m harness.targets.validation harness/datasets/catalogs/<catalog>.json
python3 -m harness.targets.test_coverage_templates
python3 -m harness.targets.test_coverage_collection harness/datasets/catalogs/<catalog>.json
python3 -m harness.targets.test_coverage_templates --combine
```

Coverage logs are written to `logs/coverage/`.

## Reporting

```bash
# summarise a run
python3 summarize_results.py harness/executions/runs/<run_name>/execution/results.csv

# rebuild the global experiment index
python3 -m harness.reporting.experiment_index

# generate base kill matrices
python3 -m harness.reporting.kill_matrix --group-by run

# build final matrices (per run, target, and model)
python3 -m harness.reporting.build_kill_matrices

# summarise a batch
python3 -m harness.reporting.summarize_batch --batch-id <batch_id>
```

## Where Results Are Stored

| Path | Contents |
|---|---|
| `harness/executions/runs/<run_name>/generation/` | Generated and rejected mutants |
| `harness/executions/runs/<run_name>/execution/results.csv` | Per-mutant execution results |
| `harness/executions/runs/<run_name>/execution/test_results.csv` | Per-test results |
| `harness/executions/runs/<run_name>/execution/summary.json` | Run summary |
| `harness/executions/batches/batchNN.json` | Batch manifest |
| `harness/reports/experiment_index.csv` | Global experiment index |
| `harness/reports/matrices/` | Kill matrices (per run, target, model) |

## Notes

- `run_llm.py` and `run_batch.py` take a JSON config as their only argument; they do not support `--help`.
- `execute_only` requires `run_name` (single run) or `source_batch_id` / `source_batch_manifest` (batch).
- With `missing_target_tests_policy: "report_and_skip"`, a run without coverage data closes with status `no_coverage` instead of failing hard.
- The harness is currently centred on Defects4J; the internal structure is abstracted by dataset but other adapters are not yet complete.
