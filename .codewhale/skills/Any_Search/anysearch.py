# Any_Search — real-time web/vertical search + URL extract for the agent runtime.
#
# Backed by the AnySearch JSON-RPC API (https://api.anysearch.com/mcp). Mirrors
# Web_Reach's shape: pure logic (search/extract/subdomains) takes its transport as
# an injected `call` callable so it is unit-testable without network; the thin
# adapter (anysearch_call) does the real HTTP and is wired in by cli.py.
#
# Key priority: ANYSEARCH_API_KEY env var > skill-dir .env > anonymous (lower limits).
import json
import os
import urllib.request
from pathlib import Path

CAP = 6000              # max chars handed back to the LLM (DeepSeek max_tokens is tight)
_TRUNC = "…[截断]"
_TIMEOUT = 25           # under _run_cli's 30s subprocess cap
ENDPOINT = "https://api.anysearch.com/mcp"

AVAILABLE_DOMAINS = [
    "general", "resource", "social_media", "finance", "academic", "legal",
    "health", "business", "security", "ip", "code", "energy",
    "environment", "agriculture", "travel", "film", "gaming",
]


# ── pure logic ──────────────────────────────────────────────

def trim(text, cap=CAP):
    """Cap text length; append a marker when truncated."""
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + _TRUNC


def parse_sdp(value):
    """Parse sub_domain_params: dict passthrough, JSON, or key=value,key2=value2."""
    if not value:
        return None
    if isinstance(value, dict):
        return value or None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed or None
    except (json.JSONDecodeError, TypeError):
        pass
    result = {}
    for pair in str(value).split(","):
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        k = k.strip()
        if k:
            result[k] = v.strip()      # empty value kept on purpose (required-but-blank params)
    return result or None


def search(query, *, call, domain=None, sub_domain=None,
           sub_domain_params=None, max_results=None):
    """General or vertical search; returns cleaned result text (markdown)."""
    q = (query or "").strip()
    if not q:
        return "[错误] 空查询"
    args = {"query": q}
    if domain:
        args["domain"] = domain
        if sub_domain:
            args["sub_domain"] = sub_domain
        sdp = parse_sdp(sub_domain_params)
        if sdp:
            args["sub_domain_params"] = sdp
    if max_results not in (None, ""):
        try:
            args["max_results"] = max(1, min(int(max_results), 10))
        except (TypeError, ValueError):
            pass
    try:
        raw = call("search", args)
    except Exception as e:                       # noqa: BLE001 — relay any failure
        return f"[错误] 搜索失败：{e}"
    raw = (raw or "").strip()
    if not raw:
        return "[错误] 没查到结果"
    return trim(raw)


def extract(url, *, call):
    """Fetch and extract one page's full content as markdown."""
    u = (url or "").strip()
    if not u:
        return "[错误] 空链接"
    try:
        raw = call("extract", {"url": u})
    except Exception as e:                       # noqa: BLE001
        return f"[错误] 抓取失败：{e}"
    raw = (raw or "").strip()
    if not raw:
        return "[错误] 没读到内容"
    return trim(raw)


def subdomains(domains, *, call):
    """List sub_domains/params for one or more vertical domains (discovery)."""
    if isinstance(domains, str):
        items = [d.strip() for d in domains.split(",") if d.strip()]
    else:
        items = [str(d).strip() for d in (domains or []) if str(d).strip()]
    if not items:
        return "[错误] 未指定 domain"
    args = {"domains": items} if len(items) > 1 else {"domain": items[0]}
    try:
        raw = call("get_sub_domains", args)
    except Exception as e:                       # noqa: BLE001
        return f"[错误] 查询失败：{e}"
    raw = (raw or "").strip()
    if not raw:
        return "[错误] 没查到子域"
    return trim(raw)


# ── network adapter (real I/O; injected into the pure logic by cli.py) ──

def _load_env():
    """Load ANYSEARCH_API_KEY from the skill-dir .env (utf-8-sig for Notepad BOM).

    Does NOT override an already-set process env var — env var wins, matching the
    documented priority (env var > .env file > anonymous)."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip().lstrip("﻿")
            v = v.strip().strip("\"'").strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    except OSError:
        pass


def _api_key():
    _load_env()
    return os.environ.get("ANYSEARCH_API_KEY", "")


def anysearch_call(tool_name, arguments, *, timeout=_TIMEOUT,
                   endpoint=ENDPOINT, api_key=None):
    """POST a JSON-RPC 2.0 tools/call; return the text result or raise on failure.

    Pure logic above catches the raise and turns it into a friendly [错误] string."""
    key = _api_key() if api_key is None else api_key
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "FamilyAssistant/1.0 (+any_search)"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(endpoint, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
    result = (data or {}).get("result", {})
    for item in result.get("content", []) or []:
        if item.get("type") == "text":
            return item.get("text", "")
    return json.dumps(result, ensure_ascii=False)
