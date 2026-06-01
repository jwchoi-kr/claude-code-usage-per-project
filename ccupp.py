#!/usr/bin/env python3
"""ccupp — Claude Code usage-per-project. Entry point: dispatches by argv/TTY to each mode."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json


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


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "install":
        return _install()
    if argv and argv[0] == "--export":
        import ccupp_export
        return ccupp_export.run(argv[1:])
    if argv and argv[0] == "--all":
        import ccupp_all
        return ccupp_all.run()
    if argv and argv[0] == "--daily":
        import ccupp_daily
        return ccupp_daily.run()
    if argv and argv[0] == "--model":
        import ccupp_model
        return ccupp_model.run()
    if sys.stdin.isatty():
        import ccupp_report
        return ccupp_report.run()
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = {}
    import ccupp_status_line
    print(ccupp_status_line.render(data))


if __name__ == "__main__":
    main()
