# CLAUDE.md — claude-code-usage-per-project (ccupp)

## What this project is

A Claude Code status line + usage tracker. `ccupp.py` is a thin entry point: `main()`
dispatches by argv/TTY to one of four modes, each living in its own module:

1. **Status line** — bare `ccupp` piped from Claude Code's `statusLine` hook (stdin is not a TTY); `main()` reads JSON from stdin and delegates rendering to `ccupp_status_line.py`, which prints a 2-line HUD
2. **Usage report** — bare `ccupp` run in a terminal (TTY); `ccupp_report.py` prints a per-session table for the current project
3. **All-projects comparison** — `ccupp --all`; `ccupp_all.py` prints a per-project comparison table across every tracked project
4. **Export** — `ccupp --export`; `ccupp_export.py` writes `PROMPTS.md`

Each user-facing mode is its own module. `ccupp_core.py` holds only what is *shared*
across modes (data analysis, persistence, shared formatters). `ccupp_status_line.py`
holds status-line presentation. Keep mode-specific logic out of `ccupp_core.py`.

## File structure

```
ccupp.py                   main() dispatcher + install command
ccupp_status_line.py       Status line rendering (2-line HUD)
ccupp_core.py              Shared utilities, data analysis, persistence, table renderer
ccupp_report.py            Terminal usage report — current project (TTY mode)
ccupp_all.py               All-projects comparison report (--all)
ccupp_export.py            Prompt extraction and Markdown export (--export)
test_ccupp.py              Tests for ccupp.py (dispatch + install)
test_ccupp_status_line.py  Tests for ccupp_status_line.py
test_ccupp_core.py         Tests for ccupp_core.py
test_ccupp_report.py       Tests for ccupp_report.py
test_ccupp_all.py          Tests for ccupp_all.py
test_ccupp_export.py       Tests for ccupp_export.py
docs/                      Historical planning/spec docs — treat as context only
```

## Running tests

```bash
python3 -m unittest test_ccupp test_ccupp_status_line test_ccupp_core test_ccupp_report test_ccupp_all test_ccupp_export -v
```

No external dependencies for runtime or tests. The only network call is the LiteLLM pricing fetch in `_load_pricing_map()`, which is cached to disk and gated by tests via `_PRICING_CACHE`.

## Key invariants

**Snapshot storage path is `.ccupp/sessions/`** inside the project's Claude transcript directory. The test suite asserts this path. Don't rename it without updating tests.

**Token counting includes all four token types** — `input`, `output`, `cache_creation_input`, `cache_read_input` — matching ccusage. See `sum_unique_tokens()` in `ccupp_core.py`.

**Deduplication by `(message.id, requestId)`** with sidechain fallback to `message.id` alone. Claude Code streams multiple lines per request and may emit sidechain copies of the same message. Tie-breaking when keys collide (in `_should_replace`): non-sidechain wins over sidechain → larger token total wins → entry with `speed` info wins. `sum_unique_tokens` and `estimate_cost` share this dedup via `_dedupe_assistants`.

**The status line must never raise**. `ccupp_status_line.render()` always emits line 1, then wraps line-2 (project totals) rendering in `except Exception: pass` — if the status line crashes, Claude Code shows a blank line, which breaks the UI for the whole session.

**The `--export` and `--all` flags are handled before the TTY check** in `main()`. Order matters.

## Architecture decisions

**Why per-session snapshot files instead of a single accumulated counter?**
The status line fires after every assistant response, not once per session. A single counter would require atomic read-modify-write (race condition risk with concurrent sessions) and would double-count if re-run. One file per session, overwritten each render, is idempotent and concurrent-safe.

**Why is `workspace.project_dir` used for the project name instead of `dirname(transcript_path)`?**
`transcript_path` is inside `~/.claude/projects/...`, not the actual working directory. `workspace.project_dir` is the actual project folder.

**How is cost calculated?**
Modeled on ccusage's Auto mode:
- Live session: trust `total_cost_usd` from Claude Code's stdin.
- Per-entry: if the JSONL line has a `costUSD` field, use it directly.
- Otherwise compute from tokens × per-model LiteLLM rates, with the 200K-tier surcharges and the Fast-mode multiplier (`FAST_MULTIPLIER_OVERRIDES`).

Pricing is loaded from `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`, cached at `~/.ccupp/litellm-pricing.json` for 24h. On fetch failure: stale cache wins; otherwise falls back to `FALLBACK_PRICES` (per-family `opus`/`sonnet`/`haiku` rates) so first-run-offline still produces a sensible estimate. Tests must override `core._PRICING_CACHE` (see `_PricingIsolation` in `test_ccupp_core.py`) to keep the suite network-free.

## Adding features

- Shared formatting/data helpers belong in `ccupp_core.py` alongside `format_tokens`, `format_duration`, `_box_table`. Logic used by only one mode belongs in that mode's module, not `ccupp_core.py`
- Status-line-only presentation (bars, colors, line layout) belongs in `ccupp_status_line.py`
- New export filters go in the `UI_SLASH` set or `classify()` function in `ccupp_export.py`
- Every new function needs a test in the corresponding test file
- The status line output format (line 1 / line 2) is tested in `TestRenderLine1` / `TestRenderLine2` in `test_ccupp_status_line.py` — update those tests if the format changes
