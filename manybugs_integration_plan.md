# ManyBugs Integration Plan for `mt-harness`

## Objective

Integrate an initial `ManyBugs` capability into the existing harness without
forking the pipeline into separate Java and C stacks. The goal is to reuse the
current run orchestration, storage, reporting, and mutant evaluation flow, and
extend only the parts that are still hardcoded to `Defects4J`.

This plan is intentionally aligned with the current repository structure:

- adapters live in `harness/adapters/`
- catalogs live in `harness/datasets/catalogs/`
- target-test mappings live in `harness/datasets/coverage/catalogs/`
- runs and batches live in `harness/executions/`
- mutation generation and parsing live in `harness/llm/`

## Architectural Position

The harness is not purely language-agnostic today, but it is already shaped
around a dataset adapter boundary:

- `BenchmarkAdapter` defines the execution contract.
- `Subject` already carries `dataset` and `language`.
- `Target` already carries `language`.
- batch expansion already propagates `dataset`, `language`, `file`, `function`,
  and target line bounds from catalog entries.

The correct integration model for `ManyBugs` is therefore:

1. add a new dataset adapter
2. add ManyBugs catalogs in the existing catalog format
3. reuse `run_llm.py`, `run_batch.py`, `MutationRunner`, storage, and reports
4. defer any protocol changes in LLM output format unless they are proven necessary

The incorrect model is to introduce a second parallel harness tree such as
`harness/java/` and `harness/c/`.

## Non-Goals for the First Iteration

The first integration should not attempt all of the following at once:

- no parallel `run_experiment.py` orchestration path
- no new result schema
- no mandatory Docker abstraction in phase 1
- no immediate switch from line/snippet mutation format to unified diffs
- no mandatory Mull baseline before the core pipeline works

These can be layered later if needed.

## Constraints from the Current Codebase

The implementation must respect these current realities:

1. `run_llm.py` selects the adapter by `dataset`, but today only accepts
   `defects4j`.
2. `resolve_target()` supports non-Java targets only when `start_line` and
   `end_line` are already present in the catalog.
3. prompt generation and LLM parsing are currently built around full-target code
   plus line-level edits, not patch diffs.
4. the result CSV and per-test CSV schemas are already dataset-agnostic enough
   for `ManyBugs`.
5. target-test mapping is keyed by `(dataset, subject_id, target_id)`, so
   `ManyBugs` can plug into the current coverage mapping model.

## Proposed Repository Shape

No new top-level language split should be introduced. The proposed additions are:

```text
.
├── manybugs_integration_plan.md
├── harness/
│   ├── adapters/
│   │   └── manybugs.py
│   ├── datasets/
│   │   ├── catalogs/
│   │   │   └── manybugs_<project>_pilot.json
│   │   └── coverage/
│   │       └── catalogs/
│   │           └── manybugs_<project>_pilot/
│   │               └── target_tests.csv
│   └── targets/
│       └── build_manybugs_catalog.py
└── scripts/
    └── manybugs/
        ├── checkout_subject.sh
        ├── build_subject.sh
        └── test_subject.sh
```

Notes:

- `scripts/manybugs/` is only a suggested location for benchmark-specific shell
  helpers. If Python wrappers are cleaner, that is acceptable.
- `build_manybugs_catalog.py` should be treated as optional for the first pilot.
  A manual catalog is acceptable at the start.

## Delivery Strategy

The work should be delivered in two tracks:

- Track A: get the harness to run `ManyBugs` end-to-end with the existing LLM
  pipeline
- Track B: improve reproducibility and comparability after the first successful
  run

Track A is required. Track B is optional until Track A is stable.

## Phase 0: Scope and Dataset Triage

### Goal

Choose one `ManyBugs` project and a small pilot set of bug instances that are
realistic for the current harness.

### Recommended Output

A short pilot manifest maintained outside code at first, for example:

- selected project name
- candidate bug IDs
- build status
- test oracle status
- known source file
- target function
- candidate line range
- expected blockers

### Selection Rules

Only include bugs that satisfy all of the following:

1. fixed and buggy revisions are both recoverable
2. the project can be built in a repeatable way
3. the test harness can distinguish buggy from fixed
4. at least one target region can be identified with stable line bounds
5. build and test turnaround is acceptable for repeated mutant execution

### Practical Scope

Start with:

- 1 project
- 5 to 10 bug instances
- 1 target per bug instance

Do not start with 15 to 20 instances. That is too large for the first pass and
will hide design mistakes behind operational noise.

### Acceptance Criteria

- one project is selected
- pilot bug list exists
- each candidate has an identified target file and approximate target lines
- each candidate is marked as `keep`, `exclude`, or `needs investigation`

## Phase 1: Catalog-First Integration

### Goal

Represent `ManyBugs` targets in the existing catalog format before changing
execution code.

### Why This First

The harness already knows how to expand runs from catalogs. If the `ManyBugs`
catalog can match the current schema, the rest of the integration remains local
to adapters and benchmark helpers.

### Required Catalog Fields

Each target entry should include:

- `dataset`: `manybugs`
- `subject`: benchmark subject identifier
- `version`: likely `f` for the fixed baseline used during mutation
- `language`: `c`
- `file`
- `function`
- `start_line`
- `end_line`
- `target_id`

Optional metadata may also include:

- selected bug identifier
- revision identifiers
- project name
- target provenance
- notes on oracle stability

### Important Design Choice

For `ManyBugs`, `start_line` and `end_line` should be mandatory in the pilot.
This avoids depending on language-specific extraction logic that does not exist
yet for C.

### Deliverables

1. `harness/datasets/catalogs/manybugs_<project>_pilot.json`
2. one pilot entry per selected subject/target
3. target IDs consistent with current naming conventions

### Acceptance Criteria

- catalog loads through the existing catalog loader
- batch expansion can consume the catalog without special handling
- every entry has explicit target line bounds

## Phase 2: `ManyBugsAdapter` Minimum Viable Adapter

### Goal

Implement `ManyBugs` as a new `BenchmarkAdapter`, not as a new harness branch.

### Required Interface

The adapter must satisfy the current contract:

1. `checkout_subject(subject, workdir)`
2. `build(workdir)`
3. `test(workdir)`
4. `test_target(workdir, eligible_tests)`
5. `apply_mutant(workdir, target, mutant_code)`
6. `reset_subject(workdir)`

### Responsibilities

`checkout_subject`

- materialize the fixed subject revision into `workdir`
- interpret `subject.subject_id` according to the catalog convention

`build`

- compile the checked-out source tree
- return `(success, log)` in the same style as the current adapter

`test`

- run the benchmark test suite
- determine baseline pass/fail

`test_target`

- return structured per-test observations in the harness format
- map native benchmark test output into `PASS`, `FAIL`, `ERROR`, `NOT_RUN`

`apply_mutant`

- replace the target region contents with the generated mutant code
- follow the same target line slicing model used today

`reset_subject`

- restore the checkout to its clean baseline within the worker workdir

### First-Iteration Simplification

The first version does not need to implement a sophisticated reset mechanism if
the runner already works by copying a clean baseline snapshot per worker. It may
be enough for `reset_subject` to be a no-op if the worker lifecycle guarantees a
fresh tree before each evaluation.

### Deliverables

1. `harness/adapters/manybugs.py`
2. adapter registration in the existing adapter selection path
3. minimal smoke coverage through a manual run

### Acceptance Criteria

- `dataset="manybugs"` resolves to the new adapter
- a subject can be checked out into a run workdir
- baseline build and baseline test can complete successfully for at least one
  fixed pilot subject

## Phase 3: Test Oracle Mapping and Target-Test Coverage

### Goal

Plug `ManyBugs` into the existing target-test filtering model instead of
inventing a separate test selection mechanism.

### Rationale

The harness already expects a `target_tests.csv` keyed by:

- `dataset`
- `subject_id`
- `target_id`
- `test_name`

That mechanism should be reused.

### Initial Strategy

For the pilot, generate `target_tests.csv` by one of these methods:

1. manual curation from the benchmark’s failing and relevant tests
2. instrumentation-based coverage collection if feasible
3. a temporary broad mapping where all available tests are assigned to the
   target, only if runtime is still acceptable

Option 1 is the safest starting point. Option 3 is acceptable only for a very
small pilot.

### Deliverables

1. `harness/datasets/coverage/catalogs/manybugs_<project>_pilot/target_tests.csv`
2. naming normalization for `test_name`
3. documented mapping procedure

### Acceptance Criteria

- `target_tests_for()` resolves non-empty test lists for pilot targets
- the runner does not fail with `no_mapped_target_tests`
- per-test results are written to `execution/test_results.csv`

## Phase 4: Reuse the Existing LLM Mutation Protocol

### Goal

Run `ManyBugs` through the existing LLM generation and parsing pipeline with the
smallest possible change surface.

### Decision

Keep the current mutation protocol for the first iteration:

- prompt over the extracted target code
- model returns structured line edits
- parser reconstructs full mutated target code
- adapter writes mutated code back into the source file

Do not switch to unified diff output in the pilot.

### Rationale

Switching to diffs would require changes to:

- prompt design
- parsing and rejection logic
- mutant application
- debug artifacts
- possibly deduplication semantics

That is too much risk before the adapter path is proven.

### Prompt Implications

The current prompt builder is Java-biased in naming, but not irreparably so.
The first implementation can succeed with one of these approaches:

1. keep the existing prompt machinery and provide a C-specific prompt file that
   interprets the placeholders generically
2. minimally generalize prompt placeholder names later, once `ManyBugs` runs end
   to end

Option 1 is preferred for the pilot.

### Parser Implications

The parser already accepts `language="c"` for syntax sanity. That should be
preserved and exercised before any parser redesign is considered.

### Deliverables

1. one pilot prompt file for C
2. config examples for `dataset="manybugs"`
3. one successful `generate_only` run
4. one successful `full` run

### Acceptance Criteria

- accepted mutants are parsed under `language="c"`
- at least one mutant compiles
- at least one mutant reaches test execution
- results are stored in the standard run directories

## Phase 5: Pilot End-to-End Execution

### Goal

Run the full current pipeline against the pilot catalog using the existing
entrypoints.

### Required User Flows

1. individual run via `run_llm.py`
2. batch expansion via `run_batch.py`
3. `generate_only`
4. `execute_only`

The integration is not complete if it only works through an ad hoc script.

### Deliverables

1. one config for single-target smoke testing
2. one config for pilot batch execution
3. at least one completed pilot batch with artifacts in:
   - `harness/executions/runs/`
   - `harness/executions/batches/`
   - `harness/reports/`

### Acceptance Criteria

- batch manifest generation works
- run manifests are written correctly
- result CSVs and test result CSVs are populated
- summaries and reports still function without schema changes

## Phase 6: Stabilization and Hardening

### Goal

Reduce operational fragility after the first successful pilot.

### Hardening Topics

1. improve checkout determinism
2. improve build log normalization
3. improve test name normalization
4. reduce rebuild time where possible
5. document benchmark prerequisites clearly
6. add smoke tests for the adapter and catalog assumptions

### Suggested Tests

1. adapter unit tests for:
   - checkout invocation
   - build result parsing
   - test result parsing
2. catalog loader tests with `manybugs` entries
3. target resolution tests for non-Java targets with explicit line bounds

### Acceptance Criteria

- at least one automated smoke test covers the adapter path
- rerunning the same pilot configuration is reproducible
- setup steps are documented well enough for another user to repeat the pilot

## Phase 7: Optional Reproducibility Layer

### Goal

Introduce containerization only if the pilot proves that host setup is too
fragile or too expensive to maintain.

### Position

Docker is a potential phase 2 improvement, not a prerequisite for phase 1.

### When to Add It

Add Docker if one or more of these become true:

1. build dependencies are too host-specific
2. benchmark setup is difficult to reproduce
3. CI execution becomes a requirement
4. subject-specific dependency drift becomes a recurring issue

### Deliverables

1. optional project-specific Dockerfiles or a shared base image
2. adapter support for host or container execution
3. documented execution mode selection

### Acceptance Criteria

- container execution reproduces the same baseline outcomes as host execution
- Docker does not become mandatory for local development unless justified

## Phase 8: Optional Baseline with Mull

### Goal

Add operator-based baseline comparison only after the `ManyBugs` path works in
the native harness pipeline.

### Position

Mull should be modeled as an additional evaluation capability, not as a reason
to restructure the harness.

### Integration Approach

Treat Mull as one of:

1. a sidecar benchmark script that produces comparison artifacts
2. a later reporting extension
3. a separate baseline command path that consumes the same subject catalog

Do not block core `ManyBugs` support on Mull integration.

### Acceptance Criteria

- Mull artifacts can be associated with the same `subject_id` and `target_id`
- no change is required to the primary LLM run path

## Recommended File-Level Work Breakdown

### Required in the First Implementation

- `harness/adapters/manybugs.py`
- `run_llm.py`
- maybe `README.md`
- one `ManyBugs` pilot catalog
- one `ManyBugs` target-tests mapping
- one or more benchmark helper scripts

### Likely Not Required Immediately

- `MutationRunner`
- result schema modules
- summary/report aggregation
- batch manifest structure

### Optional Later

- `harness/targets/build_manybugs_catalog.py`
- C-specific target discovery helpers
- Docker support
- Mull integration

## Risks and Mitigations

### Risk 1: `ManyBugs` test output is hard to normalize

Mitigation:

- start with a project whose test driver is already scriptable
- normalize test names early
- keep `test()` and `test_target()` parsing logic separate if necessary

### Risk 2: target line ranges drift across revisions

Mitigation:

- mutate only the fixed version in the pilot
- record explicit line ranges in the catalog
- avoid automatic discovery until manual ranges are proven stable

### Risk 3: prompt quality drops on C targets

Mitigation:

- keep the protocol unchanged first
- create a C-specific prompt file
- start with simple, bounded target regions

### Risk 4: build times make mutant execution impractical

Mitigation:

- keep the pilot small
- prefer fast subjects
- use broad all-tests mappings only if runtime remains acceptable

### Risk 5: over-design before first successful run

Mitigation:

- require one real end-to-end pilot run before adding Docker, Mull, or parser
  redesign

## Milestones

### Milestone 1: Catalog Ready

- pilot project selected
- pilot catalog created
- target line ranges fixed

### Milestone 2: Adapter Ready

- `ManyBugsAdapter` can checkout, build, and test a fixed subject

### Milestone 3: Harness Path Ready

- `run_llm.py` can dispatch `dataset="manybugs"`
- `generate_only` works for at least one target

### Milestone 4: End-to-End Pilot

- `full` mode completes for at least one target
- results land in the standard execution directories

### Milestone 5: Batch Pilot

- one small batch completes against a `ManyBugs` pilot catalog

## Recommended Order of Implementation

1. choose the pilot project and bug subset
2. create the pilot catalog manually
3. implement the adapter
4. wire adapter selection in `run_llm.py`
5. create a minimal target-tests mapping
6. run `generate_only`
7. run `full`
8. run a small batch
9. only then decide on Docker and Mull

## Definition of Done

`ManyBugs` support should be considered integrated into the harness when all of
the following are true:

1. a `ManyBugs` catalog can be expanded by `run_batch.py`
2. `run_llm.py` can execute a `ManyBugs` target through the existing pipeline
3. the adapter produces baseline build/test behavior compatible with the current
   evaluator flow
4. pilot runs produce standard `results.csv`, `test_results.csv`, and summary
   artifacts
5. no separate Java-vs-C harness tree is required

## Final Recommendation

Build `ManyBugs` as a new dataset inside the current harness, not as a new
language-specific subsystem. The shortest viable path is:

- catalog first
- adapter second
- existing LLM protocol third
- reproducibility and comparative baselines later

That path minimizes architectural churn, keeps the current reporting pipeline
intact, and makes the first successful run arrive earlier.
