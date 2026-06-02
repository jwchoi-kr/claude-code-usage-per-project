#!/usr/bin/env python3
"""ccupp --daily — the current project's Claude Code usage grouped by calendar day.

Run `ccupp --daily` inside a project directory. Where bare `ccupp` breaks usage
down by session, this breaks the same project's usage down by local calendar
date — recomputed from the raw transcripts so tokens, cost, and response time
are attributed to the day each request happened. Transcript dirs sharing a git
identity (folder renames) are merged.
"""
import os

from . import core  # reuse the tested aggregation, persistence, and table renderer


def _collect_objs(project_dir, identity):
    objs = []
    for tp in core.project_transcript_paths(project_dir, identity):
        objs.extend(core.iter_jsonl(tp))
    return objs


def render_report(project_dir=None, cwd=None):
    cwd = cwd or os.getcwd()
    root = os.path.expanduser("~/.claude/projects")
    if project_dir is None:
        from . import export
        project_dir = export.find_project_dir(cwd, root)
    project_name = os.path.basename(os.path.abspath(cwd).rstrip("/")) or cwd
    title = f"{project_name} — Claude Code usage by day"

    identity = core.project_identity(cwd) if cwd else None
    objs = _collect_objs(project_dir, identity) if (project_dir or identity) else []
    days = core.aggregate_by_day(objs)
    days = {d: v for d, v in days.items()
            if int(v.get("utterances") or 0) or int(v.get("tokens") or 0)}
    if not days:
        return f"{title}\n\n  No tracked sessions for this folder.\n  (searched: {root})"

    headers = ["DATE", "USER_MSG", "TOKENS", "COST", "TIME"]
    aligns = ["left", "right", "right", "right", "right"]
    body = []
    tu = tt = tms = 0
    tc = 0.0
    for d in sorted(days, key=lambda k: (k is None, k or "")):
        v = days[d]
        u = int(v.get("utterances") or 0)
        t = int(v.get("tokens") or 0)
        c = float(v.get("cost_usd") or 0.0)
        ms = int(v.get("api_ms") or 0)
        tu += u
        tt += t
        tc += c
        tms += ms
        body.append([d or "—", str(u), core.format_tokens(t),
                     f"${c:.2f}", core.format_duration(ms)])
    total = ["TOTAL", str(tu), core.format_tokens(tt),
             f"${tc:.2f}", core.format_duration(tms)]
    return f"{title}\n\n{core._box_table(headers, body, total, aligns)}"


def run(project_dir=None, cwd=None):
    print(render_report(project_dir=project_dir, cwd=cwd))
