from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MULL_SCRIPTS = REPO_ROOT / "scripts/manybugs/mull"
sys.path.insert(0, str(MULL_SCRIPTS))

from run_mull_catalog import build_preflight_plan, run_mull_catalog  # noqa: E402
from run_mull_subject import (  # noqa: E402
    binary_relpath_for_target, direct_wrapper_script, run_mull_subject,
)
from prepare_mull_checkout import (  # noqa: E402
    CONFIGURE_ARGS, DEFAULT_MAKE_JOBS, PRE_CONFIGURE_COMMANDS,
    apply_manybugs_fix_diffs,
)
from prepare_execution_plan import build_execution_plan, render_shell  # noqa: E402


class MullCatalogPreflightTests(unittest.TestCase):
    def test_all_pilots_have_oracles_and_validated_adapters_are_ready(self) -> None:
        plan = build_preflight_plan(
            REPO_ROOT / "harness/datasets/catalogs/manybugs_all_pilots.json"
        )

        self.assertEqual(31, plan["n_targets"])
        self.assertEqual(17, plan["n_ready_targets"])
        self.assertTrue(all(row["oracle_tests"] for row in plan["targets"]))
        projects = {row["project"]: row for row in plan["projects"]}
        self.assertTrue(projects["gzip"]["ready"])
        self.assertTrue(projects["libtiff"]["ready"])
        self.assertEqual(["runtime_baseline_incompatible"], projects["lighttpd"]["blockers"])
        self.assertEqual(["clang9_baseline_incompatible"], projects["gmp"]["blockers"])
        self.assertEqual(["unvalidated_execution_adapter"], projects["python"]["blockers"])

    def test_unimplemented_project_fails_before_checkout(self) -> None:
        with self.assertRaisesRegex(ValueError, "not implemented for project 'unknown'"):
            run_mull_subject(
                subject_id="unknown-example", target_id="target", target_file="file.c",
                start_line=1, end_line=2, catalog_file=None, mutators=[], timeout_ms=1,
                report_dir=Path("/tmp/unused-mull-preflight-test"),
            )

    def test_libtiff_binary_depends_on_target_file(self) -> None:
        self.assertEqual(
            "libtiff/.libs/libtiff.so",
            binary_relpath_for_target("libtiff", "libtiff/tif_dirread.c"),
        )
        self.assertEqual(
            "tools/.libs/tiffsplit",
            binary_relpath_for_target("libtiff", "tools/tiffsplit.c"),
        )
        self.assertEqual(
            "tools/.libs/tiffcrop",
            binary_relpath_for_target("libtiff", "tools/tiffcrop.c"),
        )

    def test_lighttpd_profile_uses_real_binary_and_perl_wrapper(self) -> None:
        self.assertEqual(
            "src/lighttpd", binary_relpath_for_target("lighttpd", "src/request.c")
        )
        wrapper = direct_wrapper_script("lighttpd", "mod-rewrite.t")
        self.assertIn("cd /experiment/src/tests", wrapper)
        self.assertIn("perl ./mod-rewrite.t", wrapper)
        self.assertIn("killall -9 lighttpd php-cgi", wrapper)

    def test_gmp_profile_uses_library_and_direct_test_protocol(self) -> None:
        self.assertEqual(
            ".libs/libgmp.a", binary_relpath_for_target("gmp", "mpz/gcdext.c")
        )
        wrapper = direct_wrapper_script("gmp", "tests/mpz/t-gcd")
        self.assertIn("cd /experiment/src/.", wrapper)
        self.assertIn("./tests/mpz/t-gcd", wrapper)
        self.assertNotIn("test.sh", wrapper)
        self.assertEqual(
            ("--disable-shared", "--enable-static"), CONFIGURE_ARGS["gmp"]
        )
        self.assertEqual("make distclean", PRE_CONFIGURE_COMMANDS["gmp"])

    def test_python_profile_uses_interpreter_and_scenario_test_protocol(self) -> None:
        self.assertEqual(
            "build/lib.linux-x86_64-3.3/select.cpython-33m.so",
            binary_relpath_for_target("python", "Modules/selectmodule.c"),
        )
        self.assertEqual("python", binary_relpath_for_target("python", "Python/peephole.c"))
        self.assertEqual("python", binary_relpath_for_target("python", "Modules/signalmodule.c"))
        self.assertEqual(
            "build/lib.linux-x86_64-3.3/math.cpython-33m.so",
            binary_relpath_for_target("python", "Modules/mathmodule.c"),
        )
        self.assertEqual(
            "build/lib.linux-x86_64-3.3/_json.cpython-33m.so",
            binary_relpath_for_target("python", "Modules/_json.c"),
        )
        self.assertEqual(
            "build/lib.linux-x86_64-3.3/zlib.cpython-33m.so",
            binary_relpath_for_target("python", "Modules/zlibmodule.c"),
        )
        self.assertEqual(1, DEFAULT_MAKE_JOBS["python"])
        python_wrapper = direct_wrapper_script("python", "n1")
        self.assertIn("bash /experiment/test.sh n1 dummy", python_wrapper)
        self.assertIn("PYTHONPATH=/experiment/src/build/lib.linux-x86_64-3.3", python_wrapper)

    def test_lighttpd_is_blocked_even_for_single_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime_baseline_incompatible"):
            run_mull_catalog(
                catalog_file=(
                    REPO_ROOT / "harness/datasets/catalogs/manybugs_lighttpd_pilot.json"
                ),
                run_name="must-not-run", output_root=Path("/tmp/must-not-run"),
                mutators=[], timeout_ms=1,
                only_target_id=(
                    "lighttpd_1806_1807_config_check_cond_nocache__line214_474"
                ),
            )

    def test_execution_plan_only_emits_validated_campaigns(self) -> None:
        plan = build_execution_plan(
            REPO_ROOT / "harness/datasets/catalogs/manybugs_all_pilots.json"
        )
        stages = {row["project"]: row for row in plan["stages"]}
        self.assertEqual("campaign_ready", stages["gzip"]["state"])
        self.assertEqual("campaign_ready", stages["libtiff"]["state"])
        self.assertEqual("blocked", stages["gmp"]["state"])
        self.assertEqual("smoke_required", stages["python"]["state"])
        self.assertEqual("blocked", stages["lighttpd"]["state"])

        shell = render_shell(plan)
        self.assertIn("--run-name mull_gzip_pilot_fixed_v1 --resume", shell)
        self.assertIn("--run-name mull_libtiff_pilot_fixed_v1 --resume", shell)
        self.assertIn("# SMOKE:", shell)
        self.assertNotIn("--run-name mull_lighttpd_pilot_fixed_v1", shell)
        executable_lines = [
            line for line in shell.splitlines()
            if line.startswith("python3 ")
        ]
        self.assertTrue(all("gmp" not in line for line in executable_lines))
        self.assertTrue(all("python_pilot" not in line for line in executable_lines))

    def test_applies_manybugs_fix_diffs_to_extracted_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            experiment = Path(tmpdir)
            target = experiment / "src/src/file.c"
            diff = experiment / "diffs/src/file.c-diff"
            target.parent.mkdir(parents=True)
            diff.parent.mkdir(parents=True)
            target.write_text("buggy\n", encoding="utf-8")
            diff.write_text(
                "--- file.c\n+++ file.c\n@@ -1 +1 @@\n-buggy\n+fixed\n",
                encoding="utf-8",
            )

            self.assertEqual(1, apply_manybugs_fix_diffs(experiment))
            self.assertEqual("fixed\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
