import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from harness.adapters.defects4j import Defects4JAdapter
from harness.experiments.metadata import build_experiment_metadata
from harness.generators.llm import LLMMutantGenerator
from harness.llm.io import save_generation_artifacts
from harness.llm.parsing import parse_report_to_dict
from harness.llm.prompt_builder import PromptBuilder
from harness.llm.providers.gemini_provider import GeminiProvider
from harness.llm.providers.gpt4o_provider import GPT4oProvider
from harness.llm.providers.ollama_provider import OllamaProvider
from harness.models import Subject
from harness.models import Target
from harness.runners.mutation_runner import MutationRunner
from harness.storage.cleanup import cleanup_paths
from harness.storage.artifacts import save_rejected_mutant_artifacts
from harness.storage.layout import execution_results_path, execution_summary_path, generation_dir, manifest_path
from harness.storage.run_state import prepare_run_dir, write_run_manifest
from harness.targets.resolver import resolve_target


BASE_REQUIRED_FIELDS = [
    "dataset",
    "subject",
    "version",
    "model",
    "num_mutants",
    "timeout",
    "run_name",
    "prompt_file",
]


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def log_duration(label: str, start: float) -> None:
    log(f"{label} finished in {time.time() - start:.2f}s")


def extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    missing = [field for field in BASE_REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    has_catalog_target = "target_id" in data
    has_manual_target = "file" in data and "function" in data

    if not has_catalog_target and not has_manual_target:
        raise ValueError(
            "Config must define either:\n"
            "  - target_id + catalog_file\n"
            "or\n"
            "  - file + function"
        )

    if has_catalog_target and "catalog_file" not in data:
        raise ValueError("Config uses target_id but is missing catalog_file")

    return data


def build_adapter(dataset: str):
    if dataset == "defects4j":
        return Defects4JAdapter()

    raise ValueError(f"Unsupported dataset: {dataset}")


def write_empty_results_csv(run_dir: str):
    csv_path = execution_results_path(run_dir)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "dataset",
        "subject_id",
        "function_name",
        "mutant_id",
        "build_status",
        "test_status",
        "killed",
        "executable",
        "log_path",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
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

    return "failure", exc.__class__.__name__, message


def build_target_stub(cfg: dict) -> Target:
    return Target(
        file_path=cfg.get("file", ""),
        function_name=cfg.get("function", ""),
        start_line=cfg.get("start_line"),
        end_line=cfg.get("end_line"),
        language=cfg.get("language", "java"),
        target_id=cfg.get("target_id"),
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

    write_run_manifest(
        run_dir=run_dir,
        subject=resolved_subject,
        target=resolved_target,
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

    try:
        t = time.time()
        cfg = load_config(config_path)
        log_duration("Config load", t)

        t = time.time()
        adapter = build_adapter(cfg["dataset"])
        log_duration("Adapter build", t)

        t = time.time()
        subject = Subject(
            dataset=cfg["dataset"],
            subject_id=cfg["subject"],
            language=cfg.get("language", "java"),
            version=cfg["version"],
        )
        log_duration("Subject build", t)

        run_dir = f"harness/runs/{cfg['run_name']}"
        base_snapshot_dir = f"tmp/base_{cfg['run_name']}"
        workdir_base = f"tmp/{cfg['run_name']}"
        run_path = prepare_run_dir(run_dir, mode=cfg.get("run_mode", "overwrite"))

        t = time.time()
        provider_type = cfg.get("provider", "ollama")

        if provider_type == "ollama":
            provider = OllamaProvider(cfg["model"], timeout_seconds=cfg["timeout"])
        elif provider_type == "gpt4o":
            provider = GPT4oProvider(cfg["model"], timeout_seconds=cfg["timeout"])
        elif provider_type == "gemini":
            provider = GeminiProvider(cfg["model"], timeout_seconds=cfg["timeout"])
        else:
            raise ValueError(f"Unknown provider: {provider_type}")

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

        parse_start = time.time()
        try:
            mutants, report = generator.parser.parse_with_report(
                raw_text=raw_text,
                requested_count=cfg["num_mutants"],
                original_target_code=target_code,
                language=target.language,
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

        log(f"Generated valid mutants: {len(mutants)}")
        log(f"Rejected candidates: {report.rejected_count}")
        for rej in report.rejections:
            log(f"rejection[{rej.index}]: {rej.reason}")

        for m in mutants:
            log(f"accepted mutant: {m.mutant_id}")

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

        extra_metadata = build_experiment_metadata(
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
            n_accepted_mutants=n_accepted_mutants,
            n_rejected_mutants=n_rejected_mutants,
            acceptance_rate=acceptance_rate,
            rej_duplicate_mutant=rejection_reason_counts.get("duplicate_mutant", 0),
            rej_unchanged_mutant=rejection_reason_counts.get("unchanged_mutant", 0),
            rej_non_executable_change=rejection_reason_counts.get(
                "non_executable_change", 0
            ),
            rej_non_executable_structural_change=sum(
                count
                for reason, count in rejection_reason_counts.items()
                if str(reason).startswith("non_executable_structural_change")
            ),
            rej_precode_not_found=rejection_reason_counts.get("precode_not_found", 0),
            rej_ambiguous_precode_match=rejection_reason_counts.get(
                "ambiguous_precode_match", 0
            ),
            rej_invalid_json_response=rejection_reason_counts.get(
                "invalid_json_response", 0
            ),
            parse_failed=parse_failed,
            parse_error_message=parse_error_message,
            notes=cfg.get(
                "notes",
                f"Prompt file: {cfg['prompt_file']}; Config file: {config_path}",
            ),
        )

        if not mutants:
            run_status = "failure" if parse_failed else "no_valid_mutants"
            failure_reason = "invalid_json_response" if parse_failed else None
            failure_message = parse_error_message if parse_failed else None

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
                

            if cfg.get("rebuild_index", True):
                t = time.time()
                from harness.experiments.build_experiment_index import (
                    build_experiment_index,
                )

                build_experiment_index()
                log_duration("Rebuild experiment index", t)

            log(f"Run finished with status: {run_status}")
            log(f"Generation artifacts saved to: {generation_path}")
            log(f"Empty run artifacts stored in: {run_dir}")
            log_duration("Total run", total_start)
            return

        exec_start = time.time()
        log("[execution] mutation execution start")
        runner.run(
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
        )
        log_duration("[execution] mutation execution", exec_start)

        t = time.time()
        write_run_manifest(
            run_dir=run_dir,
            subject=subject,
            target=target,
            mutants=mutants,
            run_mode=cfg.get("run_mode", "overwrite"),
            workdir_base=workdir_base,
            extra_metadata=extra_metadata,
            status="ok",
            started_at_utc=started_at_utc,
            completed_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        log_duration("Finalize run manifest", t)

        t = time.time()
        save_generation_artifacts(
            output_dir=generation_path,
            system_prompt=built_prompt.system_prompt,
            user_prompt=built_prompt.user_prompt,
            raw_response=raw_text,
            mutants=mutants,
            parse_report=parse_report_dict,
        )
        log_duration("Final generation artifact save", t)

        if parse_failed:
            (generation_path / "parse_error.txt").write_text(
                parse_error_message or "unknown parse error",
                encoding="utf-8",
            )

        log("Done.")
        log(f"Generation artifacts: {generation_path}")
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

            if cfg.get("rebuild_index", True):
                t = time.time()
                from harness.experiments.build_experiment_index import (
                    build_experiment_index,
                )

                build_experiment_index()
                log_duration("Rebuild experiment index", t)

        raise
    finally:
        if cfg.get("cleanup_tmp", True) and base_snapshot_dir:
            base_snapshot_path = Path(base_snapshot_dir)
            if base_snapshot_path.exists():
                t = time.time()
                cleanup_paths([base_snapshot_path], print_to_stdout=True)
                log_duration("Final cleanup tmp paths", t)


if __name__ == "__main__":
    main()
