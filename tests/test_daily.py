import os
import json
import shutil
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import io

from ccupp import daily
from ccupp import core
# Reuse shared fixtures (functions/mixins, not TestCase classes — so they aren't re-collected).
from tests.test_core import _assistant, _write_jsonl, _PricingIsolation


class _Base(unittest.TestCase, _PricingIsolation):
    def setUp(self):
        self._set_pricing()
        self._prev_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)
        if self._prev_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._prev_tz
        time.tzset()
        self._restore_pricing()


class TestRenderReport(_Base):
    def test_groups_by_day_sorted_with_total(self):
        _write_jsonl(os.path.join(self.proj, "sess1.jsonl"), [
            {"type": "user", "promptId": "p1", "message": {"content": "hi"},
             "timestamp": "2026-01-02T09:00:00.000Z"},
            _assistant("r2", inp=2000, out=2000, model="claude-opus-4-7",
                       ts="2026-01-02T09:00:04.000Z"),
            {"type": "user", "promptId": "p2", "message": {"content": "yo"},
             "timestamp": "2026-01-01T10:00:00.000Z"},
            _assistant("r1", inp=1000, out=1000, model="claude-opus-4-7",
                       ts="2026-01-01T10:00:05.000Z"),
        ])
        out = daily.render_report(project_dir=self.proj, cwd=self.proj)
        self.assertIn("by day", out)
        self.assertIn("DATE", out)
        self.assertIn("USER_MSG", out)
        self.assertIn("2026-01-01", out)
        self.assertIn("2026-01-02", out)
        self.assertIn("$0.09", out)   # day 1
        self.assertIn("$0.18", out)   # day 2
        self.assertIn("$0.27", out)   # total
        self.assertIn("6.0k", out)    # total tokens
        self.assertIn("TOTAL", out)
        self.assertIn("╭", out)
        self.assertLess(out.index("2026-01-01"), out.index("2026-01-02"))  # ascending

    def test_no_sessions(self):
        out = daily.render_report(project_dir=self.proj, cwd=self.proj)
        self.assertIn("No tracked sessions", out)


class TestRun(_Base):
    def test_run_prints(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            daily.run(project_dir=self.proj, cwd=self.proj)
        self.assertIn("No tracked sessions", buf.getvalue())


class TestCrossDir(unittest.TestCase, _PricingIsolation):
    def setUp(self):
        self._set_pricing()
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
        self._restore_pricing()

    def test_pulls_renamed_dir_via_identity(self):
        _write_jsonl(os.path.join(self.dirA, "old.jsonl"), [
            {"type": "user", "promptId": "p", "message": {"content": "hi"},
             "timestamp": "2026-02-01T00:00:00.000Z"},
            _assistant("r1", inp=1000, out=1000, model="claude-opus-4-7",
                       ts="2026-02-01T00:00:01.000Z"),
        ])
        core._register_dir("commit:p1", self.dirA)
        core._register_dir("commit:p1", self.dirB)
        with patch.object(daily.core, "project_identity", return_value="commit:p1"):
            out = daily.render_report(project_dir=self.dirB, cwd=self.dirB)
        self.assertIn("2026-02-01", out)


if __name__ == "__main__":
    unittest.main()
