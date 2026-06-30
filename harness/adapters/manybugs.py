import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import docker

from harness.adapters.base import BenchmarkAdapter
from harness.models import Subject, Target, TestObservation, TestRunResult

CONTAINER_SOURCE_DIR = "/experiment/src"
CONTAINER_TEST_SCRIPT = "/experiment/test.sh"
CONTAINER_WORK_DIR = "/experiment"

IMAGE_REGISTRY = "prosyslab/manybugs"
# amd64 required: all prosyslab/manybugs images are linux/amd64
PLATFORM = "linux/amd64"

META_FILE = ".manybugs_meta.json"
CONTAINER_ID_FILE = ".container_id"
MUTATED_FILE_RECORD = ".manybugs_mutated_file"


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def _docker_client() -> docker.DockerClient:
    return docker.from_env()


def _container_create(client: docker.DockerClient, image: str, **kwargs) -> docker.models.containers.Container:
    """Create a container, stripping `platform` if the SDK doesn't support it."""
    try:
        return client.containers.create(image, platform=PLATFORM, **kwargs)
    except TypeError:
        return client.containers.create(image, **kwargs)


def _image_pull(client: docker.DockerClient, image: str) -> None:
    """Pull an image, stripping `platform` if the SDK doesn't support it."""
    try:
        client.images.pull(image, platform=PLATFORM)
    except TypeError:
        client.images.pull(image)


class ManyBugsAdapter(BenchmarkAdapter):

    BUILD_TIMEOUT = 300
    TEST_TIMEOUT = 120

    # ------------------------------------------------------------------ #
    # checkout_subject
    # ------------------------------------------------------------------ #

    def checkout_subject(self, subject: Subject, workdir: str) -> None:
        """Pull the prosyslab/manybugs Docker image for *subject*, extract the
        fixed source tree into *workdir*, and write adapter metadata.

        Container layout varies by project:
          - Some images (e.g. libtiff) ship the fixed source already in
            /experiment/src — no further action needed.
          - Others (e.g. gzip, lighttpd) ship the *buggy* source in
            /experiment/src and keep per-file diffs in /experiment/diffs/.
            For these we extract /experiment/src and then apply every
            /experiment/diffs/*.diff patch to produce the fixed tree.
        """
        scenario = subject.subject_id
        image_name = f"{IMAGE_REGISTRY}:{scenario}"
        workdir_path = Path(workdir).resolve()

        if workdir_path.exists():
            shutil.rmtree(workdir_path)
        workdir_path.mkdir(parents=True)

        log(f"[checkout] pulling image {image_name}")
        client = _docker_client()
        _image_pull(client, image_name)

        log(f"[checkout] extracting source from {image_name}")
        container = _container_create(client, image_name)
        try:
            _docker_cp_out(container.id, CONTAINER_SOURCE_DIR, str(workdir_path))
            _apply_diffs_if_needed(container.id, workdir_path)
        finally:
            container.remove(force=True)

        # Fix permissions on files extracted from Docker (may be root-owned)
        subprocess.run(
            ["chmod", "-R", "u+rwX", str(workdir_path)],
            capture_output=True,
        )

        meta = {
            "image": image_name,
            "scenario": scenario,
            "container_source_dir": CONTAINER_SOURCE_DIR,
            "container_work_dir": CONTAINER_WORK_DIR,
            "container_test_script": CONTAINER_TEST_SCRIPT,
        }
        (workdir_path / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log(f"[checkout] done — source at {workdir_path}")

    # ------------------------------------------------------------------ #
    # build
    # ------------------------------------------------------------------ #

    def build(self, workdir: str) -> tuple[bool, str]:
        """Create a fresh Docker container from the image, bring source to the
        fixed state, overwrite the target file with the mutant, then compile.

        For images where /experiment/src already contains the fixed source
        (libtiff-style) no diff step is needed.  For images where the source
        is buggy (gzip/lighttpd-style), we apply the diffs inside the container
        first so that every source file is at the fixed revision before the
        mutant is inserted.
        """
        log("[build] start")
        workdir_path = Path(workdir)
        meta = _read_meta(workdir_path)
        image = meta["image"]
        src_dir = meta["container_source_dir"]

        client = _docker_client()
        log(f"[build] creating container from {image}")
        container = _container_create(
            client, image,
            command="tail -f /dev/null",
            detach=True,
        )
        container.start()
        container_id = container.id

        t = time.time()
        try:
            # Step 1: bring source to fixed state inside the container
            fix_ok, fix_log = _apply_diffs_in_container(container_id, src_dir)
            if not fix_ok:
                _stop_and_remove(client, container_id)
                log(f"[build] could not apply fix diffs: {fix_log}")
                return False, fix_log

            # Step 2: overwrite target file with the mutant
            mutated_rel = _read_mutated_file(workdir_path)
            if mutated_rel:
                host_file = workdir_path / mutated_rel
                container_file = f"{src_dir}/{mutated_rel}"
                log(f"[build] inserting mutant: {mutated_rel}")
                _docker_cp_file(container_id, str(host_file), container_file)
                subprocess.run(
                    ["docker", "exec", container_id, "touch", container_file],
                    capture_output=True, text=True,
                )

            # Step 3: compile
            log("[build] running make")
            make_result = subprocess.run(
                ["docker", "exec", "-w", src_dir, container_id, "make", "-j2"],
                capture_output=True, text=True, timeout=self.BUILD_TIMEOUT,
            )
            build_log = make_result.stdout + make_result.stderr
            success = make_result.returncode == 0
        except Exception as exc:
            _stop_and_remove(client, container_id)
            log(f"[build] exception: {exc}")
            return False, str(exc)

        log(f"[build] finished in {time.time() - t:.2f}s — {'OK' if success else 'FAIL'}")

        if success:
            (workdir_path / CONTAINER_ID_FILE).write_text(container_id, encoding="utf-8")
        else:
            _stop_and_remove(client, container_id)

        return success, build_log

    # ------------------------------------------------------------------ #
    # test (baseline — no per-test detail)
    # ------------------------------------------------------------------ #

    def test(self, workdir: str, eligible_tests: list[str] | None = None) -> tuple[bool, str]:
        """Run baseline tests to verify the subject is in a valid state.

        If *eligible_tests* is provided, only those test IDs are run — this
        avoids false BASELINE_FAIL when unrelated positive tests are flaky in
        the current environment.  Otherwise all positive (pN) tests are run.
        """
        log("[test] baseline start")
        workdir_path = Path(workdir)
        meta = _read_meta(workdir_path)
        container_id = _read_container_id(workdir_path)
        if container_id is None:
            return False, "No container ID found; build may not have been called."

        client = _docker_client()
        container = client.containers.get(container_id)
        test_script = meta["container_test_script"]
        work_dir = meta["container_work_dir"]

        if eligible_tests:
            test_ids = eligible_tests
            log(f"[test] running {len(test_ids)} eligible target test(s): {test_ids}")
        else:
            test_ids = _discover_test_ids(container, test_script, kinds=("p",))
            log(f"[test] running all {len(test_ids)} positive test(s)")

        logs: list[str] = []
        all_pass = True
        t = time.time()

        for test_id in test_ids:
            passed, out = _run_single_test(
                container, test_script, test_id, work_dir, self.TEST_TIMEOUT
            )
            logs.append(f"[{test_id}] {'PASS' if passed else 'FAIL'}\n{out}")
            if not passed:
                all_pass = False

        log(
            f"[test] baseline finished in {time.time() - t:.2f}s"
            f" — {'PASS' if all_pass else 'FAIL'}"
        )
        return all_pass, "\n\n".join(logs)

    # ------------------------------------------------------------------ #
    # test_target
    # ------------------------------------------------------------------ #

    def test_target(self, workdir: str, eligible_tests: list[str]) -> TestRunResult:
        """Run only *eligible_tests* inside the existing container and return
        structured per-test observations.  Stops and removes the container
        when done.
        """
        log("[test_target] start")
        workdir_path = Path(workdir)
        meta = _read_meta(workdir_path)
        container_id = _read_container_id(workdir_path)
        work_dir = meta["container_work_dir"]
        test_script = meta["container_test_script"]

        if container_id is None:
            msg = "No container ID — build may have failed or was not called."
            return _all_not_run(eligible_tests, "NO_CONTAINER", msg)

        client = _docker_client()
        try:
            container = client.containers.get(container_id)
        except docker.errors.NotFound:
            msg = f"Container {container_id[:12]} not found."
            return _all_not_run(eligible_tests, "NO_CONTAINER", msg)

        observations: list[TestObservation] = []
        combined_log: list[str] = []
        t = time.time()

        for index, test_name in enumerate(eligible_tests, 1):
            test_start = time.time()
            passed, out = _run_single_test(
                container, test_script, test_name, work_dir, self.TEST_TIMEOUT
            )
            duration_ms = int((time.time() - test_start) * 1000)
            outcome = "PASS" if passed else "FAIL"
            observations.append(
                TestObservation(
                    test_name=test_name,
                    eligible=True,
                    executed=True,
                    outcome=outcome,
                    duration_ms=duration_ms,
                    failure_type="TEST_FAILURE" if not passed else None,
                    execution_index=index,
                )
            )
            combined_log.append(f"[{test_name}] {outcome}\n{out}")

        log(f"[test_target] finished {len(eligible_tests)} test(s) in {time.time() - t:.2f}s")

        _stop_and_remove(client, container_id)
        (workdir_path / CONTAINER_ID_FILE).unlink(missing_ok=True)

        suite_ok = all(obs.outcome == "PASS" for obs in observations)
        return TestRunResult(
            success=suite_ok,
            log="\n\n".join(combined_log),
            observations=observations,
        )

    # ------------------------------------------------------------------ #
    # apply_mutant
    # ------------------------------------------------------------------ #

    def apply_mutant(self, workdir: str, target: Target, mutant_code: str) -> None:
        """Apply a line-level mutant to the local source file and record which
        file was changed so build() can sync only that file into the container.
        """
        file_path = Path(workdir) / target.file_path
        original = file_path.read_text(encoding="utf-8")
        lines = original.splitlines()

        new_lines = (
            lines[: target.start_line - 1]
            + mutant_code.splitlines()
            + lines[target.end_line :]
        )
        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        (Path(workdir) / MUTATED_FILE_RECORD).write_text(
            target.file_path, encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # reset_subject
    # ------------------------------------------------------------------ #

    def reset_subject(self, workdir: str) -> None:
        """Clean up any lingering Docker container for *workdir*.

        The MutationRunner resets local source via copytree, so only the
        Docker container needs cleanup here.
        """
        workdir_path = Path(workdir)
        container_id = _read_container_id(workdir_path)
        if container_id is None:
            return
        try:
            client = _docker_client()
            _stop_and_remove(client, container_id)
        except Exception as exc:
            log(f"[reset] warning: could not remove container: {exc}")
        finally:
            (workdir_path / CONTAINER_ID_FILE).unlink(missing_ok=True)


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _read_meta(workdir_path: Path) -> dict:
    meta_file = workdir_path / META_FILE
    if not meta_file.exists():
        raise FileNotFoundError(
            f"ManyBugs metadata not found at {meta_file}. "
            "Was checkout_subject() called for this workdir?"
        )
    return json.loads(meta_file.read_text(encoding="utf-8"))


def _read_container_id(workdir_path: Path) -> str | None:
    cid_file = workdir_path / CONTAINER_ID_FILE
    if not cid_file.exists():
        return None
    return cid_file.read_text(encoding="utf-8").strip() or None


def _stop_and_remove(client: docker.DockerClient, container_id: str) -> None:
    try:
        container = client.containers.get(container_id)
        container.stop(timeout=5)
        container.remove(force=True)
        log(f"[docker] removed container {container_id[:12]}")
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        log(f"[docker] warning removing {container_id[:12]}: {exc}")


def _read_mutated_file(workdir_path: Path) -> str | None:
    record = workdir_path / MUTATED_FILE_RECORD
    if not record.exists():
        return None
    return record.read_text(encoding="utf-8").strip() or None


def _docker_cp_file(container_id: str, host_file: str, container_file: str) -> None:
    """Copy a single host file into the container at the given path."""
    result = subprocess.run(
        ["docker", "cp", host_file, f"{container_id}:{container_file}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker cp file failed (rc={result.returncode}): {result.stderr}"
        )


def _apply_diffs_in_container(container_id: str, src_dir: str) -> tuple[bool, str]:
    """Apply /experiment/diffs/**/*-diff patches directly inside a running container.

    Used by build() to bring the container source from the buggy to the fixed
    state before inserting the mutant.  For libtiff-style images (no diffs
    directory) this is a no-op that returns (True, "").
    """
    result = subprocess.run(
        ["docker", "exec", container_id,
         "find", "/experiment/diffs", "-type", "f", "-name", "*-diff"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return True, ""  # no diffs — src is already fixed

    diff_paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    log(f"[build] applying {len(diff_paths)} fix diff(s) inside container")

    for diff_path in diff_paths:
        rel = diff_path.removeprefix("/experiment/diffs/")
        target_file = rel.removesuffix("-diff")
        patch_result = subprocess.run(
            ["docker", "exec", "-w", src_dir, container_id,
             "patch", target_file, "-i", diff_path, "--forward", "-s"],
            capture_output=True, text=True,
        )
        if patch_result.returncode not in (0, 1):  # 1 = already applied
            return False, f"patch failed for {target_file}: {patch_result.stderr}"
        # touch so make sees this file as newer than the pre-built binary
        subprocess.run(
            ["docker", "exec", container_id, "touch", f"{src_dir}/{target_file}"],
            capture_output=True, text=True,
        )

    return True, ""


def _apply_diffs_if_needed(container_id: str, workdir_path: Path) -> None:
    """Apply /experiment/diffs/**/*.diff patches to the extracted source tree.

    For gzip/lighttpd-style images /experiment/src contains the *buggy* source
    and the fix is recorded as standard diff hunks under /experiment/diffs/.
    The diff filename without the trailing '-diff' is the relative path to the
    target source file within the source tree (e.g. 'src/mod_accesslog.c-diff'
    patches 'src/mod_accesslog.c').

    For libtiff-style images /experiment/src already contains the fixed source
    and there is no diffs directory — this function is then a no-op.
    """
    result = subprocess.run(
        ["docker", "exec", container_id, "find", "/experiment/diffs", "-type", "f", "-name", "*-diff"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return  # no diffs — src is already fixed

    diff_paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    log(f"[checkout] applying {len(diff_paths)} diff(s) to produce fixed source")

    for diff_container_path in diff_paths:
        rel = diff_container_path.removeprefix("/experiment/diffs/")
        target_rel = rel.removesuffix("-diff")
        target_host = workdir_path / target_rel

        local_diff = workdir_path / f".manybugs_patch_{rel.replace('/', '_')}"
        cp = subprocess.run(
            ["docker", "cp", f"{container_id}:{diff_container_path}", str(local_diff)],
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            log(f"[checkout] warning: could not copy {diff_container_path}: {cp.stderr}")
            continue

        patch_result = subprocess.run(
            ["patch", str(target_host), "-i", str(local_diff), "--forward", "-s"],
            capture_output=True, text=True,
        )
        local_diff.unlink(missing_ok=True)
        if patch_result.returncode not in (0, 1):
            log(f"[checkout] warning: patch {rel} failed (rc={patch_result.returncode}): {patch_result.stderr}")
        else:
            log(f"[checkout] applied {rel}")


def _docker_cp_out(container_id: str, src_path: str, dest_dir: str) -> None:
    result = subprocess.run(
        ["docker", "cp", f"{container_id}:{src_path}/.", dest_dir],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker cp out failed (rc={result.returncode}): {result.stderr}"
        )


def _docker_cp_in(container_id: str, src_dir: str, dest_path: str) -> None:
    result = subprocess.run(
        ["docker", "cp", f"{src_dir}/.", f"{container_id}:{dest_path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker cp in failed (rc={result.returncode}): {result.stderr}"
        )


def _discover_test_ids(container, test_script: str, kinds: tuple[str, ...] = ("p", "n")) -> list[str]:
    """Parse the test.sh inside the container to extract available test IDs.

    ManyBugs test scripts have entries like:
        p1) run_test 2 && exit 0 ;;
        n1) run_test 22 && exit 0 ;;
    """
    result = subprocess.run(
        ["docker", "exec", container.id, "cat", test_script],
        capture_output=True, text=True,
    )
    content = result.stdout
    pattern = r"^\s+([" + "".join(kinds) + r"]\d+)\)"
    return re.findall(pattern, content, re.MULTILINE)


def _run_single_test(
    container,
    test_script: str,
    test_id: str,
    work_dir: str,
    timeout: int,
) -> tuple[bool, str]:
    """Run one ManyBugs test inside *container* via subprocess docker exec
    so that the OS-level timeout can be enforced.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", "-w", work_dir, container.id,
             "bash", test_script, test_id, "dummy"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"


def _all_not_run(
    eligible_tests: list[str],
    failure_type: str,
    message: str,
) -> TestRunResult:
    observations = [
        TestObservation(
            test_name=name,
            eligible=True,
            executed=False,
            outcome="NOT_RUN",
            failure_type=failure_type,
            message=message,
            execution_index=i,
        )
        for i, name in enumerate(eligible_tests, 1)
    ]
    return TestRunResult(success=False, log=message, observations=observations)
