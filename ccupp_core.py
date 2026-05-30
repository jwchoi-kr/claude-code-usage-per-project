"""ccupp_core — shared utilities, data analysis, and persistence for ccupp."""
import os
import json
import glob
from datetime import datetime

# Rough Anthropic list prices, USD per 1M tokens. Approximate; edit as prices change.
PRICES = {
    "opus":   {"in": 15.0, "out": 75.0, "cache_write": 18.75, "cache_read": 1.5},
    "sonnet": {"in": 3.0,  "out": 15.0, "cache_write": 3.75,  "cache_read": 0.3},
    "haiku":  {"in": 1.0,  "out": 5.0,  "cache_write": 1.25,  "cache_read": 0.1},
}

SLASH_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-caveat>",
)


def format_tokens(n):
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def format_duration(ms):
    secs = int(ms or 0) // 1000
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


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


def iter_jsonl(path):
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (ValueError, TypeError):
                continue


def sum_unique_tokens(objs):
    seen = {}
    for o in objs:
        if o.get("type") != "assistant":
            continue
        usage = (o.get("message") or {}).get("usage") or {}
        rid = o.get("requestId")
        key = rid if rid is not None else id(o)
        seen[key] = usage  # last usage wins for a given request
    total = 0
    for usage in seen.values():
        total += int(usage.get("input_tokens") or 0)
        total += int(usage.get("cache_creation_input_tokens") or 0)
        total += int(usage.get("output_tokens") or 0)
    return total


def _is_human_prompt(o):
    if o.get("type") != "user":
        return False
    if o.get("isMeta") or o.get("isSidechain"):
        return False
    content = (o.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    elif isinstance(content, str):
        text = content
    else:
        return False
    text = text.strip()
    if not text:
        return False
    if text.startswith(SLASH_PREFIXES):
        return False
    return True


def count_utterances(objs):
    seen = set()
    count = 0
    for o in objs:
        if not _is_human_prompt(o):
            continue
        pid = o.get("promptId")
        if pid is not None:
            if pid in seen:
                continue
            seen.add(pid)
        count += 1
    return count


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def estimate_api_ms(objs):
    objs = list(objs)
    first_idx = {}
    last_ts = {}
    for i, o in enumerate(objs):
        if o.get("type") == "assistant" and o.get("requestId"):
            rid = o["requestId"]
            if rid not in first_idx:
                first_idx[rid] = i
            if o.get("timestamp"):
                last_ts[rid] = o["timestamp"]
    total_ms = 0
    for rid, fi in first_idx.items():
        j = fi - 1
        while j >= 0 and objs[j].get("type") == "assistant":
            j -= 1
        if j < 0:
            continue
        start = _parse_ts(objs[j].get("timestamp"))
        end = _parse_ts(last_ts.get(rid))
        if start and end:
            d = (end - start).total_seconds()
            if d > 0:
                total_ms += int(d * 1000)
    return total_ms


def price_for_model(model_id):
    mid = (model_id or "").lower()
    for key in ("opus", "sonnet", "haiku"):
        if key in mid:
            return PRICES[key]
    return PRICES["sonnet"]


def estimate_cost(objs):
    seen = {}
    for o in objs:
        if o.get("type") != "assistant":
            continue
        msg = o.get("message") or {}
        rid = o.get("requestId")
        key = rid if rid is not None else id(o)
        seen[key] = (msg.get("model"), msg.get("usage") or {})
    cost = 0.0
    for model, usage in seen.values():
        p = price_for_model(model)
        cost += (
            int(usage.get("input_tokens") or 0) * p["in"]
            + int(usage.get("cache_creation_input_tokens") or 0) * p["cache_write"]
            + int(usage.get("cache_read_input_tokens") or 0) * p["cache_read"]
            + int(usage.get("output_tokens") or 0) * p["out"]
        ) / 1_000_000
    return cost


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def compute_live_snapshot(transcript_path, total_cost_usd, total_api_ms):
    objs = list(iter_jsonl(transcript_path))
    return {
        "tokens": sum_unique_tokens(objs),
        "utterances": count_utterances(objs),
        "cost_usd": float(total_cost_usd or 0.0),
        "api_ms": int(total_api_ms or 0),
        "estimated": False,
    }


def compute_backfill_snapshot(transcript_path):
    objs = list(iter_jsonl(transcript_path))
    return {
        "tokens": sum_unique_tokens(objs),
        "utterances": count_utterances(objs),
        "cost_usd": estimate_cost(objs),
        "api_ms": estimate_api_ms(objs),
        "estimated": True,
    }


def project_totals(transcript_path, session_id, total_cost_usd, total_api_ms):
    project_dir = os.path.dirname(transcript_path)
    sessions_dir = os.path.join(project_dir, ".ccupp", "sessions")
    snaps = {}

    # current session: exact live snapshot, overwritten each render
    snaps[session_id] = compute_live_snapshot(transcript_path, total_cost_usd, total_api_ms)
    _write_json(os.path.join(sessions_dir, session_id + ".json"), snaps[session_id])

    # other transcripts: load snapshot or lazily backfill
    for tp in glob.glob(os.path.join(project_dir, "*.jsonl")):
        stem = os.path.splitext(os.path.basename(tp))[0]
        if stem == session_id:
            continue
        snap_path = os.path.join(sessions_dir, stem + ".json")
        snap = _read_json(snap_path)
        if snap is None:
            snap = compute_backfill_snapshot(tp)
            _write_json(snap_path, snap)
        snaps[stem] = snap

    # snapshots whose transcript no longer exists still count
    for sp in glob.glob(os.path.join(sessions_dir, "*.json")):
        stem = os.path.splitext(os.path.basename(sp))[0]
        if stem in snaps:
            continue
        snap = _read_json(sp)
        if snap:
            snaps[stem] = snap

    totals = {"tokens": 0, "utterances": 0, "cost_usd": 0.0, "api_ms": 0}
    for s in snaps.values():
        totals["tokens"] += int(s.get("tokens") or 0)
        totals["utterances"] += int(s.get("utterances") or 0)
        totals["cost_usd"] += float(s.get("cost_usd") or 0.0)
        totals["api_ms"] += int(s.get("api_ms") or 0)
    return totals
