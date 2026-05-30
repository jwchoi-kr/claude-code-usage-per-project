#!/usr/bin/env python3
"""ccupp — Claude Code usage-per-project status line."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from ccupp_core import project_totals, render_bar, bar_color, format_tokens, format_duration


DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"


def _tag(s):
    return f"{DIM}[{RESET}{s}{DIM}]{RESET}"


def render_line1(data):
    model = (data.get("model") or {}).get("display_name") or "?"
    effort = (data.get("effort") or {}).get("level")
    pct = (data.get("context_window") or {}).get("used_percentage")
    pct = int(pct) if pct is not None else 0
    head = f"{CYAN}{model}{RESET}"
    if effort:
        head += f"  {_tag(effort)}"
    bar = render_bar(pct)
    color = bar_color(pct)
    return f"{head}   {DIM}ctx{RESET} {color}{bar}{RESET} {pct}%"


def render_line2(project_name, totals):
    parts = [
        project_name,
        _tag(f"{totals['utterances']} msg"),
        _tag(f"{format_tokens(totals['tokens'])} tok"),
        _tag(f"${totals['cost_usd']:.2f}"),
        _tag(format_duration(totals['api_ms'])),
    ]
    return "  " + "  ".join(parts)


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--export":
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import ccupp_export
        return ccupp_export.run(argv[1:])
    if sys.stdin.isatty():
        import ccupp_report
        return ccupp_report.run()
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {}
    lines = [render_line1(data)]
    try:
        transcript_path = data.get("transcript_path")
        session_id = data.get("session_id")
        if transcript_path and session_id:
            workspace_cwd = (data.get("workspace") or {}).get("project_dir")
            project_name = os.path.basename(
                workspace_cwd or os.path.dirname(transcript_path)
            )
            cost = data.get("cost") or {}
            totals = project_totals(
                transcript_path,
                session_id,
                cost.get("total_cost_usd"),
                cost.get("total_api_duration_ms"),
                cwd=workspace_cwd,
            )
            lines.append(render_line2(project_name, totals))
    except Exception:
        pass  # never break the status line
    print("\n".join(lines))


if __name__ == "__main__":
    main()
