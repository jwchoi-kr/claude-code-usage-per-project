#!/usr/bin/env python3
"""ccupp status line — renders the 2-line HUD from Claude Code's statusLine stdin JSON."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ccupp_core import project_totals, format_tokens, format_duration


DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"


def render_bar(pct, width=10):
    pct = max(0, min(100, int(pct or 0)))
    filled = pct * width // 100
    return "█" * filled + "░" * (width - filled)


def bar_color(pct):
    pct = int(pct or 0)
    if pct >= 90:
        return "\033[31m"
    if pct >= 70:
        return "\033[33m"
    return "\033[32m"


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


def render(data):
    """Full 2-line HUD. Line 1 always renders; line 2 is best-effort and never raises."""
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
    return "\n".join(lines)
