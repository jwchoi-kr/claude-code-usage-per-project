#!/usr/bin/env python3
"""ccupp --all — compare Claude Code usage across every tracked project.

Run `ccupp --all` from anywhere. It scans ~/.claude/projects/*, aggregates each
project's per-session snapshots into one row, and prints a comparison table
sorted by cost (highest first). Transcript dirs that share a project identity
(folder renames) are merged into a single row, and sessions duplicated across
those dirs are counted once.
"""
import os
import glob

from . import core  # reuse the tested aggregation, persistence, and table renderer


def _display_from_dir(transcript_dir):
    """Readable project name fallback: last segment of a sanitized transcript dir."""
    base = os.path.basename(transcript_dir.rstrip("/"))
    return base.rsplit("-", 1)[-1] or base


def _aggregate_snaps(snaps):
    agg = {"sessions": 0, "tokens": 0, "utterances": 0, "cost_usd": 0.0, "api_ms": 0}
    for s in snaps.values():
        t = int(s.get("tokens") or 0)
        u = int(s.get("utterances") or 0)
        if not t and not u:
            continue
        agg["sessions"] += 1
        agg["tokens"] += t
        agg["utterances"] += u
        agg["cost_usd"] += float(s.get("cost_usd") or 0.0)
        agg["api_ms"] += int(s.get("api_ms") or 0)
    return agg


def all_project_totals(root=None):
    """Per-project usage totals across every transcript dir under root.

    Dirs sharing a registry identity (folder renames) collapse into one row;
    sessions duplicated across those dirs are counted once. Returns a list of
    {name, sessions, tokens, utterances, cost_usd, api_ms}, unsorted, with
    empty projects omitted.
    """
    root = root or os.path.expanduser("~/.claude/projects")
    reg = core._load_registry()
    dir_to_identity = {}
    identity_display = {}
    for ident, entry in (reg.get("projects") or {}).items():
        if not isinstance(entry, dict):
            continue
        identity_display[ident] = entry.get("display_name")
        for d in entry.get("transcript_dirs") or []:
            if isinstance(d, str):
                dir_to_identity[os.path.abspath(d)] = ident

    groups = {}
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        if not os.path.isdir(d):
            continue
        ad = os.path.abspath(d)
        ident = dir_to_identity.get(ad)
        key = ident or ad
        g = groups.setdefault(key, {"dirs": [], "name": None})
        g["dirs"].append(ad)
        if ident and identity_display.get(ident):
            g["name"] = identity_display[ident]

    rows = []
    for g in groups.values():
        snaps = {}
        for d in g["dirs"]:
            core._accumulate_dir_snaps(d, None, snaps)
        agg = _aggregate_snaps(snaps)
        if not agg["sessions"]:
            continue
        agg["name"] = g["name"] or _display_from_dir(g["dirs"][0])
        rows.append(agg)
    return rows


def render_report(root=None):
    title = "Claude Code usage — all projects"
    rows = all_project_totals(root=root)
    if not rows:
        return f"{title}\n\n  No tracked projects found."
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)

    headers = ["PROJECT", "SESSIONS", "USER_MSG", "TOKENS", "COST", "TIME"]
    aligns = ["left", "right", "right", "right", "right", "right"]
    body = []
    ts = tu = tt = tms = 0
    tc = 0.0
    for r in rows:
        ts += r["sessions"]
        tu += r["utterances"]
        tt += r["tokens"]
        tc += r["cost_usd"]
        tms += r["api_ms"]
        body.append([
            r["name"],
            str(r["sessions"]),
            str(r["utterances"]),
            core.format_tokens(r["tokens"]),
            f"${r['cost_usd']:.2f}",
            core.format_duration(r["api_ms"]),
        ])
    total = ["TOTAL", str(ts), str(tu), core.format_tokens(tt),
             f"${tc:.2f}", core.format_duration(tms)]
    return f"{title}\n\n{core._box_table(headers, body, total, aligns)}"


def run(root=None):
    print(render_report(root=root))
