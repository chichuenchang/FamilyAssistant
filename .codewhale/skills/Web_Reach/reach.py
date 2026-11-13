# Web_Reach — read-only web / news / YouTube fetch for the agent runtime.
#
# Pure logic (trim/parse_vtt/summarize_youtube/web_search/web_read) takes its I/O as
# injected callables so it is unit-testable without network. The thin adapters
# (serper_search/parallel_search/jina_fetch/ytdlp_subs/ytdlp_meta) do the real I/O,
# wired in by cli.py.
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

CAP = 6000              # max chars handed back to the LLM (DeepSeek max_tokens is tight)
_TRUNC = "…[截断]"
_JINA_TIMEOUT = 20      # under _run_cli's 30s subprocess cap
_SUBS_TIMEOUT = 18      # subs + meta worst case 26s, still under 30s
_META_TIMEOUT = 8
_SEARCH_TIMEOUT = 20    # per provider; Serper + Parallel fallback stays under the 45s CLI cap
_JINA_READER = "https://r.jina.ai/"                  # keyless; cleans any URL to markdown
_SERPER_URL = "https://google.serper.dev/search"     # Google results (paid, serper.dev)
_PARALLEL_URL = "https://api.parallel.ai/v1/search"  # fallback (paid, platform.parallel.ai)
_RESULTS = 10
# Parallel snippets are page excerpts; it overshoots its own cap (~1000 back for 500), so cut
# here too — else 10 results blow CAP and the tail results are lost.
_PARALLEL_SNIPPET = 500

_TAG_RE = re.compile(r"<[^>]+>")   # <c>…</c>, inline <00:00:01.500> word timings
_MD_HEADING_RE = re.compile(r"(?m)^#+\s*")   # Parallel excerpts are markdown; one-line snippets
_WS_RE = re.compile(r"\s+")


# ── pure logic ──────────────────────────────────────────────

def trim(text, cap=CAP):
    """Cap text length; append a marker when truncated."""
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + _TRUNC


def parse_vtt(vtt):
    """Turn a .vtt subtitle blob into plain, de-duplicated transcript text."""
    out = []
    prev = None
    for raw in (vtt or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("NOTE", "Kind:", "Language:", "STYLE")):
            continue
        if "-->" in line:           # timestamp line (carries any cue settings too)
            continue
        if line.isdigit():          # numeric cue index
            continue
        line = _TAG_RE.sub("", line).strip()
        if not line:
            continue
        if line == prev:            # auto-subs repeat the rolling caption — drop dupes
            continue
        out.append(line)
        prev = line
    return " ".join(out)


def summarize_youtube(url, *, get_subs, get_meta):
    """Prefer the transcript; fall back to title+description; else an error string."""
    subs = get_subs(url)
    if subs:
        transcript = parse_vtt(subs).strip()
        if transcript:
            return trim(transcript)
    meta = get_meta(url)
    if meta and (meta.get("title") or meta.get("description")):
        title = (meta.get("title") or "").strip()
        desc = (meta.get("description") or "").strip()
        body = "\n".join(p for p in (f"标题：{title}" if title else "",
                                     f"简介：{desc}" if desc else "") if p)
        return trim(body)
    return "[错误] 该视频无字幕、无简介，无法总结"


def format_results(results):
    """[{title, url, snippet}] -> numbered markdown list."""
    lines = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        if not (title or url):
            continue
        lines.append("\n".join(p for p in (f"{i}. {title}", url, snippet) if p))
    return "\n\n".join(lines)


def _attempt(q, search):
    """One provider -> (results, None) or (None, error reason)."""
    try:
        results = search(q)
    except Exception as e:                       # noqa: BLE001 — relay any fetch failure
        return None, str(e)
    return (results, None) if results else (None, "无结果")


def web_search(query, *, search, fallback=None):
    """Search via injected `search(q) -> [{title,url,snippet}]` (Serper); markdown results.

    On a failure (raise / no results) and a `fallback(q)` of the same shape given (Parallel),
    retry there.
    """
    q = (query or "").strip()
    if not q:
        return "[错误] 空查询"
    results, err = _attempt(q, search)
    if results:
        return trim(format_results(results))
    if fallback is not None:
        results, fb_err = _attempt(q, fallback)
        if results:
            return trim(format_results(results))
        if err == fb_err == "无结果":
            return "[错误] 没查到结果"
        return f"[错误] 搜索失败：{err}；备用 Parallel 失败：{fb_err}"
    return "[错误] 没查到结果" if err == "无结果" else f"[错误] 搜索失败：{err}"


def web_read(url, *, fetch):
    """Read one URL via Jina reader (r.jina.ai); return cleaned markdown."""
    u = (url or "").strip()
    if not u:
        return "[错误] 空链接"
    try:
        raw = fetch(_JINA_READER + u)
    except Exception as e:                       # noqa: BLE001 — relay any fetch failure
        return f"[错误] 抓取失败：{e}"
    raw = (raw or "").strip()
    if not raw:
        return "[错误] 没读到内容"
    return trim(raw)


# ── network adapters (real I/O; injected into the pure logic by cli.py) ──

def _load_env():
    """Load skill-dir .env (utf-8-sig for Notepad BOM). Process env var wins."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("\"'").strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    except OSError:
        pass


def _post_json(url, body, headers, *, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={
        "Content-Type": "application/json",
        "User-Agent": "FamilyAssistant/1.0 (+web_reach)",
        **headers,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {e.code} {body}") from None


def _require_key(name):
    _load_env()
    key = os.environ.get(name, "")
    if not key:
        raise RuntimeError(f"未配置 {name}")
    return key


def serper_search(query, *, timeout=_SEARCH_TIMEOUT):
    """POST Serper /search; return [{title,url,snippet}]. Raises on failure."""
    key = _require_key("SERPER_API_KEY")
    data = _post_json(_SERPER_URL, {"q": query, "num": _RESULTS}, {"X-API-KEY": key},
                      timeout=timeout)
    items = data.get("organic") if isinstance(data, dict) else None
    return [{"title": r.get("title") or "", "url": r["link"], "snippet": r.get("snippet") or ""}
            for r in (items if isinstance(items, list) else [])
            if isinstance(r, dict) and r.get("link")][:_RESULTS]


def parallel_search(query, *, timeout=_SEARCH_TIMEOUT):
    """POST Parallel /v1/search; return [{title,url,snippet}], excerpts as the snippet."""
    key = _require_key("PARALLEL_API_KEY")
    body = {"objective": query, "search_queries": [query], "mode": "advanced",
            "advanced_settings": {"max_results": _RESULTS, "excerpt_settings": {
                "max_chars_per_result": _PARALLEL_SNIPPET}}}
    data = _post_json(_PARALLEL_URL, body, {"x-api-key": key}, timeout=timeout)
    items = data.get("results") if isinstance(data, dict) else None
    out = []
    for r in items if isinstance(items, list) else []:
        if isinstance(r, dict) and r.get("url"):
            ex = r.get("excerpts") if isinstance(r.get("excerpts"), list) else []
            text = _MD_HEADING_RE.sub("", "\n".join(e for e in ex if isinstance(e, str)))
            snippet = _WS_RE.sub(" ", text).strip()[:_PARALLEL_SNIPPET]
            out.append({"title": r.get("title") or "", "url": r["url"], "snippet": snippet})
    return out[:_RESULTS]


def jina_fetch(url, *, timeout=_JINA_TIMEOUT):
    headers = {
        "User-Agent": "FamilyAssistant/1.0 (+web_reach)",
        "Accept": "text/plain, text/markdown, */*",
    }
    key = os.environ.get("JINA_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _best_vtt(paths):
    """Pick the best-language .vtt path (prefer en, then zh, then any); None if none."""
    vtts = [p for p in paths if str(p).lower().endswith(".vtt")]
    if not vtts:
        return None
    vtts.sort(key=lambda p: (0 if ".en" in p.name.lower()
                             else 1 if ".zh" in p.name.lower() else 2, p.name))
    return vtts[0]


def ytdlp_subs(url, *, timeout=_SUBS_TIMEOUT):
    """Download auto/uploaded subs to a temp dir; return best-language .vtt text or None."""
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(
                ["yt-dlp", "--skip-download", "--write-auto-sub", "--write-sub",
                 "--sub-lang", "en.*,zh.*,zh-Hans,zh-Hant", "--sub-format", "vtt",
                 "-o", str(Path(td) / "v"), url],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        best = _best_vtt(Path(td).glob("*.vtt"))
        if best is None:
            return None
        try:
            return best.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None


def ytdlp_meta(url, *, timeout=_META_TIMEOUT):
    try:
        r = subprocess.run(
            ["yt-dlp", "--skip-download", "--dump-json", url],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return {"title": d.get("title", ""), "description": d.get("description", "")}
