import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import io

from ccupp import model
from ccupp import core
# Reuse shared fixtures (functions/mixins, not TestCase classes — so they aren't re-collected).
from tests.test_core import _assistant, _write_jsonl, _PricingIsolation


class _Base(unittest.TestCase, _PricingIsolation):
    def setUp(self):
        self._set_pricing()
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)
        self._restore_pricing()


class TestRenderReport(_Base):
    def test_groups_by_model_sorted_by_cost_with_total(self):
        _write_jsonl(os.path.join(self.proj, "sess1.jsonl"), [
            _assistant("r1", inp=1000, out=1000, model="claude-sonnet-4-5"),
            _assistant("r2", inp=1000, out=1000, model="claude-opus-4-7"),
            _assistant("r2", inp=1000, out=1000, model="claude-opus-4-7"),  # streamed dup
        ])
        out = model.render_report(project_dir=self.proj, cwd=self.proj)
        self.assertIn("by model", out)
        self.assertIn("MODEL", out)
        self.assertIn("REQS", out)
        self.assertIn("claude-opus-4-7", out)
        self.assertIn("claude-sonnet-4-5", out)
        self.assertIn("$0.09", out)   # opus
        self.assertIn("$0.02", out)   # sonnet (0.018 rounded)
        self.assertIn("$0.11", out)   # total
        self.assertIn("4.0k", out)    # total tokens (2000 + 2000, dup dropped)
        self.assertIn("TOTAL", out)
        self.assertIn("╭", out)
        self.assertLess(out.index("claude-opus-4-7"), out.index("claude-sonnet-4-5"))

    def test_no_sessions(self):
        out = model.render_report(project_dir=self.proj, cwd=self.proj)
        self.assertIn("No tracked sessions", out)


class TestRun(_Base):
    def test_run_prints(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            model.run(project_dir=self.proj, cwd=self.proj)
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
        _write_jsonl(os.path.join(self.dirA, "old.jsonl"),
                     [_assistant("r1", inp=1000, out=1000, model="claude-opus-4-7")])
        core._register_dir("commit:p1", self.dirA)
        core._register_dir("commit:p1", self.dirB)
        with patch.object(model.core, "project_identity", return_value="commit:p1"):
            out = model.render_report(project_dir=self.dirB, cwd=self.dirB)
        self.assertIn("claude-opus-4-7", out)


if __name__ == "__main__":
    unittest.main()
