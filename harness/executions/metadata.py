from __future__ import annotations


DEFAULT_EXPERIMENT_METADATA = {
    "experiment_name": None,
    "mutant_source": None,
    "model_name": None,
    "model_provider": None,
    "prompt_name": None,
    "prompt_version": None,
    "temperature": None,
    "n_requested_mutants": None,
    "generation_mode": None,
    "dataset_split": None,
    "batch_id": None,
    "target_id": None,
    "run_group_id": None,
    "run_index_for_target": None,
    "runs_per_target": None,
    "mutant_workers": 1,
    "missing_target_tests_policy": None,
    "n_accepted_mutants": None,
    "n_rejected_mutants": None,
    "acceptance_rate": None,
    "rej_duplicate_mutant": 0,
    "rej_unchanged_mutant": 0,
    "rej_non_executable_change": 0,
    "rej_non_executable_structural_change": 0,
    "rej_precode_not_found": 0,
    "rej_ambiguous_precode_match": 0,
    "rej_missing_or_invalid_aftercode": 0,
    "rej_missing_or_invalid_precode": 0,
    "parsed_mutants_integrity": None,
    "requested_mutant_count": None,
    "loaded_mutant_count": None,
    "recovered_mutant_count": 0,
    "missing_mutant_count": None,
    "target_test_coverage_status": None,
    "eligible_test_count": None,
    "target_tests_csv_path": None,
    "notes": "",
}


def build_experiment_metadata(**overrides) -> dict:
    metadata = dict(DEFAULT_EXPERIMENT_METADATA)
    metadata.update(overrides)
    return metadata
