import os
import sys
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp_all as allmod
import ccupp_core as core


class _Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.prev_home = os.environ.get("CCUPP_HOME")
        os.environ["CCUPP_HOME"] = self.home
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        if self.prev_home is None:
            os.environ.pop("CCUPP_HOME", None)
        else:
            os.environ["CCUPP_HOME"] = self.prev_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def _snap(self, project_name, sid, u, t, c, ms):
        sd = os.path.join(self.root, project_name, ".ccupp", "sessions")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, sid + ".json"), "w") as f:
            json.dump({"tokens": t, "utterances": u, "cost_usd": c,
                       "api_ms": ms, "estimated": False}, f)


class TestAllProjectTotals(_Base):
    def test_aggregates_per_project_and_skips_empty(self):
        self._snap("-Users-me-alpha", "s1", 3, 300, 0.50, 10_000)
        self._snap("-Users-me-alpha", "s2", 2, 200, 0.25, 5_000)
        self._snap("-Users-me-alpha", "empty", 0, 0, 0.0, 0)  # skipped
        self._snap("-Users-me-beta", "s3", 4, 400, 1.0, 20_000)

        rows = {r["name"]: r for r in allmod.all_project_totals(root=self.root)}
        self.assertEqual(set(rows), {"alpha", "beta"})
        self.assertEqual(rows["alpha"]["sessions"], 2)
        self.assertEqual(rows["alpha"]["utterances"], 5)
        self.assertEqual(rows["alpha"]["tokens"], 500)
        self.assertAlmostEqual(rows["alpha"]["cost_usd"], 0.75, places=6)
        self.assertEqual(rows["alpha"]["api_ms"], 15_000)
        self.assertEqual(rows["beta"]["sessions"], 1)

    def test_merges_renamed_dirs_via_registry_and_dedups_sessions(self):
        a = os.path.join(self.root, "dirA")
        b = os.path.join(self.root, "dirB")
        self._snap("dirA", "shared", 1, 100, 0.10, 1_000)  # same id in both dirs
        self._snap("dirB", "shared", 1, 100, 0.10, 1_000)
        self._snap("dirB", "onlyB", 2, 200, 0.20, 2_000)
        core._register_dir("commit:p1", a, display_name="merged")
        core._register_dir("commit:p1", b)

        rows = allmod.all_project_totals(root=self.root)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["name"], "merged")
        self.assertEqual(r["sessions"], 2)        # shared counted once + onlyB
        self.assertEqual(r["tokens"], 300)
        self.assertEqual(r["utterances"], 3)

    def test_empty_root_returns_no_rows(self):
        self.assertEqual(allmod.all_project_totals(root=self.root), [])


class TestRenderReport(_Base):
    def test_table_sorted_by_cost_with_total(self):
        self._snap("-Users-me-cheap", "s1", 2, 200, 0.50, 5_000)
        self._snap("-Users-me-pricey", "s2", 9, 900, 9.00, 50_000)
        out = allmod.render_report(root=self.root)
        self.assertIn("all projects", out)
        self.assertIn("PROJECT", out)
        self.assertIn("cheap", out)
        self.assertIn("pricey", out)
        self.assertIn("TOTAL", out)
        self.assertIn("$9.50", out)  # summed cost
        self.assertIn("╭", out)
        self.assertLess(out.index("pricey"), out.index("cheap"))  # higher cost first

    def test_no_projects(self):
        self.assertIn("No tracked projects", allmod.render_report(root=self.root))

    def test_run_prints(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            allmod.run(root=self.root)
        self.assertIn("No tracked projects", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
