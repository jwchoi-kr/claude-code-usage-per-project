#!/usr/bin/env python3
"""ccupp --daily — the current project's Claude Code usage grouped by calendar day.

Run `ccupp --daily` inside a project directory. Where bare `ccupp` breaks usage
down by session, this breaks the same project's usage down by local calendar
date — recomputed from the raw transcripts so tokens, cost, and response time
are attributed to the day each request happened. Transcript dirs sharing a git
identity (folder renames) are merged.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccupp_core as ccupp  # reuse the tested aggregation, persistence, and table renderer


def _collect_objs(project_dir, identity):
    objs = []
    for tp in ccupp.project_transcript_paths(project_dir, identity):
        objs.extend(ccupp.iter_jsonl(tp))
    return objs


def render_report(project_dir=None, cwd=None):
    cwd = cwd or os.getcwd()
    root = os.path.expanduser("~/.claude/projects")
    if project_dir is None:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import ccupp_export
        project_dir = ccupp_export.find_project_dir(cwd, root)
    project_name = os.path.basename(os.path.abspath(cwd).rstrip("/")) or cwd
    title = f"{project_name} — Claude Code usage by day"

    identity = ccupp.project_identity(cwd) if cwd else None
    objs = _collect_objs(project_dir, identity) if (project_dir or identity) else []
    days = ccupp.aggregate_by_day(objs)
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
        body.append([d or "—", str(u), ccupp.format_tokens(t),
                     f"${c:.2f}", ccupp.format_duration(ms)])
    total = ["TOTAL", str(tu), ccupp.format_tokens(tt),
             f"${tc:.2f}", ccupp.format_duration(tms)]
    return f"{title}\n\n{ccupp._box_table(headers, body, total, aligns)}"


def run(project_dir=None, cwd=None):
    print(render_report(project_dir=project_dir, cwd=cwd))


if __name__ == "__main__":
    run()
