import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp_core as core


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


class TestFormatTokens(unittest.TestCase):
    def test_compact_units(self):
        self.assertEqual(core.format_tokens(0), "0")
        self.assertEqual(core.format_tokens(999), "999")
        self.assertEqual(core.format_tokens(1000), "1.0k")
        self.assertEqual(core.format_tokens(84200), "84.2k")
        self.assertEqual(core.format_tokens(1_000_000), "1.0M")
        self.assertEqual(core.format_tokens(12_345_000), "12.3M")
        self.assertEqual(core.format_tokens(None), "0")


class TestFormatDuration(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(core.format_duration(0), "0m00s")
        self.assertEqual(core.format_duration(372_000), "6m12s")
        self.assertEqual(core.format_duration(59_000), "0m59s")

    def test_hours(self):
        self.assertEqual(core.format_duration(7_530_000), "2h05m")
        self.assertEqual(core.format_duration(3_600_000), "1h00m")
        self.assertEqual(core.format_duration(None), "0m00s")


class TestBar(unittest.TestCase):
    def test_render_bar(self):
        self.assertEqual(core.render_bar(0), "░" * 10)
        self.assertEqual(core.render_bar(38), "█" * 3 + "░" * 7)
        self.assertEqual(core.render_bar(100), "█" * 10)
        self.assertEqual(core.render_bar(None), "░" * 10)
        self.assertEqual(core.render_bar(150), "█" * 10)  # clamps

    def test_bar_color(self):
        self.assertEqual(core.bar_color(50), "\033[32m")   # green
        self.assertEqual(core.bar_color(70), "\033[33m")   # yellow
        self.assertEqual(core.bar_color(89), "\033[33m")
        self.assertEqual(core.bar_color(90), "\033[31m")   # red


class TestIterJsonl(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_skips_blank_and_broken_lines(self):
        path = os.path.join(self.dir, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"a": 1}) + "\n")
            f.write("\n")
            f.write("{not valid json\n")
            f.write(json.dumps({"a": 2}) + "\n")
        objs = list(core.iter_jsonl(path))
        self.assertEqual(objs, [{"a": 1}, {"a": 2}])

    def test_missing_file_yields_nothing(self):
        objs = list(core.iter_jsonl(os.path.join(self.dir, "nope.jsonl")))
        self.assertEqual(objs, [])


class TestSumTokens(unittest.TestCase):
    def test_dedup_by_request_and_exclude_cache_read(self):
        objs = [
            _assistant("r1", inp=10, cc=100, cr=9999, out=50),
            _assistant("r1", inp=10, cc=100, cr=9999, out=50),  # streamed duplicate
            _assistant("r1", inp=10, cc=100, cr=9999, out=50),
        ]
        # counted once: 10 + 100 + 50 = 160 (cache_read excluded)
        self.assertEqual(core.sum_unique_tokens(objs), 160)

    def test_includes_sidechain_and_ignores_non_assistant(self):
        objs = [
            {"type": "user", "message": {"content": "hi"}},
            _assistant("r1", inp=0, cc=20, cr=0, out=5),
            _assistant("r2", inp=0, cc=30, cr=0, out=7, sidechain=True),
        ]
        self.assertEqual(core.sum_unique_tokens(objs), 20 + 5 + 30 + 7)


class TestCountUtterances(unittest.TestCase):
    def test_filters_and_dedup(self):
        objs = [
            {"type": "user", "promptId": "p1", "message": {"content": "real question"}},
            {"type": "user", "isMeta": True, "promptId": "m1", "message": {"content": "system reminder"}},
            {"type": "user", "isSidechain": True, "promptId": "s1", "message": {"content": "subagent"}},
            {"type": "user", "promptId": "t1", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "user", "promptId": "c1", "message": {"content": "<command-name>/model</command-name>"}},
            {"type": "assistant", "requestId": "r1", "message": {}},
            {"type": "user", "promptId": "p2", "message": {"content": [{"type": "text", "text": "second"}]}},
        ]
        self.assertEqual(core.count_utterances(objs), 2)

    def test_same_promptid_counted_once(self):
        objs = [
            {"type": "user", "promptId": "p1", "message": {"content": "part a"}},
            {"type": "user", "promptId": "p1", "message": {"content": "part b"}},
        ]
        self.assertEqual(core.count_utterances(objs), 1)


class TestEstimateApiMs(unittest.TestCase):
    def test_sums_per_request_response_time(self):
        objs = [
            {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z"},
            {"type": "assistant", "requestId": "r1", "timestamp": "2026-01-01T00:00:03.000Z", "message": {}},
            {"type": "user", "timestamp": "2026-01-01T00:00:10.000Z"},
            {"type": "assistant", "requestId": "r2", "timestamp": "2026-01-01T00:00:14.500Z", "message": {}},
        ]
        self.assertEqual(core.estimate_api_ms(objs), 7500)

    def test_handles_missing_timestamps(self):
        objs = [{"type": "assistant", "requestId": "r1", "message": {}}]
        self.assertEqual(core.estimate_api_ms(objs), 0)


class TestEstimateCost(unittest.TestCase):
    def test_opus_cost_with_cache_read_included(self):
        objs = [_assistant("r1", inp=1000, cc=0, cr=0, out=1000, model="claude-opus-4-7")]
        self.assertAlmostEqual(core.estimate_cost(objs), 0.09, places=6)

    def test_sonnet_includes_all_components(self):
        objs = [_assistant("r1", inp=20, cc=200, cr=2000, out=30, model="claude-sonnet-4-5")]
        expected = (20 * 3 + 200 * 3.75 + 2000 * 0.3 + 30 * 15) / 1_000_000
        self.assertAlmostEqual(core.estimate_cost(objs), expected, places=6)

    def test_dedup_by_request(self):
        objs = [
            _assistant("r1", inp=1000, out=1000, model="claude-opus-4-7"),
            _assistant("r1", inp=1000, out=1000, model="claude-opus-4-7"),
        ]
        self.assertAlmostEqual(core.estimate_cost(objs), 0.09, places=6)


class TestProjectTotals(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def _build(self):
        a = os.path.join(self.proj, "sessA.jsonl")
        _write_jsonl(a, [
            {"type": "user", "promptId": "pa", "message": {"content": "hi"}},
            _assistant("ra", inp=10, cc=100, cr=1000, out=50, model="claude-opus-4-7"),
        ])
        b = os.path.join(self.proj, "sessB.jsonl")
        _write_jsonl(b, [
            {"type": "user", "promptId": "pb", "message": {"content": "yo"},
             "timestamp": "2026-01-01T00:00:00.000Z"},
            _assistant("rb", inp=20, cc=200, cr=2000, out=30, model="claude-sonnet-4-5",
                       ts="2026-01-01T00:00:02.000Z"),
        ])
        return a

    def test_totals_combine_live_and_backfill(self):
        a = self._build()
        totals = core.project_totals(a, "sessA", total_cost_usd=0.50, total_api_ms=60_000)
        self.assertEqual(totals["tokens"], 160 + 250)
        self.assertEqual(totals["utterances"], 2)
        b_cost = (20 * 3 + 200 * 3.75 + 2000 * 0.3 + 30 * 15) / 1_000_000
        self.assertAlmostEqual(totals["cost_usd"], 0.50 + b_cost, places=6)
        self.assertEqual(totals["api_ms"], 60_000 + 2_000)
        self.assertTrue(os.path.exists(os.path.join(self.proj, ".ccupp", "sessions", "sessB.json")))

    def test_idempotent(self):
        a = self._build()
        t1 = core.project_totals(a, "sessA", 0.50, 60_000)
        t2 = core.project_totals(a, "sessA", 0.50, 60_000)
        self.assertEqual(t1, t2)


class TestNormalizeRemote(unittest.TestCase):
    def test_https_strip_dotgit(self):
        self.assertEqual(core._normalize_remote("https://github.com/u/repo.git"),
                         "github.com/u/repo")

    def test_ssh_form(self):
        self.assertEqual(core._normalize_remote("git@github.com:u/repo.git"),
                         "github.com/u/repo")

    def test_trailing_slash_and_case(self):
        self.assertEqual(core._normalize_remote("HTTPS://GitHub.com/u/Repo/"),
                         "github.com/u/repo")

    def test_empty(self):
        self.assertEqual(core._normalize_remote(""), "")
        self.assertEqual(core._normalize_remote(None), "")


class TestProjectIdentity(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.cwd, ignore_errors=True)

    def _fake_git(self, mapping):
        """Build a fake _git(cwd, args) that returns mapping[tuple(args)]."""
        def fake(cwd, args):
            return mapping.get(tuple(args))
        return fake

    def test_invalid_cwd(self):
        self.assertIsNone(core.project_identity(None))
        self.assertIsNone(core.project_identity(""))
        self.assertIsNone(core.project_identity("/no/such/dir/should/exist/123abc"))

    def test_remote_wins(self):
        fake = self._fake_git({
            ("config", "--get", "remote.origin.url"): "git@github.com:u/repo.git",
            ("rev-list", "--max-parents=0", "HEAD"): "abc123",
        })
        with patch.object(core, "_git", side_effect=fake):
            ident = core.project_identity(self.cwd)
        self.assertTrue(ident.startswith("remote:"))
        # deterministic: same URL hashes the same
        with patch.object(core, "_git", side_effect=fake):
            self.assertEqual(core.project_identity(self.cwd), ident)

    def test_first_commit_fallback_when_no_remote(self):
        fake = self._fake_git({
            ("config", "--get", "remote.origin.url"): None,
            ("rev-list", "--max-parents=0", "HEAD"): "deadbeef1234567890",
        })
        with patch.object(core, "_git", side_effect=fake):
            ident = core.project_identity(self.cwd)
        self.assertEqual(ident, "commit:deadbeef12345678")

    def test_none_when_no_git_info(self):
        with patch.object(core, "_git", return_value=None):
            self.assertIsNone(core.project_identity(self.cwd))


class _CcuppHomeIsolation:
    """Mixin: redirect CCUPP_HOME to a tempdir so tests don't touch real registry."""

    def _isolate_home(self):
        self._home = tempfile.mkdtemp()
        self._prev_home = os.environ.get("CCUPP_HOME")
        os.environ["CCUPP_HOME"] = self._home

    def _restore_home(self):
        if self._prev_home is None:
            os.environ.pop("CCUPP_HOME", None)
        else:
            os.environ["CCUPP_HOME"] = self._prev_home
        shutil.rmtree(self._home, ignore_errors=True)


class TestRegistry(unittest.TestCase, _CcuppHomeIsolation):
    def setUp(self):
        self._isolate_home()

    def tearDown(self):
        self._restore_home()

    def test_missing_registry_returns_empty(self):
        self.assertEqual(core._dirs_for_identity("commit:nope"), [])
        self.assertEqual(core._load_registry()["projects"], {})

    def test_register_dedup_and_lookup(self):
        core._register_dir("commit:x", "/a", display_name="A")
        core._register_dir("commit:x", "/a")          # dup → no-op
        core._register_dir("commit:x", "/b")
        core._register_dir("commit:y", "/c")
        self.assertEqual(core._dirs_for_identity("commit:x"), ["/a", "/b"])
        self.assertEqual(core._dirs_for_identity("commit:y"), ["/c"])
        reg = core._load_registry()
        self.assertEqual(reg["projects"]["commit:x"]["display_name"], "A")
        self.assertIn("last_seen", reg["projects"]["commit:x"])

    def test_corrupt_registry_falls_back_empty(self):
        path = core._registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")
        self.assertEqual(core._load_registry()["projects"], {})
        self.assertEqual(core._dirs_for_identity("commit:any"), [])


class TestProjectTotalsCrossDir(unittest.TestCase, _CcuppHomeIsolation):
    def setUp(self):
        self._isolate_home()
        self.dirA = tempfile.mkdtemp()
        self.dirB = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dirA, ignore_errors=True)
        shutil.rmtree(self.dirB, ignore_errors=True)
        self._restore_home()

    def test_aggregates_snapshots_from_registered_dirs(self):
        # Prior session lives in dirA (old folder name).
        old_snap_dir = os.path.join(self.dirA, ".ccupp", "sessions")
        os.makedirs(old_snap_dir)
        with open(os.path.join(old_snap_dir, "oldSess.json"), "w") as f:
            json.dump({"tokens": 1000, "utterances": 7,
                       "cost_usd": 1.50, "api_ms": 120_000, "estimated": True}, f)

        # Pre-register dirA under our fake identity (mimics a previous render).
        core._register_dir("commit:proj1", self.dirA)

        # New live session in dirB (new folder name).
        live_tp = os.path.join(self.dirB, "newSess.jsonl")
        _write_jsonl(live_tp, [
            {"type": "user", "promptId": "p", "message": {"content": "hi"}},
            _assistant("r1", inp=10, cc=0, cr=0, out=20, model="claude-opus-4-7"),
        ])

        with patch.object(core, "project_identity", return_value="commit:proj1"):
            totals = core.project_totals(live_tp, "newSess",
                                         total_cost_usd=0.25, total_api_ms=30_000,
                                         cwd="/fake/cwd")

        # live: 1 utterance + 10+20 tokens, 0.25 cost, 30s
        # backfill from dirA: 7 utterances + 1000 tokens, 1.50 cost, 120s
        self.assertEqual(totals["utterances"], 1 + 7)
        self.assertEqual(totals["tokens"], 30 + 1000)
        self.assertAlmostEqual(totals["cost_usd"], 0.25 + 1.50, places=6)
        self.assertEqual(totals["api_ms"], 30_000 + 120_000)

        # dirB got registered too.
        self.assertIn(self.dirB, core._dirs_for_identity("commit:proj1"))

    def test_identity_none_keeps_old_behavior(self):
        a = os.path.join(self.dirA, "sessA.jsonl")
        _write_jsonl(a, [
            {"type": "user", "promptId": "p", "message": {"content": "hi"}},
            _assistant("r1", inp=10, cc=0, cr=0, out=20, model="claude-opus-4-7"),
        ])
        # Even if dirB is registered under some other identity, no cwd → no cross-pull.
        core._register_dir("commit:other", self.dirB)
        totals = core.project_totals(a, "sessA", 0.0, 0)
        self.assertEqual(totals["utterances"], 1)
        self.assertEqual(totals["tokens"], 30)


if __name__ == "__main__":
    unittest.main()
