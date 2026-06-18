import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.adapters.manybugs import ManyBugsAdapter
from harness.executions.metadata import build_experiment_metadata
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import load_generation_mutants_with_recovery, save_generation_artifacts
from harness.llm.parsing import MissingMutantsListError, parse_report_to_dict
from harness.llm.prompt_builder import PromptBuilder
from harness.llm.providers.gemini_provider import GeminiProvider
from harness.llm.providers.gpt4o_provider import GPT4oProvider
from harness.llm.providers.ollama_api_provider import OllamaApiProvider
from harness.models import Subject, Target
from harness.runners.mutation_runner import MutationRunner
from harness.storage.artifacts import save_rejected_mutant_artifacts
from harness.storage.cleanup import cleanup_paths
from harness.storage.layout import (
    catalog_target_tests_csv_path,
    llm_run_dir,
    resolve_run_dir,
    execution_dir,
    execution_results_path,
    execution_summary_path,
    execution_test_results_path,
    generation_dir,
    manifest_path,
)
from harness.storage.results import RESULT_FIELDNAMES
from harness.storage.test_results import TEST_RESULT_FIELDNAMES
from harness.storage.run_state import prepare_run_dir, write_run_manifest
from harness.reporting.kill_matrix import build_kill_matrices
from harness.targets.resolver import resolve_target
from harness.targets.target_tests import MissingTargetTestsFileError, NoMappedTargetTestsError


PIPELINE_MODE_FULL = "full"
PIPELINE_MODE_GENERATE_ONLY = "generate_only"
PIPELINE_MODE_EXECUTE_ONLY = "execute_only"
PIPELINE_MODES = {
    PIPELINE_MODE_FULL,
    PIPELINE_MODE_GENERATE_ONLY,
    PIPELINE_MODE_EXECUTE_ONLY,
}

GENERATION_REQUIRED_FIELDS = [
    "dataset",
    "subject",
    "version",
    "model",
    "num_mutants",
    "timeout",
    "run_name",
    "prompt_file",
]


class TeeStream:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()

    def isatty(self):
        return any(getattr(s, "isatty", lambda: False)() for s in self.streams)


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def log_duration(label: str, start: float) -> None:
    log(f"{label} finished in {time.time() - start:.2f}s")


def run_path_for_config(cfg: dict) -> Path:
    subdir = cfg.get("runs_subdir", "").strip("/")
    base = runs_root()
    return (base / subdir / cfg["run_name"]) if subdir else (base / cfg["run_name"])


def extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def load_raw_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def pipeline_mode_from_cfg(cfg: dict) -> str:
    mode = str(cfg.get("pipeline_mode", PIPELINE_MODE_FULL)).strip().lower()
    if mode not in PIPELINE_MODES:
        raise ValueError(f"Unsupported pipeline_mode: {mode}")
    return mode


def validate_generation_config(cfg: dict) -> dict:
    missing = [field for field in GENERATION_REQUIRED_FIELDS if field not in cfg]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    has_catalog_target = "target_id" in cfg
    has_manual_target = "file" in cfg and "function" in cfg

    if not has_catalog_target and not has_manual_target:
        raise ValueError(
            "Config must define either:\n"
            "  - target_id + catalog_file\n"
            "or\n"
            "  - file + function"
        )

    if has_catalog_target and "catalog_file" not in cfg:
        raise ValueError("Config uses target_id but is missing catalog_file")

    return cfg


def merge_with_existing_run_config(cfg: dict) -> dict:
    run_name = cfg.get("run_name")
    if not run_name:
        return cfg

    run_path = resolve_run_dir(
        str(run_name),
        cfg.get("batch_id"),
    )
    run_config_path = run_path / "run_config.json"
    if not run_config_path.exists():
        return cfg

    existing = json.loads(run_config_path.read_text(encoding="utf-8"))
    merged = dict(existing)
    merged.update(cfg)
    return merged


def load_config(config_path: str) -> dict:
    cfg = load_raw_config(config_path)
    cfg = merge_with_existing_run_config(cfg)

    pipeline_mode = pipeline_mode_from_cfg(cfg)
    cfg["pipeline_mode"] = pipeline_mode
    cfg["missing_target_tests_policy"] = missing_target_tests_policy_from_cfg(cfg)

    if pipeline_mode in {PIPELINE_MODE_FULL, PIPELINE_MODE_GENERATE_ONLY}:
        cfg = validate_generation_config(cfg)
    elif "run_name" not in cfg:
        raise ValueError("execute_only config requires run_name")

    return cfg


def build_adapter(dataset: str):
    if dataset == "defects4j":
        return Defects4JAdapter()
    if dataset == "manybugs":
        return ManyBugsAdapter()

    raise ValueError(f"Unsupported dataset: {dataset}")


def write_empty_results_csv(run_dir: str):
    csv_path = execution_results_path(run_dir)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDNAMES)
        writer.writeheader()

    test_results_path = execution_test_results_path(run_dir)
    with test_results_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TEST_RESULT_FIELDNAMES)
        writer.writeheader()

    return csv_path


def write_empty_summary_json(
    run_dir: str,
    *,
    run_status: str = "unknown",
    failure_reason: str | None = None,
    failure_message: str | None = None,
):
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    summary_path = execution_summary_path(run_dir)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    execution_fields = {
        "total_mutants": None,
        "build_successes": None,
        "executable_mutants": None,
        "killed_mutants": None,
        "survived_mutants": None,
        "baseline_failures": None,
        "build_success_rate": None,
        "executable_yield": None,
        "mutation_score": None,
    }

    payload = {
        "csv_path": str(execution_results_path(run_dir)),
        "json_path": str(summary_path),
        "run_status": run_status,
        "failure_reason": failure_reason,
        "failure_message": failure_message,
        "deduplicated": None,
        "input_row_count": None,
        "used_row_count": None,
        "duplicate_rows_within_run": None,
        "duplicate_mutants_within_run": None,
        "unique_mutants_within_run": None,
        "overall": execution_fields,
        "by_subject_function": [],
    }

    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary_path


def classify_exception(exc: Exception) -> tuple[str, str, str]:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()

    if "timed out" in lowered or lowered.endswith("_timeout") or " timeout" in lowered:
        return "failure", "timeout", message
    if isinstance(exc, MissingTargetTestsFileError):
        return "failure", "missing_target_tests_file", message
    if isinstance(exc, NoMappedTargetTestsError):
        return "failure", "no_mapped_target_tests", message

    return "failure", exc.__class__.__name__, message


def build_subject(cfg: dict) -> Subject:
    return Subject(
        dataset=cfg["dataset"],
        subject_id=cfg["subject"],
        language=cfg.get("language", "java"),
        version=cfg["version"],
    )


def build_target_stub(cfg: dict) -> Target:
    return Target(
        file_path=cfg.get("file", ""),
        function_name=cfg.get("function", ""),
        start_line=cfg.get("start_line"),
        end_line=cfg.get("end_line"),
        language=cfg.get("language", "java"),
        target_id=cfg.get("target_id"),
    )


def save_run_config(run_path: Path, cfg: dict) -> None:
    run_path.mkdir(parents=True, exist_ok=True)
    (run_path / "run_config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def prepare_execution_only_run_dir(run_path: Path, mode: str) -> None:
    if mode not in {"fresh", "overwrite", "resume"}:
        raise ValueError(f"Unsupported run mode: {mode}")

    if not run_path.exists():
        raise FileNotFoundError(
            f"Run directory not found for execute_only mode: {run_path}"
        )

    execution_path = execution_dir(run_path)
    run_error_path = run_path / "run_error.txt"

    if mode == "fresh" and execution_path.exists():
        raise FileExistsError(
            f"Execution artifacts already exist in {execution_path}; "
            "use run_mode='overwrite' or run_mode='resume'."
        )

    if mode == "overwrite":
        if execution_path.exists():
            import shutil

            shutil.rmtree(execution_path)
        if run_error_path.exists():
            run_error_path.unlink()


def load_existing_manifest(run_dir: str | Path) -> dict:
    manifest_file = manifest_path(run_dir)
    if not manifest_file.exists():
        raise FileNotFoundError(f"Run manifest not found: {manifest_file}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    run_path = Path(run_dir)
    if not manifest.get("run_name"):
        manifest["run_name"] = run_path.name
        manifest["run_dir"] = str(run_path)
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_subject_target_from_manifest(run_dir: str | Path) -> tuple[Subject, Target, dict]:
    manifest = load_existing_manifest(run_dir)
    subject_data = manifest.get("subject") or {}
    target_data = manifest.get("target") or {}

    subject = Subject(
        dataset=subject_data.get("dataset", "unknown"),
        subject_id=subject_data.get("subject_id", "unknown"),
        language=target_data.get("language") or "java",
        version=subject_data.get("version", "unknown"),
    )
    target = Target(
        file_path=target_data.get("file_path", ""),
        function_name=target_data.get("function_name", ""),
        start_line=target_data.get("start_line"),
        end_line=target_data.get("end_line"),
        language=target_data.get("language") or "java",
        target_id=target_data.get("target_id"),
    )
    extra_metadata = dict(manifest.get("extra_metadata") or {})
    return subject, target, extra_metadata


def ensure_execute_only_config(cfg: dict, manifest_subject: Subject, manifest_target: Target) -> dict:
    cfg.setdefault("dataset", manifest_subject.dataset)
    cfg.setdefault("subject", manifest_subject.subject_id)
    cfg.setdefault("version", manifest_subject.version)
    cfg.setdefault("language", manifest_target.language or manifest_subject.language)
    cfg.setdefault("file", manifest_target.file_path)
    cfg.setdefault("function", manifest_target.function_name)
    cfg.setdefault("start_line", manifest_target.start_line)
    cfg.setdefault("end_line", manifest_target.end_line)
    cfg.setdefault("target_id", manifest_target.target_id)
    if cfg.get("target_id") and not cfg.get("catalog_file"):
        raise ValueError(
            "execute_only config uses target_id but is missing catalog_file; "
            "batch executions must pass the source catalog to resolve target tests"
        )
    return cfg


def resolve_run_target_tests_csv(cfg: dict) -> str | None:
    catalog_file = cfg.get("catalog_file")
    if cfg.get("target_id") and not catalog_file:
        raise ValueError(
            "Config uses target_id but is missing catalog_file; cannot resolve "
            "catalog-specific target_tests.csv"
        )
    if not catalog_file:
        return None
    return str(catalog_target_tests_csv_path(catalog_file))


def missing_target_tests_policy_from_cfg(cfg: dict) -> str:
    policy = str(cfg.get("missing_target_tests_policy", "fail")).strip().lower()
    if policy not in {"fail", "report_and_skip"}:
        raise ValueError(f"Unsupported missing_target_tests_policy: {policy}")
    return policy


def build_provider(cfg: dict):
    provider_type = cfg.get("provider", "ollama")

    if provider_type in {"ollama", "ollama_old"}:
        provider = OllamaApiProvider(cfg["model"], timeout_seconds=cfg["timeout"])
    elif provider_type == "gpt4o":
        provider = GPT4oProvider(cfg["model"], timeout_seconds=cfg["timeout"])
    elif provider_type == "gemini":
        provider = GeminiProvider(cfg["model"], timeout_seconds=cfg["timeout"])
    else:
        raise ValueError(f"Unknown provider: {provider_type}")

    return provider_type, provider


def build_extra_metadata(
    *,
    cfg: dict,
    config_path: str,
    provider_type: str,
    target: Target,
    n_requested_mutants: int,
    n_accepted_mutants: int,
    n_rejected_mutants: int,
    acceptance_rate: float | None,
    rejection_reason_counts: dict[str, int],
    parse_failed: bool,
    parse_error_message: str | None,
) -> dict:
    return build_experiment_metadata(
        experiment_name=cfg["run_name"],
        mutant_source="llm",
        model_name=cfg["model"],
        model_provider=cfg.get("model_provider", provider_type),
        prompt_name=Path(cfg["prompt_file"]).stem,
        prompt_version=cfg.get("prompt_version", "file_based"),
        temperature=cfg.get("temperature", 0.0),
        n_requested_mutants=n_requested_mutants,
        generation_mode=cfg.get("generation_mode", "batch_once"),
        dataset_split=cfg.get("dataset_split"),
        batch_id=cfg.get("batch_id"),
        target_id=getattr(target, "target_id", None),
        run_group_id=cfg.get("run_group_id"),
        run_index_for_target=cfg.get("run_index_for_target"),
        runs_per_target=cfg.get("runs_per_target"),
        mutant_workers=cfg.get("mutant_workers", 1),
        n_accepted_mutants=n_accepted_mutants,
        n_rejected_mutants=n_rejected_mutants,
        acceptance_rate=acceptance_rate,
        rej_duplicate_mutant=rejection_reason_counts.get("duplicate_mutant", 0),
        rej_unchanged_mutant=rejection_reason_counts.get("unchanged_mutant", 0),
        rej_non_executable_change=rejection_reason_counts.get(
            "non_executable_change",
            0,
        ),
        rej_non_executable_structural_change=sum(
            count
            for reason, count in rejection_reason_counts.items()
            if str(reason).startswith("non_executable_structural_change")
        ),
        rej_precode_not_found=rejection_reason_counts.get("precode_not_found", 0),
        rej_ambiguous_precode_match=rejection_reason_counts.get(
            "ambiguous_precode_match",
            0,
        ),
        rej_invalid_json_response=rejection_reason_counts.get(
            "invalid_json_response",
            0,
        ),
        parse_failed=parse_failed,
        parse_error_message=parse_error_message,
        notes=cfg.get(
            "notes",
            f"Prompt file: {cfg['prompt_file']}; Config file: {config_path}",
        ),
        missing_target_tests_policy=cfg.get("missing_target_tests_policy"),
    )


def ensure_failed_run_artifacts(
    *,
    cfg: dict,
    run_dir: str,
    workdir_base: str,
    run_status: str,
    failure_reason: str,
    failure_message: str,
    subject: Subject | None,
    target: Target | None,
    extra_metadata: dict | None,
    started_at_utc: str,
) -> None:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    manifest_file = manifest_path(run_dir)

    resolved_subject = subject or Subject(
        dataset=cfg.get("dataset", "unknown"),
        subject_id=cfg.get("subject", "unknown"),
        language=cfg.get("language", "java"),
        version=cfg.get("version", "unknown"),
    )
    resolved_target = target or build_target_stub(cfg)
    failure_extra_metadata = dict(extra_metadata or {})
    failure_extra_metadata.setdefault("batch_id", cfg.get("batch_id"))
    failure_extra_metadata.setdefault("target_id", getattr(resolved_target, "target_id", None))
    failure_extra_metadata.setdefault("run_group_id", cfg.get("run_group_id"))
    failure_extra_metadata.setdefault("run_index_for_target", cfg.get("run_index_for_target"))
    failure_extra_metadata.setdefault("runs_per_target", cfg.get("runs_per_target"))
    failure_extra_metadata.setdefault("model_name", cfg.get("model"))
    failure_extra_metadata.setdefault("model_provider", cfg.get("provider"))
    failure_extra_metadata.setdefault("mutant_workers", cfg.get("mutant_workers", 1))

    write_run_manifest(
        run_dir=run_dir,
        subject=resolved_subject,
        target=resolved_target,
        mutants=[],
        run_mode=cfg.get("run_mode", "overwrite"),
        workdir_base=workdir_base,
        extra_metadata=failure_extra_metadata,
        status=run_status,
        failure_reason=failure_reason,
        failure_message=failure_message,
        started_at_utc=started_at_utc,
        completed_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["requested_mutant_count"] = cfg.get(
            "num_mutants",
            manifest.get("requested_mutant_count", 0),
        )
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        pass

    write_empty_results_csv(run_dir)
    write_empty_summary_json(
        run_dir,
        run_status=run_status,
        failure_reason=failure_reason,
        failure_message=failure_message,
    )

    error_path = run_path / "run_error.txt"
    error_path.write_text(failure_message.rstrip() + "\n", encoding="utf-8")


def maybe_rebuild_index(cfg: dict) -> None:
    if cfg.get("rebuild_index", True):
        t = time.time()
        from harness.reporting.experiment_index import build_experiment_index

        build_experiment_index()
        log_duration("Rebuild experiment index", t)


def maybe_build_kill_matrix(cfg: dict) -> None:
    t = time.time()
    build_kill_matrices(group_by="run")
    log_duration("Build kill matrices", t)


def main():
    total_start = time.time()
    started_at_utc = datetime.now(timezone.utc).isoformat()

    if len(sys.argv) != 2:
        print("Usage: python3 run_llm.py <config.json>")
        sys.exit(1)

    config_path = sys.argv[1]
    log(f"run_llm.py started with config: {config_path}")

    cfg = {}
    subject = None
    target = None
    extra_metadata = None
    run_dir = None
    workdir_base = None
    base_snapshot_dir = None
    _log_file = None

    try:
        t = time.time()
        cfg = load_config(config_path)
        pipeline_mode = cfg["pipeline_mode"]
        log_duration("Config load", t)
        log(f"Pipeline mode: {pipeline_mode}")

        run_path = run_path_for_config(cfg)
        run_dir = str(run_path)
        workdir_base = f"tmp/{cfg['run_name']}"
        base_snapshot_dir = f"tmp/base_{cfg['run_name']}"

        if pipeline_mode in {PIPELINE_MODE_FULL, PIPELINE_MODE_GENERATE_ONLY}:
            run_path = prepare_run_dir(run_dir, mode=cfg.get("run_mode", "overwrite"))
            save_run_config(run_path, cfg)

            # Tee stdout+stderr to <run_dir>/run.log — opened after prepare_run_dir
            # so overwrite mode does not wipe the log file
            _log_file = (run_path / "run.log").open("w", encoding="utf-8")
            sys.stdout = TeeStream(sys.__stdout__, _log_file)
            sys.stderr = TeeStream(sys.__stderr__, _log_file)
            log(f"Log file: {run_path / 'run.log'}")

            t = time.time()
            adapter = build_adapter(cfg["dataset"])
            log_duration("Adapter build", t)

            t = time.time()
            subject = build_subject(cfg)
            log_duration("Subject build", t)

            t = time.time()
            provider_type, provider = build_provider(cfg)
            prompt_builder = PromptBuilder(cfg["prompt_file"])
            generator = LLMMutantGenerator(provider=provider, prompt_builder=prompt_builder)
            runner = MutationRunner(adapter)
            log_duration("Provider/generator/runner init", t)

            t = time.time()
            log(f"Checking out subject into {base_snapshot_dir}")
            adapter.checkout_subject(subject, base_snapshot_dir)
            log_duration("Checkout", t)

            t = time.time()
            target, target_code = resolve_target(cfg, base_snapshot_dir)
            log_duration("Target resolution", t)

            t = time.time()
            built_prompt = generator.prompt_builder.build(
                subject=subject,
                target=target,
                target_code=target_code,
                context_code=None,
                num_mutants=cfg["num_mutants"],
            )
            log_duration("Prompt build", t)

            log(f"Config file: {config_path}")
            log(f"Dataset: {cfg['dataset']}")
            log(f"Subject: {cfg['subject']}")
            log(f"Version: {cfg['version']}")
            if cfg.get("target_id"):
                log(f"Target ID: {cfg['target_id']}")
            log(f"Resolved file: {target.file_path}")
            log(f"Resolved function: {target.function_name}")
            log(f"Resolved start_line: {target.start_line}")
            log(f"Resolved end_line: {target.end_line}")
            log(f"Prompt file: {built_prompt.prompt_file}")
            log(f"Prompt length: {len(built_prompt.user_prompt)} chars")

            llm_start = time.time()
            log("[generation] provider.generate start")
            raw_text = provider.generate(
                system_prompt=built_prompt.system_prompt,
                user_prompt=built_prompt.user_prompt,
                temperature=cfg.get("temperature", 0.0),
            )
            log_duration("[generation] provider.generate", llm_start)

            t = time.time()
            raw_text = extract_json(raw_text)
            log_duration("JSON extraction", t)

            parse_failed = False
            parse_error_message = None
            missing_mutants_list = False

            parse_start = time.time()
            try:
                mutants, report = generator.parser.parse_with_report(
                    raw_text=raw_text,
                    requested_count=cfg["num_mutants"],
                    original_target_code=target_code,
                    language=target.language,
                )
            except MissingMutantsListError as e:
                parse_error_message = str(e)
                missing_mutants_list = True
                mutants = []

                from harness.llm.parsing import ParseReport

                report = ParseReport(
                    requested_count=cfg["num_mutants"],
                    accepted_count=0,
                    rejected_count=cfg["num_mutants"],
                    rejections=[],
                )
            except Exception as e:
                parse_failed = True
                parse_error_message = str(e)
                mutants = []

                from harness.llm.parsing import ParseReport

                report = ParseReport(
                    requested_count=cfg["num_mutants"],
                    accepted_count=0,
                    rejected_count=cfg["num_mutants"],
                    rejections=[],
                )

            log_duration("Parsing", parse_start)

            n_requested_mutants = cfg["num_mutants"]
            n_accepted_mutants = len(mutants)
            n_rejected_mutants = report.rejected_count
            acceptance_rate = (
                n_accepted_mutants / n_requested_mutants if n_requested_mutants > 0 else None
            )

            rejection_reason_counts = {}
            for rej in report.rejections:
                rejection_reason_counts[rej.reason] = (
                    rejection_reason_counts.get(rej.reason, 0) + 1
                )

            if parse_failed:
                rejection_reason_counts["invalid_json_response"] = (
                    rejection_reason_counts.get("invalid_json_response", 0) + 1
                )
            if missing_mutants_list:
                rejection_reason_counts["missing_mutants_list"] = (
                    rejection_reason_counts.get("missing_mutants_list", 0) + 1
                )

            log(f"Generated valid mutants: {len(mutants)}")
            log(f"Rejected candidates: {report.rejected_count}")
            for rej in report.rejections:
                log(f"rejection[{rej.index}]: {rej.reason}")

            for mutant in mutants:
                log(f"accepted mutant: {mutant.mutant_id}")

            generation_path = generation_dir(run_path)
            generation_path.mkdir(parents=True, exist_ok=True)
            parse_report_dict = parse_report_to_dict(report)

            t = time.time()
            save_generation_artifacts(
                output_dir=generation_path,
                system_prompt=built_prompt.system_prompt,
                user_prompt=built_prompt.user_prompt,
                raw_response=raw_text,
                mutants=mutants,
                parse_report=parse_report_dict,
            )
            log_duration("Save generation artifacts", t)

            t = time.time()
            save_rejected_mutant_artifacts(
                run_dir=run_path,
                subject=subject,
                target=target,
                original_code=target_code,
                rejections=parse_report_dict.get("rejections", []),
                raw_response_path=str(generation_path / "raw_response.txt"),
            )
            log_duration("Save rejected mutant artifacts", t)

            extra_metadata = build_extra_metadata(
                cfg=cfg,
                config_path=config_path,
                provider_type=provider_type,
                target=target,
                n_requested_mutants=n_requested_mutants,
                n_accepted_mutants=n_accepted_mutants,
                n_rejected_mutants=n_rejected_mutants,
                acceptance_rate=acceptance_rate,
                rejection_reason_counts=rejection_reason_counts,
                parse_failed=parse_failed,
                parse_error_message=parse_error_message,
            )

            if not mutants:
                run_status = "failure" if parse_failed else "no_valid_mutants"
                failure_reason = (
                    "invalid_json_response"
                    if parse_failed
                    else "missing_mutants_list"
                    if missing_mutants_list
                    else None
                )
                failure_message = parse_error_message if parse_failed or missing_mutants_list else None

                write_run_manifest(
                    run_dir=run_dir,
                    subject=subject,
                    target=target,
                    mutants=[],
                    run_mode=cfg.get("run_mode", "overwrite"),
                    workdir_base=workdir_base,
                    extra_metadata=extra_metadata,
                    status=run_status,
                    failure_reason=failure_reason,
                    failure_message=failure_message,
                    started_at_utc=started_at_utc,
                    completed_at_utc=datetime.now(timezone.utc).isoformat(),
                )

                t = time.time()
                write_empty_results_csv(run_dir)
                write_empty_summary_json(
                    run_dir,
                    run_status=run_status,
                    failure_reason=failure_reason,
                    failure_message=failure_message,
                )
                log_duration("Write empty run artifacts", t)

                if parse_failed:
                    (generation_path / "parse_error.txt").write_text(
                        parse_error_message or "unknown parse error",
                        encoding="utf-8",
                    )

                maybe_rebuild_index(cfg)

                log(f"Run finished with status: {run_status}")
                log(f"Generation artifacts saved to: {generation_path}")
                log(f"Empty run artifacts stored in: {run_dir}")
                log_duration("Total run", total_start)
                return

            write_run_manifest(
                run_dir=run_dir,
                subject=subject,
                target=target,
                mutants=mutants,
                run_mode=cfg.get("run_mode", "overwrite"),
                workdir_base=workdir_base,
                extra_metadata=extra_metadata,
                status="generated" if pipeline_mode == PIPELINE_MODE_GENERATE_ONLY else "running",
                started_at_utc=started_at_utc,
                completed_at_utc=datetime.now(timezone.utc).isoformat()
                if pipeline_mode == PIPELINE_MODE_GENERATE_ONLY
                else None,
            )

            if pipeline_mode == PIPELINE_MODE_GENERATE_ONLY:
                maybe_rebuild_index(cfg)
                log("Generation-only mode complete.")
                log(f"Generation artifacts: {generation_path}")
                log(f"Run metadata stored in: {run_dir}")
                log_duration("Total run", total_start)
                return

        else:
            prepare_execution_only_run_dir(run_path, cfg.get("run_mode", "overwrite"))

            # Tee stdout+stderr to run.log (append — generation may have already written it)
            _log_file = (run_path / "run.log").open("a", encoding="utf-8")
            sys.stdout = TeeStream(sys.__stdout__, _log_file)
            sys.stderr = TeeStream(sys.__stderr__, _log_file)
            log(f"Log file: {run_path / 'run.log'}")

            subject, target, extra_metadata = load_subject_target_from_manifest(run_dir)
            cfg = ensure_execute_only_config(cfg, subject, target)
            extra_metadata = dict(extra_metadata or {})
            if cfg.get("batch_id") is not None:
                extra_metadata["batch_id"] = cfg.get("batch_id")
            if cfg.get("run_group_id") is not None:
                extra_metadata["run_group_id"] = cfg.get("run_group_id")
            if cfg.get("run_index_for_target") is not None:
                extra_metadata["run_index_for_target"] = cfg.get("run_index_for_target")
            if cfg.get("runs_per_target") is not None:
                extra_metadata["runs_per_target"] = cfg.get("runs_per_target")
            if cfg.get("mutant_workers") is not None:
                extra_metadata["mutant_workers"] = cfg.get("mutant_workers")
            extra_metadata["missing_target_tests_policy"] = cfg.get("missing_target_tests_policy")
            save_run_config(run_path, cfg)

            t = time.time()
            adapter = build_adapter(subject.dataset)
            runner = MutationRunner(adapter)
            log_duration("Adapter/runner init", t)

            generation_path = generation_dir(run_path)
            t = time.time()
            mutants, mutant_load_info = load_generation_mutants_with_recovery(generation_path)
            log_duration("Load generated mutants", t)
            extra_metadata.update(mutant_load_info)

            if not mutants:
                raise ValueError(
                    f"No generated mutants found for execute_only mode in {generation_path}"
                )

            log(
                f"Loaded {len(mutants)} generated mutants from "
                f"{generation_path / 'parsed_mutants.json'}"
            )
            if mutant_load_info.get("parsed_mutants_integrity") != "ok":
                log(
                    "[execute_only] parsed_mutants.json recovery engaged: "
                    f"integrity={mutant_load_info.get('parsed_mutants_integrity')} "
                    f"recovered={mutant_load_info.get('recovered_mutant_count', 0)}"
                )

        t = time.time()
        log(f"Checking out subject into {base_snapshot_dir}")
        adapter.checkout_subject(subject, base_snapshot_dir)
        log_duration("Checkout", t)

        exec_start = time.time()
        log("[execution] mutation execution start")
        execution_result = runner.run(
            subject=subject,
            target=target,
            mutants=mutants,
            run_dir=run_path,
            workdir_base=workdir_base,
            base_snapshot_dir=base_snapshot_dir,
            run_mode=cfg.get("run_mode", "overwrite"),
            extra_metadata=extra_metadata,
            cleanup_tmp=cfg.get("cleanup_tmp", True),
            validate_after_run=cfg.get("validate_after_run", True),
            rebuild_index=cfg.get("rebuild_index", True),
            prepare_run_dir_on_start=False,
            mutant_workers=cfg.get("mutant_workers", 1),
            target_tests_csv_path=resolve_run_target_tests_csv(cfg),
            missing_target_tests_policy=cfg.get("missing_target_tests_policy", "fail"),
        )
        log_duration("[execution] mutation execution", exec_start)
        execution_result = execution_result or {}
        extra_metadata.update(execution_result.get("extra_metadata_updates") or {})

        final_run_status = execution_result.get("run_status", "ok")
        final_failure_reason = execution_result.get("failure_reason")
        final_failure_message = execution_result.get("failure_message")

        if final_run_status == "no_coverage":
            t = time.time()
            write_empty_results_csv(run_dir)
            write_empty_summary_json(
                run_dir,
                run_status=final_run_status,
                failure_reason=final_failure_reason,
                failure_message=final_failure_message,
            )
            log_duration("Write no-coverage run artifacts", t)

        t = time.time()
        write_run_manifest(
            run_dir=run_dir,
            subject=subject,
            target=target,
            mutants=mutants,
            run_mode=cfg.get("run_mode", "overwrite"),
            workdir_base=workdir_base,
            extra_metadata=extra_metadata,
            status=final_run_status,
            failure_reason=final_failure_reason,
            failure_message=final_failure_message,
            started_at_utc=started_at_utc,
            completed_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        log_duration("Finalize run manifest", t)

        maybe_build_kill_matrix(cfg)
        if final_run_status == "no_coverage":
            maybe_rebuild_index(cfg)

        log("Done.")
        log(f"Results stored in: {run_dir}")
        log_duration("Total run", total_start)
    except Exception as exc:
        run_status, failure_reason, failure_message = classify_exception(exc)
        log(f"Run failed with status={run_status} reason={failure_reason}: {failure_message}")

        if cfg and run_dir and workdir_base:
            ensure_failed_run_artifacts(
                cfg=cfg,
                run_dir=run_dir,
                workdir_base=workdir_base,
                run_status=run_status,
                failure_reason=failure_reason,
                failure_message=failure_message,
                subject=subject,
                target=target,
                extra_metadata=extra_metadata,
                started_at_utc=started_at_utc,
            )
            maybe_build_kill_matrix(cfg)
            maybe_rebuild_index(cfg)

        raise
    finally:
        if cfg.get("cleanup_tmp", True) and base_snapshot_dir:
            base_snapshot_path = Path(base_snapshot_dir)
            if base_snapshot_path.exists():
                t = time.time()
                cleanup_paths([base_snapshot_path], print_to_stdout=True)
                log_duration("Final cleanup tmp paths", t)

        # Restore stdout/stderr and close log file
        if isinstance(sys.stdout, TeeStream):
            sys.stdout = sys.__stdout__
        if isinstance(sys.stderr, TeeStream):
            sys.stderr = sys.__stderr__
        if _log_file is not None:
            try:
                _log_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
