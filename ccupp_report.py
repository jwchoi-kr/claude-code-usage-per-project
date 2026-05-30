"""ccupp_report — terminal usage report for ccupp."""
import os
import sys
import glob

from ccupp_core import (
    iter_jsonl, _parse_ts, _read_json, _write_json,
    compute_backfill_snapshot, format_tokens, format_duration,
    project_identity, _dirs_for_identity,
)


def _first_timestamp(transcript_path):
    for o in iter_jsonl(transcript_path):
        ts = o.get("timestamp")
        if ts:
            return ts
    return None


def collect_session_rows(project_dir, identity=None):
    """Per-session snapshots for a project (backfilling missing ones), each with first_ts.

    If `identity` is provided, also aggregates snapshots from every transcript dir
    registered under the same project identity (cross-rename support).
    """
    dirs = [project_dir] if project_dir else []
    if identity:
        for d in _dirs_for_identity(identity):
            if d not in dirs:
                dirs.append(d)
    rows = {}
    for d in dirs:
        sessions_dir = os.path.join(d, ".ccupp", "sessions")
        for tp in glob.glob(os.path.join(d, "*.jsonl")):
            sid = os.path.splitext(os.path.basename(tp))[0]
            if sid in rows:
                continue
            snap_path = os.path.join(sessions_dir, sid + ".json")
            snap = _read_json(snap_path)
            if snap is None:
                snap = compute_backfill_snapshot(tp)
                _write_json(snap_path, snap)
            rows[sid] = {"sid": sid, "first_ts": _first_timestamp(tp), "snap": snap}
        for sp in glob.glob(os.path.join(sessions_dir, "*.json")):
            sid = os.path.splitext(os.path.basename(sp))[0]
            if sid in rows:
                continue
            snap = _read_json(sp)
            if snap:
                rows[sid] = {"sid": sid, "first_ts": None, "snap": snap}
    return list(rows.values())


def _fmt_date(ts):
    d = _parse_ts(ts)
    return d.astimezone().strftime("%Y-%m-%d %H:%M") if d else "—"


_DIM = "\033[2m"
_RESET = "\033[0m"


def _box_table(headers, body, total, aligns):
    cols = list(zip(*([headers] + body + [total])))
    widths = [max(len(c) for c in col) for col in cols]
    bar = f"{_DIM}│{_RESET}"

    def row(cells):
        out = [c.ljust(w) if a == "left" else c.rjust(w)
               for c, w, a in zip(cells, widths, aligns)]
        return f"{bar} " + f" {bar} ".join(out) + f" {bar}"

    def rule(left, mid, right):
        return f"{_DIM}{left}{mid.join('─' * (w + 2) for w in widths)}{right}{_RESET}"

    lines = [rule("╭", "┬", "╮"), row(headers), rule("├", "┼", "┤")]
    lines += [row(b) for b in body]
    lines += [rule("├", "┼", "┤"), row(total), rule("╰", "┴", "╯")]
    return "\n".join(lines)


def render_report(project_dir=None, cwd=None):
    cwd = cwd or os.getcwd()
    root = os.path.expanduser("~/.claude/projects")
    if project_dir is None:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import ccupp_export
        project_dir = ccupp_export.find_project_dir(cwd, root)
    project_name = os.path.basename(os.path.abspath(cwd).rstrip("/")) or cwd
    title = f"{project_name} — Claude Code usage (per project)"

    identity = project_identity(cwd) if cwd else None
    rows = collect_session_rows(project_dir, identity=identity) if (project_dir or identity) else []
    rows = [r for r in rows
            if int(r["snap"].get("utterances") or 0) or int(r["snap"].get("tokens") or 0)]
    if not rows:
        return f"{title}\n\n  No tracked sessions for this folder.\n  (searched: {root})"
    rows.sort(key=lambda r: (r["first_ts"] is None, r["first_ts"] or ""))

    headers = ["SESSION", "DATE", "USER_MSG", "TOKENS", "COST", "TIME"]
    aligns = ["left", "left", "right", "right", "right", "right"]
    body = []
    tu = tt = tms = 0
    tc = 0.0
    any_est = False
    for r in rows:
        s = r["snap"]
        est = bool(s.get("estimated"))
        any_est = any_est or est
        u = int(s.get("utterances") or 0)
        t = int(s.get("tokens") or 0)
        c = float(s.get("cost_usd") or 0.0)
        ms = int(s.get("api_ms") or 0)
        tu += u
        tt += t
        tc += c
        tms += ms
        body.append([
            r["sid"][:8] + ("~" if est else ""),
            _fmt_date(r["first_ts"]),
            str(u),
            format_tokens(t),
            f"${c:.2f}",
            format_duration(ms),
        ])
    total = ["TOTAL", "", str(tu), format_tokens(tt), f"${tc:.2f}", format_duration(tms)]
    out = f"{title}\n\n{_box_table(headers, body, total, aligns)}"
    if any_est:
        out += "\n  ~ = backfill estimate (cost·time approximate)"
    return out


def run(project_dir=None, cwd=None):
    print(render_report(project_dir=project_dir, cwd=cwd))
