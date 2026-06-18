"""
Verify ManyBugs oracle tests: confirm buggy→FAIL / fixed→PASS for every target.

For each scenario:
  1. Start container (pre-built binary = buggy version)
  2. Run oracle (nN) tests WITHOUT rebuilding → expect FAIL
  3. Apply diffs (if any) + make to get fixed binary
  4. Run oracle tests again → expect PASS

Writes "verified": true/false back to each catalog JSON.

Usage:
    # single scenario
    python scripts/manybugs/verify_oracles.py gzip-2009-08-16-3fe0caeada-39a362ae9d:n1,n2

    # one catalog
    python scripts/manybugs/verify_oracles.py --catalog harness/datasets/catalogs/manybugs_gzip_pilot.json

    # all pilot catalogs
    python scripts/manybugs/verify_oracles.py --all-pilots

    # dry-run (don't update catalogs)
    python scripts/manybugs/verify_oracles.py --all-pilots --dry-run
"""
import json
import subprocess
import sys
import time
from pathlib import Path

IMAGE_REGISTRY = "prosyslab/manybugs"
PLATFORM = "linux/amd64"
SRC_DIR = "/experiment/src"
TEST_SCRIPT = "/experiment/test.sh"
WORK_DIR = "/experiment"
BUILD_TIMEOUT = 300
TEST_TIMEOUT = 60

PILOT_CATALOGS = [
    "harness/datasets/catalogs/manybugs_libtiff_pilot.json",
    "harness/datasets/catalogs/manybugs_gzip_pilot.json",
    "harness/datasets/catalogs/manybugs_lighttpd_pilot.json",
]


def ts():
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


def run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def start_container(image):
    rc, out = run(["docker", "create", "--platform", PLATFORM, image, "tail", "-f", "/dev/null"])
    if rc != 0:
        raise RuntimeError(f"docker create failed: {out}")
    cid = out.strip()
    rc, out = run(["docker", "start", cid])
    if rc != 0:
        raise RuntimeError(f"docker start failed: {out}")
    return cid


def stop_container(cid):
    run(["docker", "stop", "-t", "3", cid])
    run(["docker", "rm", "-f", cid])


def run_test(cid, test_id):
    rc, out = run(
        ["docker", "exec", "-w", WORK_DIR, cid, "bash", TEST_SCRIPT, test_id, "dummy"],
        timeout=TEST_TIMEOUT,
    )
    return rc == 0, out


def build_fixed(cid):
    """Apply diffs (if any) then compile. Returns (success, log)."""
    rc, out = run(["docker", "exec", cid, "find", "/experiment/diffs", "-type", "f", "-name", "*-diff"])
    diff_paths = [p.strip() for p in out.splitlines() if p.strip()] if rc == 0 else []

    for diff_path in diff_paths:
        rel = diff_path.removeprefix("/experiment/diffs/")
        target_file = rel.removesuffix("-diff")
        rc, out = run(
            ["docker", "exec", "-w", SRC_DIR, cid,
             "patch", target_file, "-i", diff_path, "--forward", "-s"],
            timeout=30,
        )
        if rc not in (0, 1):
            return False, f"patch failed for {target_file}: {out}"
        run(["docker", "exec", "-w", SRC_DIR, cid, "touch", target_file], timeout=10)

    rc, out = run(
        ["docker", "exec", "-w", SRC_DIR, cid, "make", "-j2"],
        timeout=BUILD_TIMEOUT,
    )
    return rc == 0, out


def verify_scenario(scenario, oracle_tests):
    image = f"{IMAGE_REGISTRY}:{scenario}"
    print(f"\n[{ts()}] {scenario}")
    print(f"  oracle tests: {oracle_tests}")

    run(["docker", "pull", "--platform", PLATFORM, image], timeout=300)

    t0 = time.time()
    cid = start_container(image)
    result = {
        "scenario": scenario,
        "oracle_tests": oracle_tests,
        "buggy_ok": False,
        "build_ok": False,
        "fixed_ok": False,
        "verified": False,
        "failure_reason": None,
    }

    try:
        # Step 1: oracle tests on buggy (pre-built) binary — expect FAIL
        buggy_ok = True
        for test_id in oracle_tests:
            passed, _ = run_test(cid, test_id)
            mark = "✓" if not passed else "✗"
            print(f"  [buggy] {test_id}: {'PASS' if passed else 'FAIL'} (want FAIL) {mark}")
            if passed:
                buggy_ok = False
        result["buggy_ok"] = buggy_ok
        if not buggy_ok:
            result["failure_reason"] = "buggy binary passes oracle (test not discriminating)"

        # Step 2: build fixed source
        print(f"  [build] compiling fixed...", flush=True)
        build_ok, build_log = build_fixed(cid)
        result["build_ok"] = build_ok
        if not build_ok:
            short = build_log[-400:].strip().replace("\n", " | ")
            print(f"  [build] FAILED — {short}")
            result["failure_reason"] = f"build failed: {build_log[-200:].strip()}"
            return result

        print(f"  [build] OK")

        # Step 3: oracle tests on fixed binary — expect PASS
        fixed_ok = True
        for test_id in oracle_tests:
            passed, _ = run_test(cid, test_id)
            mark = "✓" if passed else "✗"
            print(f"  [fixed] {test_id}: {'PASS' if passed else 'FAIL'} (want PASS) {mark}")
            if not passed:
                fixed_ok = False
        result["fixed_ok"] = fixed_ok
        if not fixed_ok:
            result["failure_reason"] = "fixed binary fails oracle"

        result["verified"] = buggy_ok and build_ok and fixed_ok
        label = "VERIFIED ✓" if result["verified"] else "FAILED ✗"
        print(f"  => {label}  ({time.time()-t0:.1f}s)")

    finally:
        stop_container(cid)

    return result


def load_catalog(path):
    data = json.loads(Path(path).read_text())
    specs = []
    for entry in data["targets"]:
        scenario = entry["subject"]
        oracles = entry.get("metadata", {}).get("oracle_tests", ["n1"])
        specs.append((scenario, oracles))
    return specs


def update_catalog(path, results_by_scenario, dry_run=False):
    """Write verified field back into catalog JSON."""
    data = json.loads(Path(path).read_text())
    changed = 0
    for entry in data["targets"]:
        scenario = entry["subject"]
        if scenario in results_by_scenario:
            r = results_by_scenario[scenario]
            old = entry.get("verified")
            new = r["verified"]
            if old != new:
                entry["verified"] = new
                changed += 1
    if changed and not dry_run:
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return changed


def print_summary(all_results):
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    verified = [r for r in all_results if r.get("verified")]
    failed = [r for r in all_results if not r.get("verified")]
    print(f"VERIFIED: {len(verified)}/{len(all_results)}")
    print()
    if verified:
        print("✓ READY:")
        for r in verified:
            print(f"  {r['scenario']}")
    if failed:
        print()
        print("✗ BROKEN:")
        for r in failed:
            reason = r.get("failure_reason") or r.get("error", "unknown")
            print(f"  {r['scenario']}")
            print(f"    reason: {reason}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    catalog_paths = []
    specs = []

    if args[0] == "--all-pilots":
        catalog_paths = PILOT_CATALOGS
        for p in catalog_paths:
            specs.extend(load_catalog(p))
    elif args[0] == "--catalog":
        catalog_paths = [args[1]]
        specs = load_catalog(args[1])
    else:
        for arg in args:
            if ":" in arg:
                scenario, tests_str = arg.split(":", 1)
                specs.append((scenario, tests_str.split(",")))
            else:
                specs.append((arg, ["n1"]))

    all_results = []
    for scenario, oracles in specs:
        try:
            r = verify_scenario(scenario, oracles)
        except Exception as e:
            print(f"  ERROR: {e}")
            r = {"scenario": scenario, "verified": False, "error": str(e), "failure_reason": str(e)}
        all_results.append(r)

    results_by_scenario = {r["scenario"]: r for r in all_results}

    for path in catalog_paths:
        changed = update_catalog(path, results_by_scenario, dry_run=dry_run)
        tag = "(dry-run)" if dry_run else ""
        print(f"\nUpdated {path}: {changed} target(s) changed {tag}")

    print_summary(all_results)


if __name__ == "__main__":
    main()
