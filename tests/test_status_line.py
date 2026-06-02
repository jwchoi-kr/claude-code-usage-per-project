import os
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ccupp import status_line as sl


def _assistant(rid, inp=0, cc=0, cr=0, out=0, model="claude-opus-4-7"):
    return {
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


def _write_jsonl(path, objs):
    with open(path, "w") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


class TestBar(unittest.TestCase):
    def test_render_bar(self):
        self.assertEqual(sl.render_bar(0), "░" * 10)
        self.assertEqual(sl.render_bar(38), "█" * 3 + "░" * 7)
        self.assertEqual(sl.render_bar(100), "█" * 10)
        self.assertEqual(sl.render_bar(None), "░" * 10)
        self.assertEqual(sl.render_bar(150), "█" * 10)  # clamps

    def test_bar_color(self):
        self.assertEqual(sl.bar_color(50), "\033[32m")   # green
        self.assertEqual(sl.bar_color(70), "\033[33m")   # yellow
        self.assertEqual(sl.bar_color(89), "\033[33m")
        self.assertEqual(sl.bar_color(90), "\033[31m")   # red


class TestRenderLine1(unittest.TestCase):
    def test_full(self):
        data = {
            "model": {"display_name": "Opus 4.7"},
            "effort": {"level": "xhigh"},
            "context_window": {"used_percentage": 38},
        }
        out = sl.render_line1(data)
        self.assertIn("Opus 4.7", out)
        self.assertIn(sl._tag("xhigh"), out)
        self.assertIn("38%", out)
        self.assertIn("█" * 3, out)
        self.assertIn(sl.CYAN, out)
        self.assertIn(f"{sl.DIM}ctx{sl.RESET}", out)

    def test_missing_effort_and_null_pct(self):
        data = {"model": {"display_name": "Sonnet 4.6"}, "context_window": {"used_percentage": None}}
        out = sl.render_line1(data)
        self.assertIn("Sonnet 4.6", out)
        self.assertNotIn(f"{sl.DIM}[{sl.RESET}", out.split("ctx")[0])  # no effort tag
        self.assertIn("0%", out)


class TestRenderLine2(unittest.TestCase):
    def test_format(self):
        totals = {"utterances": 12, "tokens": 84200, "cost_usd": 1.83, "api_ms": 372_000}
        out = sl.render_line2("demo", totals)
        t = sl._tag
        expected = "  " + "  ".join(["demo", t("12 msg"), t("84.2k tok"), t("$1.83"), t("6m12s")])
        self.assertEqual(out, expected)


class TestRender(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def test_two_lines_with_totals(self):
        tp = os.path.join(self.proj, "sessZ.jsonl")
        _write_jsonl(tp, [
            {"type": "user", "promptId": "p1", "message": {"content": "build it"}},
            _assistant("r1", inp=5, cc=50, cr=500, out=20, model="claude-opus-4-7"),
        ])
        data = {
            "session_id": "sessZ",
            "transcript_path": tp,
            "model": {"display_name": "Opus 4.7"},
            "context_window": {"used_percentage": 20},
            "workspace": {"project_dir": self.proj},
            "cost": {"total_cost_usd": 0.25, "total_api_duration_ms": 30_000},
        }
        out = sl.render(data)
        lines = out.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("Opus 4.7", lines[0])
        self.assertIn(os.path.basename(self.proj), lines[1])
        self.assertIn("1 msg", lines[1])

    def test_line2_failure_emits_line1_only(self):
        data = {
            "session_id": "x",
            "transcript_path": os.path.join(self.proj, "x.jsonl"),
            "model": {"display_name": "Opus 4.7"},
            "context_window": {"used_percentage": 10},
            "workspace": {"project_dir": self.proj},
        }
        with patch.object(sl, "project_totals", side_effect=RuntimeError("boom")):
            out = sl.render(data)
        self.assertEqual(len(out.split("\n")), 1)  # line 2 skipped
        self.assertIn("Opus 4.7", out)
        self.assertIn("ctx", out)


if __name__ == "__main__":
    unittest.main()
