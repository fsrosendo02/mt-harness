import json
import re
import sys
from datetime import datetime
from pathlib import Path
import subprocess


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def slug(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w\-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def next_batch_id(batches_dir: Path) -> str:
    batches_dir.mkdir(parents=True, exist_ok=True)

    max_n = 0
    for path in batches_dir.glob("batch*.json"):
        m = re.match(r"batch(\d+)\.json$", path.name)
        if m:
            max_n = max(max_n, int(m.group(1)))

    return f"batch{max_n + 1:02d}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run_batch.py <config_file>")
        sys.exit(1)

    base_config_path = sys.argv[1]

    base_cfg = load_json(base_config_path)
    
    catalog_path = base_cfg["catalog_file"]
    catalog = load_json(catalog_path)

    batches_dir = Path("harness/experiments/batches")
    batch_id = next_batch_id(batches_dir)

    batch_manifest = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "catalog_file": catalog_path,
        "base_config_file": base_config_path,
        "model": base_cfg["model"],
        "prompt_file": base_cfg["prompt_file"],
        "num_mutants": base_cfg["num_mutants"],
        "timeout": base_cfg["timeout"],
        "temperature": base_cfg.get("temperature", 0.0),
        "targets": [entry["target_id"] for entry in catalog],
        "runs": [],
    }

    for entry in catalog:
        target_id = entry["target_id"]
        subject = entry["subject"]
        function = entry["function"]

        run_name = f"{batch_id}__{slug(subject)}__{slug(function)}"

        cfg = dict(base_cfg)
        cfg["target_id"] = target_id
        cfg["batch_id"] = batch_id
        cfg["run_name"] = run_name

        tmp_config = Path("configs/tmp_batch_config.json")
        tmp_config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        print(f"\n=== Running {target_id} -> {run_name} ===")
        
        result = subprocess.run(
            ["python3", "run_llm.py", str(tmp_config)],
            check=False
        )

        batch_manifest["runs"].append({
            "target_id": target_id,
            "subject": subject,
            "function": function,
            "run_name": run_name,
            "run_dir": f"harness/runs/{run_name}",
            "return_code": result.returncode,
            "status": "ok" if result.returncode == 0 else "failed",
        })

    save_json(batches_dir / f"{batch_id}.json", batch_manifest)

    print("\nBatch complete.")
    print(f"Batch manifest: {batches_dir / f'{batch_id}.json'}")


if __name__ == "__main__":
    main()