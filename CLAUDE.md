# CLAUDE.md — claude-code-usage-per-project (ccupp)

## What this project is

A Claude Code status line + usage tracker. `ccupp/cli.py` is a thin entry point: `main()`
dispatches by argv/TTY to one of six modes, each living in its own module:

1. **Status line** — bare `ccupp` piped from Claude Code's `statusLine` hook (stdin is not a TTY); `main()` reads JSON from stdin and delegates rendering to `ccupp/status_line.py`, which prints a 2-line HUD
2. **Usage report** — bare `ccupp` run in a terminal (TTY); `ccupp/report.py` prints a per-session table for the current project
3. **Daily breakdown** — `ccupp --daily`; `ccupp/daily.py` prints the current project's usage grouped by local calendar day
4. **Model breakdown** — `ccupp --model`; `ccupp/model.py` prints the current project's usage grouped by model
5. **All-projects comparison** — `ccupp --all`; `ccupp/all.py` prints a per-project comparison table across every tracked project
6. **Export** — `ccupp --export`; `ccupp/export.py` writes `PROMPTS.md`

Modes 2–4 are the same current-project scope sliced on a different axis: by session, by day, by model. The `--daily` and `--model` modes recompute from the raw transcripts (the per-session snapshots don't carry per-day or per-model splits), reusing the shared `aggregate_by_day` / `aggregate_by_model` helpers in `ccupp/core.py`.

Each user-facing mode is its own module. `ccupp/core.py` holds only what is *shared*
across modes (data analysis, persistence, shared formatters). `ccupp/status_line.py`
holds status-line presentation. Keep mode-specific logic out of `ccupp/core.py`.

## File structure

```
ccupp/
  __init__.py          Package marker
  __main__.py          `python -m ccupp` entry point
  cli.py               main() dispatcher + install command
  status_line.py       Status line rendering (2-line HUD)
  core.py              Shared utilities, data analysis, persistence, table renderer
  report.py            Terminal usage report — current project (TTY mode)
  daily.py             Per-day usage breakdown — current project (--daily)
  model.py             Per-model usage breakdown — current project (--model)
  all.py               All-projects comparison report (--all)
  export.py            Prompt extraction and Markdown export (--export)
tests/
  __init__.py          Package marker
  test_cli.py          Tests for cli.py (dispatch + install)
  test_status_line.py  Tests for status_line.py
  test_core.py         Tests for core.py
  test_report.py       Tests for report.py
  test_daily.py        Tests for daily.py
  test_model.py        Tests for model.py
  test_all.py          Tests for all.py
  test_export.py       Tests for export.py
docs/                  Historical planning/spec docs — treat as context only
```

## Running tests

```bash
python3 -m unittest tests.test_cli tests.test_status_line tests.test_core tests.test_report tests.test_daily tests.test_model tests.test_all tests.test_export -v
```

No external dependencies for runtime or tests. The only network call is the LiteLLM pricing fetch in `_load_pricing_map()`, which is cached to disk and gated by tests via `_PRICING_CACHE`.

## Key invariants

**Snapshot storage path is `.ccupp/sessions/`** inside the project's Claude transcript directory. The test suite asserts this path. Don't rename it without updating tests.

**Backfill snapshots are a cache keyed by transcript size, not a write-once file.** A backfill snapshot (`estimated: true`) records the source `.jsonl`'s byte size in `src_size`. On read, `_read_or_backfill_snapshot` recomputes it whenever the (append-only) transcript's size differs — including legacy snapshots with no `src_size`. Without this, a snapshot frozen mid-session permanently under-reports while the transcript keeps growing, so `ccupp`/`--all`/report would disagree with the recompute-based `--daily`/`--model`. Live snapshots (`estimated: false`) carry exact stdin cost/time and are deliberately **never** invalidated here — the status line overwrites them each render.

**Token counting includes all four token types** — `input`, `output`, `cache_creation_input`, `cache_read_input` — matching ccusage. See `sum_unique_tokens()` in `ccupp/core.py`.

**Deduplication by `(message.id, requestId)`** with sidechain fallback to `message.id` alone. Claude Code streams multiple lines per request and may emit sidechain copies of the same message. Tie-breaking when keys collide (in `_should_replace`): non-sidechain wins over sidechain → larger token total wins → entry with `speed` info wins. `sum_unique_tokens`, `estimate_cost`, `aggregate_by_day`, and `aggregate_by_model` all share this dedup via `_dedupe_assistants`, so per-day / per-model totals reconcile with the session totals.

**The status line must never raise**. `status_line.render()` always emits line 1, then wraps line-2 (project totals) rendering in `except Exception: pass` — if the status line crashes, Claude Code shows a blank line, which breaks the UI for the whole session.

**The `--help`/`-h`, `--version`/`-v`, `--export`, `--all`, `--daily`, and `--model` flags are handled before the TTY check** in `main()`. Order matters — a recognized flag must short-circuit before the `sys.stdin.isatty()` branch that picks status-line vs. report. `--help`/`--version` are checked first of all (before `install`) so they always win. `_version()` reads the installed package version via `importlib.metadata`, falling back to `"unknown"` — there is no hardcoded version constant to keep in sync with `pyproject.toml`.

**`--daily` buckets by *local* calendar date** (`_local_date` → `dt.astimezone()`), matching the local-time `DATE` column in the per-session report. Tests pin `TZ=UTC` + `time.tzset()` so date bucketing is deterministic regardless of the host timezone.

## Architecture decisions

**Why per-session snapshot files instead of a single accumulated counter?**
The status line fires after every assistant response, not once per session. A single counter would require atomic read-modify-write (race condition risk with concurrent sessions) and would double-count if re-run. One file per session, overwritten each render, is idempotent and concurrent-safe. A backfill snapshot for a *non-current* session is just a cache of `compute_backfill_snapshot(transcript)`; it is invalidated by `src_size` so it can't go stale while the session keeps streaming (see Key invariants).

**Why is `workspace.project_dir` used for the project name instead of `dirname(transcript_path)`?**
`transcript_path` is inside `~/.claude/projects/...`, not the actual working directory. `workspace.project_dir` is the actual project folder.

**How is cost calculated?**
Modeled on ccusage's Auto mode:
- Live session: trust `total_cost_usd` from Claude Code's stdin.
- Per-entry: if the JSONL line has a `costUSD` field, use it directly.
- Otherwise compute from tokens × per-model LiteLLM rates, with the 200K-tier surcharges and the Fast-mode multiplier (`FAST_MULTIPLIER_OVERRIDES`).

Pricing is loaded from `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`, cached at `~/.ccupp/litellm-pricing.json` for 24h. On fetch failure: stale cache wins; otherwise falls back to `FALLBACK_PRICES` (per-family `opus`/`sonnet`/`haiku` rates) so first-run-offline still produces a sensible estimate. Tests must override `core._PRICING_CACHE` (see `_PricingIsolation` in `tests/test_core.py`) to keep the suite network-free.

## Adding features

- Shared formatting/data helpers belong in `ccupp/core.py` alongside `format_tokens`, `format_duration`, `_box_table`. Logic used by only one mode belongs in that mode's module, not `ccupp/core.py`
- Status-line-only presentation (bars, colors, line layout) belongs in `ccupp/status_line.py`
- New export filters go in the `UI_SLASH` set or `classify()` function in `ccupp/export.py`
- A new current-project breakdown (a new grouping axis) follows the `ccupp/daily.py` / `ccupp/model.py` shape: a pure `aggregate_by_*` helper in `ccupp/core.py` (unit-tested against object lists), plus a thin module with `render_report(project_dir, cwd)` + `run()` that collects objects via `project_transcript_paths` and renders through `_box_table`
- Every new function needs a test in the corresponding test file. `tests/test_daily.py` and `tests/test_model.py` import shared fixtures (`_assistant`, `_write_jsonl`, `_PricingIsolation`) from `tests/test_core.py` — import only those helpers, never its `TestCase` classes, or `unittest` will re-collect and double-run them
- The status line output format (line 1 / line 2) is tested in `TestRenderLine1` / `TestRenderLine2` in `tests/test_status_line.py` — update those tests if the format changes
