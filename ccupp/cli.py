#!/usr/bin/env python3
"""ccupp — Claude Code usage-per-project. Entry point: dispatches by argv/TTY to each mode."""
import json
import os
import sys


def _install():
    import shutil
    settings_path = os.path.expanduser("~/.claude/settings.json")
    ccupp_bin = shutil.which("ccupp") or os.path.abspath(sys.argv[0])
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
    else:
        settings = {}
    settings["statusLine"] = {"type": "command", "command": ccupp_bin, "padding": 0}
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"statusLine configured: {ccupp_bin}")
    print("Restart Claude Code to apply.")


def _version():
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("ccupp")
        except PackageNotFoundError:
            return "unknown"
    except ImportError:
        return "unknown"


def _help():
    return """ccupp — Claude Code usage per project

Usage:
  ccupp                     Current project usage, grouped by session (default)
  ccupp --daily             Current project usage, grouped by day
  ccupp --model             Current project usage, grouped by model
  ccupp --all               Compare usage across all tracked projects
  ccupp --export [-o FILE]  Export prompts to Markdown (default: PROMPTS.md)
  ccupp install             Configure Claude Code's statusLine to use ccupp
  ccupp --version, -v       Print version
  ccupp --help, -h          Show this help

When piped from Claude Code's statusLine hook (no TTY), ccupp prints the 2-line status line."""


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("--help", "-h"):
        print(_help())
        return
    if argv and argv[0] in ("--version", "-v"):
        print(f"ccupp {_version()}")
        return
    if argv and argv[0] == "install":
        return _install()
    if argv and argv[0] == "--export":
        from . import export
        return export.run(argv[1:])
    if argv and argv[0] == "--all":
        from . import all as all_mode
        return all_mode.run()
    if argv and argv[0] == "--daily":
        from . import daily
        return daily.run()
    if argv and argv[0] == "--model":
        from . import model
        return model.run()
    if sys.stdin.isatty():
        from . import report
        return report.run()
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {}
    from . import status_line
    print(status_line.render(data))
