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
    "notes": "",
}


def build_experiment_metadata(**overrides) -> dict:
    metadata = dict(DEFAULT_EXPERIMENT_METADATA)
    metadata.update(overrides)
    return metadata
