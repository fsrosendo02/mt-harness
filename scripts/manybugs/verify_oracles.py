"""
Verify ManyBugs oracle tests: confirm buggy→FAIL and fixed→PASS within Docker.

For each scenario:
  1. Start container (pre-built binary = buggy version)
  2. Run oracle tests WITHOUT building → expect FAIL
  3. Run `make` to compile fixed source
  4. Run oracle tests again → expect PASS

Usage:
    python scripts/manybugs/verify_oracles.py <scenario:n1,n2> [...]
    e.g.: python scripts/manybugs/verify_oracles.py gzip-2009-09-26-a1d3d4019d-f17cbd13a1:n1

Or read from a catalog JSON:
    python scripts/manybugs/verify_oracles.py --catalog harness/datasets/catalogs/manybugs_gzip_pilot.json
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
    """Apply diffs (if any) to the buggy source, then compile.

    For gzip/lighttpd-style images: /experiment/src/ contains the buggy source
    and /experiment/diffs/**/*.diff records the fix as standard diff hunks.
    We apply each diff to the corresponding source file, then make.

    For libtiff-style images: /experiment/src/ already contains the fixed
    source — no diffs directory exists, so we just make.
    """
    # Collect all *.diff files under /experiment/diffs/ recursively
    rc, out = run(["docker", "exec", cid, "find", "/experiment/diffs", "-type", "f", "-name", "*-diff"])
    diff_paths = [p.strip() for p in out.splitlines() if p.strip()] if rc == 0 else []

    for diff_path in diff_paths:
        # Derive target source file: strip /experiment/diffs/ prefix and -diff suffix
        rel = diff_path.removeprefix("/experiment/diffs/")  # e.g. "src/mod_accesslog.c-diff"
        target_file = rel.removesuffix("-diff")             # e.g. "src/mod_accesslog.c"
        rc, out = run(
            ["docker", "exec", "-w", SRC_DIR, cid,
             "patch", target_file, "-i", diff_path, "--forward", "-s"],
            timeout=30,
        )
        if rc not in (0, 1):  # 1 = already applied
            return False, f"patch failed for {target_file}: {out}"
        # touch so make sees the source as newer than the pre-built binary
        run(["docker", "exec", "-w", SRC_DIR, cid, "touch", target_file], timeout=10)

    rc, out = run(
        ["docker", "exec", "-w", SRC_DIR, cid, "make", "-j2"],
        timeout=BUILD_TIMEOUT,
    )
    return rc == 0, out


def verify_scenario(scenario, oracle_tests):
    image = f"{IMAGE_REGISTRY}:{scenario}"
    print(f"\n[{scenario}]")
    print(f"  oracle tests: {oracle_tests}")

    print(f"  pulling image...", flush=True)
    run(["docker", "pull", "--platform", PLATFORM, image], timeout=300)

    cid = start_container(image)
    results = {"scenario": scenario, "oracle_tests": oracle_tests, "buggy": {}, "fixed": {}, "ok": True}

    try:
        # Step 1: run oracle tests on pre-built (buggy) binary
        print("  [buggy] running oracle tests on pre-built binary...")
        buggy_ok = True
        for test_id in oracle_tests:
            passed, out = run_test(cid, test_id)
            status = "PASS" if passed else "FAIL"
            expected = "FAIL"
            check = "✓" if not passed else "✗ UNEXPECTED"
            print(f"    {test_id}: {status} (expected {expected}) {check}")
            results["buggy"][test_id] = {"passed": passed, "expected_pass": False, "ok": not passed}
            if passed:
                buggy_ok = False

        # Step 2: build fixed source
        print("  [build] compiling fixed source...")
        build_ok, build_log = build_fixed(cid)
        results["build_ok"] = build_ok
        if not build_ok:
            print(f"  [build] FAILED — cannot verify fixed")
            print(f"  [build] log tail: {build_log[-300:]}")
            results["ok"] = False
            return results

        # Step 3: run oracle tests on fixed binary
        print("  [fixed] running oracle tests on fixed binary...")
        fixed_ok = True
        for test_id in oracle_tests:
            passed, out = run_test(cid, test_id)
            status = "PASS" if passed else "FAIL"
            expected = "PASS"
            check = "✓" if passed else "✗ UNEXPECTED"
            print(f"    {test_id}: {status} (expected {expected}) {check}")
            results["fixed"][test_id] = {"passed": passed, "expected_pass": True, "ok": passed}
            if not passed:
                fixed_ok = False

        results["ok"] = buggy_ok and build_ok and fixed_ok
        verdict = "VERIFIED ✓" if results["ok"] else "FAILED ✗"
        print(f"  => {verdict}")

    finally:
        stop_container(cid)

    return results


def load_from_catalog(catalog_path):
    data = json.loads(Path(catalog_path).read_text())
    specs = []
    for entry in data["targets"]:
        scenario = entry["subject"]
        oracles = entry["metadata"].get("oracle_tests", ["n1"])
        specs.append((scenario, oracles))
    return specs


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    specs = []
    if args[0] == "--catalog":
        specs = load_from_catalog(args[1])
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
            all_results.append(r)
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results.append({"scenario": scenario, "ok": False, "error": str(e)})

    print("\n\n=== SUMMARY ===")
    passed = [r for r in all_results if r.get("ok")]
    failed = [r for r in all_results if not r.get("ok")]
    print(f"VERIFIED: {len(passed)}/{len(all_results)}")
    if failed:
        print("FAILED:")
        for r in failed:
            print(f"  {r['scenario']}: {r.get('error', 'oracle mismatch')}")


if __name__ == "__main__":
    main()
