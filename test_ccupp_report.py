import os
import sys
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp_report as report


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
        self.assertIn("┌", out)
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


if __name__ == "__main__":
    unittest.main()
