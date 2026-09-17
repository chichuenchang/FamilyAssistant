# Any_Search — real-time web/vertical search + URL extract for the agent runtime.
#
# Backed by the AnySearch JSON-RPC API (https://api.anysearch.com/mcp). Mirrors
# Web_Reach's shape: pure logic (search/extract/subdomains) takes its transport as
# an injected `call` callable so it is unit-testable without network; the thin
# adapter (anysearch_call) does the real HTTP and is wired in by cli.py.
#
# Key priority: ANYSEARCH_API_KEY env var > skill-dir .env > anonymous (lower limits).
import html
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

CAP = 6000              # max chars handed back to the LLM (DeepSeek max_tokens is tight)
_TRUNC = "…[截断]"
_TIMEOUT = 25           # under _run_cli's 30s subprocess cap
ENDPOINT = "https://api.anysearch.com/mcp"

IMG_MAX = 5             # images per request (chat flood / subprocess timeout bound)
IMG_MAX_BYTES = 5 * 1024 * 1024
IMG_RETENTION_DAYS = 7  # downloads are re-fetchable: pruned on each fetch, not backed up
_IMG_TIMEOUT = 10
_IMG_TRIES = 3          # candidate URLs tried per wanted image
IMG_BUDGET = 60         # seconds across all downloads; keeps any-images under its CLI timeout
_BING_IMAGES = "https://www.bing.com/images/search?q="
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FamilyAssistant/1.0"

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


def parse_image_urls(raw):
    """Direct image URLs from a resource.image result: the bare `- https://…` lines
    (the `- **URL**:` lines are the hosting pages, not images)."""
    return re.findall(r"^- (https?://\S+)$", raw or "", flags=re.M)


def parse_bing_image_urls(page):
    """Original-image URLs (`murl`) from a Bing image-search HTML page."""
    return [html.unescape(u)
            for u in re.findall(r"murl&quot;:&quot;(.*?)&quot;", page or "")]


def sniff_image_ext(data):
    """Extension by magic bytes, only for types the transports can send; else None."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def _prune_old(dest_dir, retention_days):
    cutoff = time.time() - retention_days * 86400
    for f in dest_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _save_images(urls, want, *, download, dest_dir, stem, deadline):
    saved = []
    for url in urls[:want * _IMG_TRIES]:
        if len(saved) >= want or time.monotonic() >= deadline:
            break
        try:
            data = download(url)
        except Exception:                        # noqa: BLE001 — skip, try next candidate
            continue
        ext = sniff_image_ext(data)
        if not ext:
            continue
        out = dest_dir / f"{stem}_{len(saved)}{ext}"
        out.write_bytes(data)
        saved.append(out)
    return saved


def fetch_images(query, *, call, download, dest_dir, count=None, fallback=None,
                 rel=str, budget=IMG_BUDGET):
    """Search images, download up to `count` into dest_dir; returns one path per line.

    Primary source is AnySearch resource.image (stock photos); `fallback(query) -> [url]`
    runs only when the primary yields no saved image."""
    q = (query or "").strip()
    if not q:
        return "[错误] 空查询"
    try:
        want = max(1, min(int(count), IMG_MAX))
    except (TypeError, ValueError):
        want = 3
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    _prune_old(dest_dir, IMG_RETENTION_DAYS)
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", q).strip("_")[:40] or "image"
    stem = f"{int(time.time())}_{slug}"
    sources = [lambda: parse_image_urls(call("search", {
        "query": q, "domain": "resource", "sub_domain": "resource.image",
        "max_results": 10}))]
    if fallback:
        sources.append(lambda: fallback(q))
    deadline = time.monotonic() + budget
    for source in sources:
        try:
            urls = source()
        except Exception:                        # noqa: BLE001 — next source
            continue
        saved = _save_images(urls, want, download=download, dest_dir=dest_dir, stem=stem,
                             deadline=deadline)
        if saved:
            return "\n".join(rel(p) for p in saved)
    return "[错误] 没找到可用的图片"


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


# ── image adapters (real I/O) ──

def _resolve(host):
    return [ai[4][0] for ai in socket.getaddrinfo(host, None)]


def _check_public(url, resolve):
    """http(s) only and every resolved address global — result URLs are untrusted (SSRF)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"不允许的链接: {url}")
    for ip in resolve(parts.hostname):
        if not ipaddress.ip_address(ip.split("%")[0]).is_global:
            raise ValueError(f"非公网地址: {parts.hostname}")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl, _resolve)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req, timeout):
    return urllib.request.build_opener(_PublicRedirects).open(req, timeout=timeout)


def download_image(url, *, timeout=_IMG_TIMEOUT, max_bytes=IMG_MAX_BYTES, resolve=_resolve):
    """GET one image's bytes; raises on a non-public URL, oversize body, or HTTP failure."""
    _check_public(url, resolve)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _open(req, timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("图片过大")
    return data


def bing_image_urls(query, *, timeout=_IMG_TIMEOUT):
    """Keyless fallback: scrape Bing image search (covers people/products stock sites lack)."""
    req = urllib.request.Request(_BING_IMAGES + urllib.parse.quote(query),
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_bing_image_urls(resp.read().decode("utf-8", "replace"))
