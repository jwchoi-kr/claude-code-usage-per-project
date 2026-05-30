import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp_export


def _user(text=None, content=None, pid=None, meta=False, side=False, ts=None):
    o = {"type": "user", "message": {"content": content if content is not None else text}}
    if pid is not None:
        o["promptId"] = pid
    if meta:
        o["isMeta"] = True
    if side:
        o["isSidechain"] = True
    if ts:
        o["timestamp"] = ts
    return o


def _write_jsonl(path, objs):
    with open(path, "w") as f:
        for o in objs:
            f.write(json.dumps(o) + "\n")


class TestClassify(unittest.TestCase):
    def test_plain_prompt_kept(self):
        self.assertEqual(ccupp_export.classify("실제 질문이야"), (True, None, "실제 질문이야"))

    def test_empty_dropped(self):
        self.assertEqual(ccupp_export.classify("   ")[0], False)

    def test_local_command_output_dropped(self):
        self.assertFalse(ccupp_export.classify("<local-command-stdout>Settings dialog dismissed</local-command-stdout>")[0])
        self.assertFalse(ccupp_export.classify("<local-command-caveat>Caveat...</local-command-caveat>")[0])

    def test_interrupt_marker_dropped(self):
        self.assertFalse(ccupp_export.classify("[Request interrupted by user]")[0])
        self.assertFalse(ccupp_export.classify("[Request interrupted by user for tool use]")[0])

    def test_ui_slash_wrapper_dropped(self):
        for cmd in ("usage", "model", "clear", "status", "context"):
            txt = f"<command-name>/{cmd}</command-name>\n  <command-message>{cmd}</command-message>\n  <command-args></command-args>"
            self.assertFalse(ccupp_export.classify(txt)[0], cmd)

    def test_ui_slash_plain_dropped(self):
        self.assertFalse(ccupp_export.classify("/usage")[0])
        self.assertFalse(ccupp_export.classify("/model opus")[0])

    def test_work_slash_wrapper_kept_with_body(self):
        txt = "<command-name>/goal</command-name>\n  <command-message>goal</command-message>\n  <command-args>이걸 만들어줘</command-args>"
        keep, cmd, body = ccupp_export.classify(txt)
        self.assertTrue(keep)
        self.assertEqual(cmd, "/goal")
        self.assertEqual(body, "이걸 만들어줘")

    def test_work_slash_plain_form_kept(self):
        keep, cmd, body = ccupp_export.classify("/goal\n프로젝트를 완성시켜.\n알아서 해.")
        self.assertTrue(keep)
        self.assertEqual(cmd, "/goal")
        self.assertEqual(body, "프로젝트를 완성시켜.\n알아서 해.")

    def test_plain_prompt_starting_with_path_not_treated_as_command(self):
        keep, cmd, body = ccupp_export.classify("/etc/hosts 파일을 설명해줘")
        self.assertTrue(keep)
        self.assertIsNone(cmd)
        self.assertEqual(body, "/etc/hosts 파일을 설명해줘")


class TestExtractPrompts(unittest.TestCase):
    def test_filters_meta_sidechain_toolresult(self):
        objs = [
            _user("진짜 프롬프트", pid="p1"),
            _user("시스템", pid="m1", meta=True),
            _user("서브에이전트", pid="s1", side=True),
            _user(content=[{"type": "tool_result", "content": "x"}], pid="t1"),
            {"type": "assistant", "requestId": "r1", "message": {}},
            _user(content=[{"type": "text", "text": "두번째"}], pid="p2"),
        ]
        prompts = ccupp_export.extract_prompts(objs)
        self.assertEqual([p["text"] for p in prompts], ["진짜 프롬프트", "두번째"])

    def test_dedup_by_promptid_keeps_first(self):
        objs = [
            _user("$37.49 이거는?", pid="b1"),
            _user(content=[{"type": "text", "text": "[Request interrupted by user]"}], pid="b1"),
            _user("$37.49 이거는?", pid="b2"),  # genuinely resent → kept
        ]
        prompts = ccupp_export.extract_prompts(objs)
        self.assertEqual([p["text"] for p in prompts], ["$37.49 이거는?", "$37.49 이거는?"])

    def test_goal_command_tagged(self):
        objs = [_user("/goal\n해줘", pid="g1")]
        prompts = ccupp_export.extract_prompts(objs)
        self.assertEqual(prompts, [{"command": "/goal", "text": "해줘"}])


class TestEncodeAndFind(unittest.TestCase):
    def test_encode_cwd(self):
        self.assertEqual(ccupp_export.encode_cwd("/Users/jw/Desktop/cchud"), "-Users-jw-Desktop-cchud")
        self.assertEqual(ccupp_export.encode_cwd("/a/b/.claude/agents"), "-a-b--claude-agents")

    def test_find_by_encoded_name(self):
        root = tempfile.mkdtemp()
        try:
            cwd = "/Users/jw/Desktop/proj"
            d = os.path.join(root, ccupp_export.encode_cwd(cwd))
            os.makedirs(d)
            self.assertEqual(ccupp_export.find_project_dir(cwd, root), d)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_find_by_cwd_field_fallback(self):
        root = tempfile.mkdtemp()
        try:
            d = os.path.join(root, "weird-unmatched-name")
            os.makedirs(d)
            _write_jsonl(os.path.join(d, "s.jsonl"), [
                {"type": "user", "cwd": "/real/project/path", "message": {"content": "hi"}},
            ])
            self.assertEqual(ccupp_export.find_project_dir("/real/project/path", root), d)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestBuildAndRender(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_sessions_sorted_earliest_first(self):
        # file 'b' is alphabetically first but opened LATER
        _write_jsonl(os.path.join(self.d, "b.jsonl"), [
            _user("나중 세션 프롬프트", pid="x", ts="2026-01-02T00:00:00.000Z"),
        ])
        _write_jsonl(os.path.join(self.d, "a.jsonl"), [
            _user("먼저 세션 프롬프트", pid="y", ts="2026-01-01T00:00:00.000Z"),
        ])
        sessions = ccupp_export.build_sessions(self.d)
        firsts = [s["prompts"][0]["text"] for s in sessions]
        self.assertEqual(firsts, ["먼저 세션 프롬프트", "나중 세션 프롬프트"])

    def test_render_structure(self):
        sessions = [
            {"sid": "aaaa1111", "first_ts": "2026-01-01T00:00:00.000Z",
             "prompts": [{"command": None, "text": "첫 프롬프트"}]},
            {"sid": "bbbb2222", "first_ts": "2026-01-02T00:00:00.000Z",
             "prompts": [{"command": "/goal", "text": "목표 본문"}]},
        ]
        md = ccupp_export.render_markdown("myproj", sessions, "2026-01-03 10:00")
        self.assertIn("# myproj — Prompts", md)
        self.assertIn("2 sessions · 2 prompts", md)
        self.assertIn("## Session 1 ·", md)
        self.assertIn("## Session 2 ·", md)
        self.assertIn("`/goal`", md)
        self.assertIn("> 첫 프롬프트", md)
        self.assertIn("> 목표 본문", md)
        self.assertIn("\n---\n", md)  # separator between sessions
        # Session 1 appears before Session 2
        self.assertLess(md.index("## Session 1"), md.index("## Session 2"))

    def test_empty_session_skipped(self):
        sessions = [
            {"sid": "a", "first_ts": "2026-01-01T00:00:00.000Z", "prompts": []},
            {"sid": "b", "first_ts": "2026-01-02T00:00:00.000Z",
             "prompts": [{"command": None, "text": "있음"}]},
        ]
        md = ccupp_export.render_markdown("p", sessions, "now")
        self.assertIn("1 sessions", md)
        self.assertIn("## Session 1 ·", md)
        self.assertNotIn("## Session 2", md)


class TestExportEndToEnd(unittest.TestCase):
    def test_writes_file_and_counts(self):
        root = tempfile.mkdtemp()
        cwd = tempfile.mkdtemp()
        try:
            d = os.path.join(root, ccupp_export.encode_cwd(cwd))
            os.makedirs(d)
            _write_jsonl(os.path.join(d, "s1.jsonl"), [
                _user("/goal\n만들어줘", pid="g", ts="2026-01-01T00:00:00.000Z"),
                _user("<command-name>/usage</command-name><command-args></command-args>", pid="u", ts="2026-01-01T00:01:00.000Z"),
                _user("추가 질문", pid="q", ts="2026-01-01T00:02:00.000Z"),
            ])
            out, nsess, nprompts = ccupp_export.export(cwd, root)
            self.assertEqual(out, os.path.join(cwd, "PROMPTS.md"))
            self.assertEqual(nsess, 1)
            self.assertEqual(nprompts, 2)  # /goal body + 추가 질문 ; /usage dropped
            with open(out, encoding="utf-8") as f:
                md = f.read()
            self.assertIn("만들어줘", md)
            self.assertIn("추가 질문", md)
            self.assertNotIn("usage", md)
        finally:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(cwd, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
