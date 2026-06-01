#!/usr/bin/env python3
"""ccupp --model — the current project's Claude Code usage grouped by model.

Run `ccupp --model` inside a project directory. It recomputes the project's
usage from the raw transcripts and splits it per model (e.g. claude-opus-4-7
vs claude-sonnet-4-5), showing how requests, tokens, and cost are distributed —
useful for seeing where spend concentrates. Transcript dirs sharing a git
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
    title = f"{project_name} — Claude Code usage by model"

    identity = ccupp.project_identity(cwd) if cwd else None
    objs = _collect_objs(project_dir, identity) if (project_dir or identity) else []
    models = ccupp.aggregate_by_model(objs)
    models = {m: v for m, v in models.items() if int(v.get("tokens") or 0)}
    if not models:
        return f"{title}\n\n  No tracked sessions for this folder.\n  (searched: {root})"

    headers = ["MODEL", "REQS", "TOKENS", "COST"]
    aligns = ["left", "right", "right", "right"]
    body = []
    tr = tt = 0
    tc = 0.0
    for m in sorted(models, key=lambda k: models[k]["cost_usd"], reverse=True):
        v = models[m]
        r = int(v.get("reqs") or 0)
        t = int(v.get("tokens") or 0)
        c = float(v.get("cost_usd") or 0.0)
        tr += r
        tt += t
        tc += c
        body.append([m, str(r), ccupp.format_tokens(t), f"${c:.2f}"])
    total = ["TOTAL", str(tr), ccupp.format_tokens(tt), f"${tc:.2f}"]
    return f"{title}\n\n{ccupp._box_table(headers, body, total, aligns)}"


def run(project_dir=None, cwd=None):
    print(render_report(project_dir=project_dir, cwd=cwd))


if __name__ == "__main__":
    run()
