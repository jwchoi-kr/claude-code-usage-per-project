import os
import sys
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp


def _assistant(rid, inp=0, cc=0, cr=0, out=0, model="claude-opus-4-7", sidechain=False, ts=None):
    o = {
        "type": "assistant",
        "requestId": rid,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": inp,
                "cache_creation_input_tokens": cc,
                "cache_read_input_tokens": cr,
                "output_tokens": out,
            },
        },
    }
    if sidechain:
        o["isSidechain"] = True
    if ts:
        o["timestamp"] = ts
    return o


def _write_jsonl(path, objs):
    with open(path, "w") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


class TestRenderLine1(unittest.TestCase):
    def test_full(self):
        data = {
            "model": {"display_name": "Opus 4.7"},
            "effort": {"level": "xhigh"},
            "context_window": {"used_percentage": 38},
        }
        out = ccupp.render_line1(data)
        self.assertIn("◈ Opus 4.7", out)
        self.assertIn("· xhigh", out)
        self.assertIn("38%", out)
        self.assertIn("█" * 3, out)
        self.assertIn("\033[36m", out)  # model cyan

    def test_missing_effort_and_null_pct(self):
        data = {"model": {"display_name": "Sonnet 4.6"}, "context_window": {"used_percentage": None}}
        out = ccupp.render_line1(data)
        self.assertIn("◈ Sonnet 4.6", out)
        self.assertNotIn("·", out.split("ctx")[0])  # no effort separator before ctx
        self.assertIn("0%", out)


class TestRenderLine2(unittest.TestCase):
    def test_format(self):
        totals = {"utterances": 12, "tokens": 84200, "cost_usd": 1.83, "api_ms": 372_000}
        out = ccupp.render_line2("demo", totals)
        self.assertEqual(out, "  ▸ demo  💬 12  ·  84.2k tok  ·  $1.83  ·  ⏱ 6m12s")


class TestMain(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def _run_main(self, data):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(data))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ccupp.main()
        finally:
            sys.stdin = old_stdin
        return buf.getvalue()

    def test_two_lines_end_to_end(self):
        tp = os.path.join(self.proj, "sessZ.jsonl")
        _write_jsonl(tp, [
            {"type": "user", "promptId": "p1", "message": {"content": "build it"}},
            _assistant("r1", inp=5, cc=50, cr=500, out=20, model="claude-opus-4-7"),
        ])
        data = {
            "session_id": "sessZ",
            "transcript_path": tp,
            "model": {"display_name": "Opus 4.7"},
            "effort": {"level": "xhigh"},
            "context_window": {"used_percentage": 20},
            "workspace": {"project_dir": self.proj},
            "cost": {"total_cost_usd": 0.25, "total_api_duration_ms": 30_000},
        }
        out = self._run_main(data).rstrip("\n")
        lines = out.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("◈ Opus 4.7", lines[0])
        self.assertIn(f"▸ {os.path.basename(self.proj)}", lines[1])
        self.assertIn("💬 1", lines[1])

    def test_bad_stdin_prints_line1_only_and_does_not_raise(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("not json")
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ccupp.main()
        finally:
            sys.stdin = old_stdin
        out = buf.getvalue().rstrip("\n")
        self.assertEqual(len(out.split("\n")), 1)  # only line 1
        self.assertIn("◈", out)


class TestExportDispatch(unittest.TestCase):
    def test_export_flag_dispatches_to_run_with_remaining_args(self):
        import ccupp_export
        captured = {}
        orig = ccupp_export.run
        ccupp_export.run = lambda argv=None: captured.__setitem__("argv", argv)
        old_argv = sys.argv
        sys.argv = ["ccupp", "--export", "-o", "OUT.md"]
        try:
            ccupp.main()
        finally:
            sys.argv = old_argv
            ccupp_export.run = orig
        self.assertEqual(captured["argv"], ["-o", "OUT.md"])


class TestReportDispatch(unittest.TestCase):
    def test_tty_dispatches_to_report(self):
        import ccupp_report

        class FakeIn:
            def isatty(self):
                return True

            def read(self):
                return ""

        orig = ccupp_report.run
        ccupp_report.run = lambda *a, **kw: print("REPORTOUT")
        old_stdin, old_argv = sys.stdin, sys.argv
        sys.stdin, sys.argv = FakeIn(), ["ccupp"]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ccupp.main()
        finally:
            sys.stdin, sys.argv = old_stdin, old_argv
            ccupp_report.run = orig
        self.assertIn("REPORTOUT", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
