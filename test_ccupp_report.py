import os
import sys
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp_report as report
import ccupp_core as core


def _write_jsonl(path, objs):
    with open(path, "w") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


class TestReport(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def _snap(self, sid, est, u, t, c, ms, ts=None):
        sd = os.path.join(self.proj, ".ccupp", "sessions")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, sid + ".json"), "w") as f:
            json.dump({"tokens": t, "utterances": u, "cost_usd": c,
                       "api_ms": ms, "estimated": est}, f)
        if ts:
            _write_jsonl(os.path.join(self.proj, sid + ".jsonl"),
                         [{"type": "user", "timestamp": ts, "message": {"content": "hi"}}])

    def test_table_rows_total_marker_and_skip_empty(self):
        self._snap("aaaaaaaa", True, 10, 1000, 1.0, 60_000, ts="2026-05-30T02:00:00Z")
        self._snap("bbbbbbbb", False, 5, 500, 2.0, 30_000, ts="2026-05-30T01:00:00Z")
        self._snap("cccccccc", True, 0, 0, 0.0, 0)  # empty → skipped
        out = report.render_report(project_dir=self.proj, cwd=self.proj)
        self.assertIn("aaaaaaaa~", out)
        self.assertIn("bbbbbbbb", out)
        self.assertNotIn("aaaaaaaa~~", out)
        self.assertNotIn("cccccccc", out)
        self.assertIn("TOTAL", out)
        self.assertIn("15", out)
        self.assertIn("╭", out)
        self.assertIn("USER_MSG", out)
        self.assertIn("~ = backfill estimate", out)
        self.assertLess(out.index("bbbbbbbb"), out.index("aaaaaaaa~"))

    def test_no_sessions(self):
        out = report.render_report(project_dir=self.proj, cwd=self.proj)
        self.assertIn("No tracked sessions", out)


class TestRun(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def test_run_prints_report(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            report.run(project_dir=self.proj, cwd=self.proj)
        self.assertIn("No tracked sessions", buf.getvalue())


class TestReportCrossDir(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.prev_home = os.environ.get("CCUPP_HOME")
        os.environ["CCUPP_HOME"] = self.home
        self.dirA = tempfile.mkdtemp()
        self.dirB = tempfile.mkdtemp()

    def tearDown(self):
        if self.prev_home is None:
            os.environ.pop("CCUPP_HOME", None)
        else:
            os.environ["CCUPP_HOME"] = self.prev_home
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.dirA, ignore_errors=True)
        shutil.rmtree(self.dirB, ignore_errors=True)

    def _snap(self, project_dir, sid, u, t, c, ms, ts=None):
        sd = os.path.join(project_dir, ".ccupp", "sessions")
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(sd, sid + ".json"), "w") as f:
            json.dump({"tokens": t, "utterances": u, "cost_usd": c,
                       "api_ms": ms, "estimated": False}, f)
        if ts:
            _write_jsonl(os.path.join(project_dir, sid + ".jsonl"),
                         [{"type": "user", "timestamp": ts, "message": {"content": "hi"}}])

    def test_collect_aggregates_across_registered_dirs(self):
        self._snap(self.dirA, "aaaaaaaa", 3, 300, 0.5, 10_000, ts="2026-01-01T00:00:00Z")
        self._snap(self.dirB, "bbbbbbbb", 4, 400, 1.0, 20_000, ts="2026-01-02T00:00:00Z")
        core._register_dir("commit:p1", self.dirA)
        core._register_dir("commit:p1", self.dirB)

        rows = report.collect_session_rows(self.dirB, identity="commit:p1")
        sids = sorted(r["sid"] for r in rows)
        self.assertEqual(sids, ["aaaaaaaa", "bbbbbbbb"])

    def test_render_report_uses_identity_for_cross_dir(self):
        self._snap(self.dirA, "aaaaaaaa", 3, 300, 0.5, 10_000, ts="2026-01-01T00:00:00Z")
        self._snap(self.dirB, "bbbbbbbb", 4, 400, 1.0, 20_000, ts="2026-01-02T00:00:00Z")
        core._register_dir("commit:p1", self.dirA)
        core._register_dir("commit:p1", self.dirB)
        with patch.object(report, "project_identity", return_value="commit:p1"):
            out = report.render_report(project_dir=self.dirB, cwd=self.dirB)
        self.assertIn("aaaaaaaa", out)
        self.assertIn("bbbbbbbb", out)
        # totals row sums utterances 3+4
        self.assertIn("TOTAL", out)
        self.assertIn("7", out)


if __name__ == "__main__":
    unittest.main()
