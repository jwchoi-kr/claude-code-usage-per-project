#!/usr/bin/env python3
"""ccupp --export — export your own Claude Code prompts for the current project to Markdown.

Run `ccupp --export` inside any project directory. It locates that project's Claude Code
transcripts under ~/.claude/projects/<encoded-cwd>/, extracts only the prompts *you*
typed (dropping tool results, meta/system messages, subagent chatter, and UI slash
commands like /usage or /model — while keeping work directives like /goal), and writes
them to PROMPTS.md ordered earliest-session-first, earliest-prompt-first within each.
"""
import os
import re
import glob
import argparse
from datetime import datetime

from . import core  # reuse the tested, robust JSONL reader + timestamp parser

# UI / session-control slash commands carry no task intent → drop them.
# Work directives and skills (e.g. /goal, /brainstorm, /review) are kept.
UI_SLASH = {
    "clear", "help", "model", "usage", "cost", "status", "context", "config",
    "login", "logout", "resume", "compact", "exit", "quit", "vim", "doctor",
    "mcp", "agents", "memory", "permissions", "export", "bug", "ide", "theme",
    "fast", "statusline", "settings", "feedback", "output-style", "hooks",
    "terminal-setup", "release-notes", "privacy-settings", "upgrade", "add-dir",
    "allowed-tools", "install-github-app", "migrate-installer", "listen", "init",
}

INTERRUPT_PREFIX = "[Request interrupted"
_CMD_NAME = re.compile(r"<command-name>\s*/?([\w:-]+)\s*</command-name>")
_CMD_ARGS = re.compile(r"<command-args>(.*?)(?:</command-args>|\Z)", re.S)
_PLAIN_CMD = re.compile(r"^/([A-Za-z][\w:-]*)$")


def _text_of(o):
    """User-authored text of an entry, or None if it is not free-text (e.g. tool_result)."""
    content = (o.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    if isinstance(content, str):
        return content
    return None


def classify(text):
    """Decide whether a piece of user text is a real prompt.

    Returns (keep: bool, command: str|None, body: str).
    - UI slash commands (/usage, /model, ...) → dropped.
    - Work slash commands (/goal, ...) → kept, with command tag and its body.
    - local-command output/caveat, interrupt markers, empties → dropped.
    - everything else → kept as a plain prompt.
    """
    t = (text or "").strip()
    if not t:
        return (False, None, "")
    if t.startswith("<local-command-stdout>") or t.startswith("<local-command-caveat>"):
        return (False, None, "")
    if t.startswith(INTERRUPT_PREFIX):
        return (False, None, "")

    m = _CMD_NAME.search(t)
    if m:
        name = m.group(1).lower()
        if name in UI_SLASH:
            return (False, None, "")
        am = _CMD_ARGS.search(t)
        body = am.group(1).strip() if am else ""
        return (True, "/" + name, body)

    if t.startswith("/"):
        first_line = t.split("\n", 1)[0]
        token = first_line.split()[0] if first_line.split() else ""
        pm = _PLAIN_CMD.match(token)
        if pm:
            name = pm.group(1).lower()
            if name in UI_SLASH:
                return (False, None, "")
            inline = first_line[len(token):].strip()
            rest = t.split("\n", 1)[1] if "\n" in t else ""
            body = (inline + ("\n" + rest if rest else "")).strip()
            return (True, "/" + name, body)

    return (True, None, t)


def extract_prompts(objs):
    """Ordered list of {command, text} for genuine human prompts (dedup by promptId)."""
    out = []
    seen = set()
    for o in objs:
        if o.get("type") != "user":
            continue
        if o.get("isMeta") or o.get("isSidechain"):
            continue
        txt = _text_of(o)
        if txt is None:
            continue
        keep, cmd, body = classify(txt)
        if not keep:
            continue
        pid = o.get("promptId")
        if pid is not None:
            if pid in seen:
                continue
            seen.add(pid)
        out.append({"command": cmd, "text": body})
    return out


def encode_cwd(cwd):
    """Claude Code's project-folder name for a working directory: '/' and '.' → '-'."""
    return re.sub(r"[/.]", "-", os.path.abspath(cwd))


def find_project_dir(cwd, projects_root):
    """Locate the ~/.claude/projects/<dir> for cwd. Encoded name first, then cwd-field scan."""
    cand = os.path.join(projects_root, encode_cwd(cwd))
    if os.path.isdir(cand):
        return cand
    target = os.path.abspath(cwd)
    for d in sorted(glob.glob(os.path.join(projects_root, "*"))):
        if not os.path.isdir(d):
            continue
        for tp in glob.glob(os.path.join(d, "*.jsonl")):
            for o in core.iter_jsonl(tp):
                c = o.get("cwd")
                if c:
                    if os.path.abspath(c) == target:
                        return d
                    break
            break
    return None


def session_summary(transcript_path):
    objs = list(core.iter_jsonl(transcript_path))
    first_ts = None
    sid = None
    for o in objs:
        if first_ts is None and o.get("timestamp"):
            first_ts = o["timestamp"]
        if sid is None and o.get("sessionId"):
            sid = o.get("sessionId")
        if first_ts and sid:
            break
    return {
        "path": transcript_path,
        "sid": sid or os.path.splitext(os.path.basename(transcript_path))[0],
        "first_ts": first_ts,
        "prompts": extract_prompts(objs),
    }


def build_sessions(project_dir, identity=None):
    """All sessions in a project, earliest-opened first (by first message timestamp).

    If `identity` is provided, also includes sessions from every transcript dir
    registered under the same project identity (cross-rename support).
    """
    dirs = [project_dir] if project_dir else []
    if identity:
        for d in core._dirs_for_identity(identity):
            if d not in dirs:
                dirs.append(d)
    sums = []
    seen_sid = set()
    for d in dirs:
        for tp in glob.glob(os.path.join(d, "*.jsonl")):
            s = session_summary(tp)
            if s["sid"] in seen_sid:
                continue
            seen_sid.add(s["sid"])
            sums.append(s)
    sums.sort(key=lambda s: (s["first_ts"] is None, s["first_ts"] or "", s["path"]))
    return sums


def _fmt_ts(ts):
    d = core._parse_ts(ts)
    if not d:
        return "?"
    return d.astimezone().strftime("%Y-%m-%d %H:%M")


def _blockquote(text):
    return "\n".join(("> " + ln) if ln.strip() else ">" for ln in text.split("\n"))


def render_markdown(project_name, sessions, now_str):
    sessions = [s for s in sessions if s["prompts"]]
    total = sum(len(s["prompts"]) for s in sessions)
    head = [
        f"# {project_name} — Prompts\n",
        f"Generated {now_str} · {len(sessions)} sessions · {total} prompts\n",
    ]
    blocks = []
    for i, s in enumerate(sessions, 1):
        seg = [f"## Session {i} · {_fmt_ts(s['first_ts'])} · `{s['sid'][:8]}`",
               f"{len(s['prompts'])} prompts\n"]
        for j, p in enumerate(s["prompts"], 1):
            tag = f" · `{p['command']}`" if p.get("command") else ""
            body = p["text"] if p["text"] else (p["command"] or "")
            seg.append(f"**{j}.**{tag}\n")
            seg.append(_blockquote(body) + "\n")
        blocks.append("\n".join(seg))
    return "\n".join(head) + "\n" + "\n---\n\n".join(blocks).rstrip() + "\n"


def export(cwd, projects_root, out_path=None):
    project_dir = find_project_dir(cwd, projects_root)
    project_name = os.path.basename(os.path.abspath(cwd).rstrip("/")) or os.path.abspath(cwd)
    identity = core.project_identity(cwd)
    sessions = build_sessions(project_dir, identity=identity) if (project_dir or identity) else []
    md = render_markdown(project_name, sessions, datetime.now().strftime("%Y-%m-%d %H:%M"))
    out_path = out_path or os.path.join(cwd, "PROMPTS.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    n_sessions = len([s for s in sessions if s["prompts"]])
    n_prompts = sum(len(s["prompts"]) for s in sessions)
    return out_path, n_sessions, n_prompts


def run(argv=None):
    ap = argparse.ArgumentParser(
        prog="ccupp --export",
        description="Export your Claude Code prompts for this project to a Markdown file.",
    )
    ap.add_argument("-o", "--out", default=None, help="output path (default: ./PROMPTS.md)")
    args = ap.parse_args(argv)
    cwd = os.getcwd()
    projects_root = os.path.expanduser("~/.claude/projects")
    out, n_sessions, n_prompts = export(cwd, projects_root, args.out)
    if n_prompts == 0:
        print(f"No user prompts found for this folder.\n  (searched: {projects_root})")
    else:
        print(f"✅ {out}\n   {n_sessions} sessions · {n_prompts} prompts")
