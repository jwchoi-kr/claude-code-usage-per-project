"""ccupp_core — shared utilities, data analysis, and persistence for ccupp."""
import os
import re
import json
import glob
import time
import hashlib
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
PRICING_CACHE_TTL = 24 * 3600
PRICING_FETCH_TIMEOUT = 10
TIER_THRESHOLD = 200_000

# Per-model fast-mode multipliers, mirroring ccusage's fast-multiplier-overrides.
FAST_MULTIPLIER_OVERRIDES = {
    "claude-opus-4-6": 6.0,
    "claude-opus-4-7": 6.0,
    "claude-opus-4-8": 2.0,
}

# Per-million-token fallback (USD) when LiteLLM JSON is unreachable on first run.
FALLBACK_PRICES = {
    "opus":   {"in": 15.0, "out": 75.0, "cc": 18.75, "cr": 1.5},
    "sonnet": {"in": 3.0,  "out": 15.0, "cc": 3.75,  "cr": 0.3},
    "haiku":  {"in": 1.0,  "out": 5.0,  "cc": 1.25,  "cr": 0.1},
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


def _usage_total(o):
    u = ((o.get("message") or {}).get("usage")) or {}
    return (
        int(u.get("input_tokens") or 0)
        + int(u.get("output_tokens") or 0)
        + int(u.get("cache_creation_input_tokens") or 0)
        + int(u.get("cache_read_input_tokens") or 0)
    )


def _should_replace(cand, existing):
    cs = bool(cand.get("isSidechain"))
    es = bool(existing.get("isSidechain"))
    if cs != es:
        return es  # non-sidechain wins over sidechain
    ct = _usage_total(cand)
    et = _usage_total(existing)
    if ct != et:
        return ct > et
    cu = ((cand.get("message") or {}).get("usage")) or {}
    eu = ((existing.get("message") or {}).get("usage")) or {}
    return cu.get("speed") is not None and eu.get("speed") is None


def _dedupe_assistants(objs):
    """ccusage-style dedup: (message.id, requestId) primary, sidechain falls back to message.id."""
    by_exact = {}
    by_msg = {}
    kept = []
    for o in objs:
        if o.get("type") != "assistant":
            continue
        msg = o.get("message") or {}
        mid = msg.get("id")
        rid = o.get("requestId")
        is_sc = bool(o.get("isSidechain"))

        idx = None
        if mid is not None:
            exact = (mid, rid)
            if exact in by_exact:
                idx = by_exact[exact]
            else:
                msg_idx = by_msg.get(mid)
                if msg_idx is not None and (is_sc or bool(kept[msg_idx].get("isSidechain"))):
                    idx = msg_idx
        elif rid is not None:
            exact = (None, rid)
            if exact in by_exact:
                idx = by_exact[exact]

        if idx is not None:
            if _should_replace(o, kept[idx]):
                kept[idx] = o
            continue

        kept.append(o)
        new_idx = len(kept) - 1
        if mid is not None:
            by_exact[(mid, rid)] = new_idx
            by_msg[mid] = new_idx
        elif rid is not None:
            by_exact[(None, rid)] = new_idx
    return kept


def sum_unique_tokens(objs):
    total = 0
    for o in _dedupe_assistants(objs):
        u = ((o.get("message") or {}).get("usage")) or {}
        total += int(u.get("input_tokens") or 0)
        total += int(u.get("cache_creation_input_tokens") or 0)
        total += int(u.get("cache_read_input_tokens") or 0)
        total += int(u.get("output_tokens") or 0)
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


# ---- LiteLLM pricing (runtime fetch + 24h disk cache) ----

_PRICING_CACHE = None  # in-process memo


def _pricing_cache_path():
    return os.path.join(_ccupp_home(), "litellm-pricing.json")


def _fetch_litellm():
    try:
        with urllib.request.urlopen(LITELLM_PRICING_URL, timeout=PRICING_FETCH_TIMEOUT) as r:
            raw = r.read().decode("utf-8")
        json.loads(raw)  # validate
        return raw
    except (urllib.error.URLError, ValueError, OSError):
        return None


def _read_cache_if_fresh():
    path = _pricing_cache_path()
    try:
        st = os.stat(path)
    except OSError:
        return None
    if time.time() - st.st_mtime >= PRICING_CACHE_TTL:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _read_cache_stale():
    try:
        with open(_pricing_cache_path(), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _save_pricing_cache(raw):
    path = _pricing_cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except OSError:
        pass


def _opt_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_litellm_entry(e):
    if not isinstance(e, dict):
        return None
    inp = e.get("input_cost_per_token")
    out = e.get("output_cost_per_token")
    if inp is None and out is None:
        return None
    return {
        "input": float(inp or 0),
        "output": float(out or 0),
        "cache_create": float(e.get("cache_creation_input_token_cost") or 0),
        "cache_read": float(e.get("cache_read_input_token_cost") or 0),
        "input_above_200k": _opt_float(e.get("input_cost_per_token_above_200k_tokens")),
        "output_above_200k": _opt_float(e.get("output_cost_per_token_above_200k_tokens")),
        "cache_create_above_200k": _opt_float(
            e.get("cache_creation_input_token_cost_above_200k_tokens")
        ),
        "cache_read_above_200k": _opt_float(
            e.get("cache_read_input_token_cost_above_200k_tokens")
        ),
    }


def _parse_pricing_json(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, entry in data.items():
        p = _parse_litellm_entry(entry)
        if p is not None:
            out[name] = p
    return out


def _load_pricing_map():
    """Memoized. Network-free when disk cache is < 24h old."""
    global _PRICING_CACHE
    if _PRICING_CACHE is not None:
        return _PRICING_CACHE
    raw = _read_cache_if_fresh()
    if raw is None:
        fetched = _fetch_litellm()
        if fetched is not None:
            _save_pricing_cache(fetched)
            raw = fetched
        else:
            raw = _read_cache_stale()
    _PRICING_CACHE = _parse_pricing_json(raw)
    return _PRICING_CACHE


def _reset_pricing_cache():
    """Test hook."""
    global _PRICING_CACHE
    _PRICING_CACHE = None


def _model_candidates(model):
    if not model:
        return []
    cands = [model]
    parts = model.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        cands.append(parts[0])  # strip -YYYYMMDD alias
    if "/" in model:
        cands.append(model.split("/", 1)[1])  # strip provider prefix
    return cands


def _find_pricing(model, pricing_map):
    for c in _model_candidates(model):
        if c in pricing_map:
            return pricing_map[c]
    return None


def _fast_multiplier(model):
    if not model:
        return 1.0
    for prefix, mult in FAST_MULTIPLIER_OVERRIDES.items():
        if model.startswith(prefix):
            return mult
    return 1.0


def _fallback_pricing(model):
    m = (model or "").lower()
    for fam in ("opus", "sonnet", "haiku"):
        if fam in m:
            p = FALLBACK_PRICES[fam]
            return {
                "input": p["in"] / 1_000_000,
                "output": p["out"] / 1_000_000,
                "cache_create": p["cc"] / 1_000_000,
                "cache_read": p["cr"] / 1_000_000,
                "input_above_200k": None,
                "output_above_200k": None,
                "cache_create_above_200k": None,
                "cache_read_above_200k": None,
            }
    return None


def _tiered(tokens, base, above):
    tokens = int(tokens or 0)
    if tokens <= 0:
        return 0.0
    if above is not None and tokens > TIER_THRESHOLD:
        return TIER_THRESHOLD * base + (tokens - TIER_THRESHOLD) * above
    return tokens * base


def _calc_cost_from_tokens(model, usage, pricing_map):
    p = _find_pricing(model, pricing_map) or _fallback_pricing(model)
    if p is None:
        return 0.0
    cost = (
        _tiered(usage.get("input_tokens"), p["input"], p["input_above_200k"])
        + _tiered(usage.get("output_tokens"), p["output"], p["output_above_200k"])
        + _tiered(
            usage.get("cache_creation_input_tokens"),
            p["cache_create"],
            p["cache_create_above_200k"],
        )
        + _tiered(
            usage.get("cache_read_input_tokens"),
            p["cache_read"],
            p["cache_read_above_200k"],
        )
    )
    speed = usage.get("speed")
    if isinstance(speed, str) and speed.lower() == "fast":
        cost *= _fast_multiplier(model)
    return cost


def estimate_cost(objs):
    """ccusage 'Auto' mode: prefer per-entry costUSD when present, else compute from tokens."""
    pricing = _load_pricing_map()
    total = 0.0
    for o in _dedupe_assistants(objs):
        cost = o.get("costUSD")
        if cost is not None:
            try:
                total += float(cost)
                continue
            except (TypeError, ValueError):
                pass
        msg = o.get("message") or {}
        total += _calc_cost_from_tokens(msg.get("model"), msg.get("usage") or {}, pricing)
    return total


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


# ---- project identity (cross-rename) ----

_REMOTE_PREFIX = re.compile(r"^(https?|ssh|git)://", re.I)


def _ccupp_home():
    return os.environ.get("CCUPP_HOME") or os.path.join(os.path.expanduser("~"), ".ccupp")


def _registry_path():
    return os.path.join(_ccupp_home(), "registry.json")


def _load_registry():
    data = _read_json(_registry_path())
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"version": 1, "projects": {}}
    return data


def _save_registry(reg):
    _write_json(_registry_path(), reg)


def _register_dir(identity, transcript_dir, display_name=None):
    if not identity or not transcript_dir:
        return
    reg = _load_registry()
    entry = reg["projects"].setdefault(identity, {"transcript_dirs": []})
    dirs = entry.setdefault("transcript_dirs", [])
    norm = os.path.abspath(transcript_dir)
    if norm not in dirs:
        dirs.append(norm)
    if display_name and not entry.get("display_name"):
        entry["display_name"] = display_name
    entry["last_seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _save_registry(reg)


def _dirs_for_identity(identity):
    if not identity:
        return []
    reg = _load_registry()
    entry = (reg.get("projects") or {}).get(identity) or {}
    return [d for d in (entry.get("transcript_dirs") or []) if isinstance(d, str)]


def _git(cwd, args):
    try:
        r = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _normalize_remote(url):
    u = (url or "").strip().lower()
    if not u:
        return ""
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("git@"):
        u = u[4:].replace(":", "/", 1)
    u = _REMOTE_PREFIX.sub("", u)
    return u.rstrip("/")


def project_identity(cwd):
    """Stable project ID across folder renames. None if no git info."""
    if not cwd or not isinstance(cwd, str) or not os.path.isdir(cwd):
        return None
    url = _git(cwd, ["config", "--get", "remote.origin.url"])
    if url:
        norm = _normalize_remote(url)
        if norm:
            return "remote:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
    first = _git(cwd, ["rev-list", "--max-parents=0", "HEAD"])
    if first:
        shas = sorted(s for s in first.split() if s)
        if shas:
            return "commit:" + shas[0][:16]
    return None


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


def _accumulate_dir_snaps(project_dir, skip_session_id, snaps):
    sessions_dir = os.path.join(project_dir, ".ccupp", "sessions")
    for tp in glob.glob(os.path.join(project_dir, "*.jsonl")):
        stem = os.path.splitext(os.path.basename(tp))[0]
        if stem == skip_session_id or stem in snaps:
            continue
        snap_path = os.path.join(sessions_dir, stem + ".json")
        snap = _read_json(snap_path)
        if snap is None:
            snap = compute_backfill_snapshot(tp)
            _write_json(snap_path, snap)
        snaps[stem] = snap
    for sp in glob.glob(os.path.join(sessions_dir, "*.json")):
        stem = os.path.splitext(os.path.basename(sp))[0]
        if stem == skip_session_id or stem in snaps:
            continue
        snap = _read_json(sp)
        if snap:
            snaps[stem] = snap


def project_totals(transcript_path, session_id, total_cost_usd, total_api_ms, cwd=None):
    project_dir = os.path.dirname(transcript_path)
    sessions_dir = os.path.join(project_dir, ".ccupp", "sessions")
    snaps = {}

    # current session: exact live snapshot, overwritten each render
    snaps[session_id] = compute_live_snapshot(transcript_path, total_cost_usd, total_api_ms)
    _write_json(os.path.join(sessions_dir, session_id + ".json"), snaps[session_id])

    identity = project_identity(cwd) if cwd else None
    if identity:
        display = None
        try:
            display = os.path.basename(os.path.abspath(cwd).rstrip("/")) or None
        except (OSError, TypeError):
            pass
        _register_dir(identity, project_dir, display_name=display)
        dirs = _dirs_for_identity(identity)
        if project_dir not in dirs:
            dirs.append(project_dir)
    else:
        dirs = [project_dir]

    for d in dirs:
        _accumulate_dir_snaps(d, session_id if d == project_dir else None, snaps)

    totals = {"tokens": 0, "utterances": 0, "cost_usd": 0.0, "api_ms": 0}
    for s in snaps.values():
        totals["tokens"] += int(s.get("tokens") or 0)
        totals["utterances"] += int(s.get("utterances") or 0)
        totals["cost_usd"] += float(s.get("cost_usd") or 0.0)
        totals["api_ms"] += int(s.get("api_ms") or 0)
    return totals
