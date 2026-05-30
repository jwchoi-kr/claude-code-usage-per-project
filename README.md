# claude-code-usage-per-project (ccupp)

A Claude Code status line and usage tracker that shows per-project efficiency metrics — prompts, tokens, cost, and API time — across all sessions for the current working directory.

## What it does

**Three modes across three scripts:**

### 1. Status line (piped from Claude Code)

When Claude Code invokes it as a `statusLine` command, reads JSON from stdin and prints a 2-line HUD:

```
◈ Opus 4.7 · xhigh        ctx ███░░░░░░░ 38%
  ▸ myproject  💬 12  ·  84.2k tok  ·  $1.83  ·  ⏱ 6m12s
```

- **Line 1**: Model name (cyan) · effort level · context-window usage bar (green/yellow/red at 70%/90%)
- **Line 2**: Project name · cumulative human prompts · unique tokens · cost · total API response time

### 2. Usage report (run directly in terminal)

```
$ ccupp
```

Prints a boxed per-session table for the project in the current directory:

```
myproject — Claude Code usage (per project)

┌──────────────┬─────────────────┬─────────┬────────┬───────┬─────────┐
│ SESSION      │ DATE            │ PROMPTS │ TOKENS │  COST │    TIME │
├──────────────┼─────────────────┼─────────┼────────┼───────┼─────────┤
│ 9beb422a     │ 2026-05-30 12:00│      12 │  84.2k │ $1.83 │  6m12s  │
│ 0ec212f4~    │ 2026-05-29 09:15│       8 │  51.0k │ $0.94 │  4m30s  │
├──────────────┼─────────────────┼─────────┼────────┼───────┼─────────┤
│ TOTAL        │                 │      20 │ 135.2k │ $2.77 │ 10m42s  │
└──────────────┴─────────────────┴─────────┴────────┴───────┴─────────┘
  ~ = backfill estimate (cost·time approximate)
```

Sessions before installation are lazily backfilled on first run (`~` marker).

### 3. Prompt export (`ccupp --export`)

```
$ ccupp --export
✅ ./PROMPTS.md
   3 sessions · 47 prompts
```

Exports every human prompt you typed (filtering out tool results, system messages, UI slash commands like `/usage` or `/model`, interrupt markers) to `PROMPTS.md` as a chronological Markdown document — useful as a portfolio artifact.

## Installation

**Requirements:** Python 3.8+ (stdlib only, no dependencies)

**1. Clone the repo:**

```bash
git clone https://github.com/yourusername/claude-code-usage-per-project.git ~/tools/ccupp
```

**2. Wire up the status line** in `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/python3 /path/to/ccupp/ccupp.py",
    "padding": 0
  }
}
```

Use a pinned Python path (e.g. `/Users/you/.pyenv/shims/python3`) to avoid PATH issues.

**3. Optional: add a shell alias** for the report and export commands:

```bash
alias ccupp='python3 /path/to/ccupp/ccupp.py'
```

## How it works

### Snapshot files

The status line fires after every assistant response (potentially dozens of times per session). Rather than accumulating into a single counter, each session writes its own snapshot file:

```
~/.claude/projects/<project-dir>/.ccupp/sessions/<session_id>.json
{"tokens": 84200, "utterances": 12, "cost_usd": 1.83, "api_ms": 372000, "estimated": false}
```

- **Current session**: rewritten each render with exact values from stdin (`cost.total_cost_usd`, `cost.total_api_duration_ms`)
- **Past sessions**: loaded from their snapshot; backfilled on first encounter if missing
- **Project total**: sum of all snapshot files

This design is idempotent (re-running never double-counts) and safe for concurrent sessions.

### Token counting

Tokens are summed as `input + cache_creation + cache_read + output` — all four types — matching [ccusage](https://github.com/ryoppippi/ccusage). Cache reads inflate the displayed number, but they're real tokens you pay for, so the figure stays comparable across tools.

Deduplication uses `(message.id, requestId)` as the primary key with a sidechain fallback on `message.id` alone (subagent copies of the same message collapse to one entry). On key collisions, non-sidechain wins over sidechain, then larger token total wins, then the entry with `speed` info wins — same tie-breaking as ccusage.

Subagent (sidechain) tokens are included; they represent real work done by the model on your behalf.

### Human prompt filter

An utterance counts only if it is:
- `type: "user"`
- Not `isMeta` or `isSidechain`
- Not a tool result block
- Not a UI slash command (`/usage`, `/model`, `/clear`, etc.) or local-command wrapper
- Not an interrupt marker

Deduplicated by `promptId` (Claude Code can emit the same prompt ID multiple times for streaming).

### Backfill estimation

Sessions from before installation have no exact cost/time data. Time is estimated from message timestamps. Cost uses ccusage-style *Auto* mode:

- If a transcript entry has a `costUSD` field, that value wins.
- Otherwise cost = tokens × per-model rate from the [LiteLLM pricing table](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json), with the 200K-tier surcharges and the Fast-mode multiplier applied.

The LiteLLM JSON is fetched on first use and cached at `~/.ccupp/litellm-pricing.json` for 24h. If the network is unreachable on first run, ccupp falls back to a built-in per-family table (`opus`/`sonnet`/`haiku`). The live session always uses the exact `cost.total_cost_usd` Claude Code provides via stdin — pricing-table inaccuracies only affect backfilled sessions.

Backfilled sessions are marked with `~` and `"estimated": true`.

## Running tests

```bash
python3 -m unittest test_ccupp test_ccupp_core test_ccupp_report test_ccupp_export -v
```

All tests use stdlib `unittest`, `tempfile`, and `shutil` — no external dependencies and no network (pricing is injected via `core._PRICING_CACHE` in `_PricingIsolation`).

## Files

| File | Purpose |
|------|---------|
| `ccupp.py` | Status line rendering and `main()` dispatcher |
| `ccupp_core.py` | Shared utilities, data analysis, and persistence |
| `ccupp_report.py` | Terminal usage report (`ccupp` command) |
| `ccupp_export.py` | Prompt extraction and Markdown rendering (`ccupp --export`) |
| `test_ccupp.py` | Tests for `ccupp.py` |
| `test_ccupp_core.py` | Tests for `ccupp_core.py` |
| `test_ccupp_report.py` | Tests for `ccupp_report.py` |
| `test_ccupp_export.py` | Tests for `ccupp_export.py` |
