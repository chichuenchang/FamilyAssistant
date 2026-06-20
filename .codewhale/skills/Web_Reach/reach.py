# Web_Reach — read-only web / news / YouTube fetch for the agent runtime.
#
# Pure logic (trim/parse_vtt/summarize_youtube/web_search/web_read) takes its I/O as
# injected callables so it is unit-testable without network. The thin adapters
# (jina_fetch/ytdlp_subs/ytdlp_meta) do the real I/O and are wired in by cli.py.
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CAP = 6000              # max chars handed back to the LLM (DeepSeek max_tokens is tight)
_TRUNC = "…[截断]"
_JINA_TIMEOUT = 20      # under _run_cli's 30s subprocess cap
_SUBS_TIMEOUT = 18      # subs + meta worst case 26s, still under 30s
_META_TIMEOUT = 8
_JINA_READER = "https://r.jina.ai/"                  # keyless; cleans any URL to markdown
_DDG_HTML = "https://html.duckduckgo.com/html/?q="   # bot-friendly, keyless search page

_TAG_RE = re.compile(r"<[^>]+>")   # <c>…</c>, inline <00:00:01.500> word timings


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


def web_search(query, *, fetch):
    """Search the web (keyless) and return cleaned markdown results.

    s.jina.ai (Jina search) needs an API key — 401 keyless — so route a DuckDuckGo
    results page through the keyless Jina reader (r.jina.ai) instead.
    """
    q = (query or "").strip()
    if not q:
        return "[错误] 空查询"
    try:
        raw = fetch(_JINA_READER + _DDG_HTML + urllib.parse.quote(q))
    except Exception as e:                       # noqa: BLE001 — relay any fetch failure
        return f"[错误] 搜索失败：{e}"
    raw = (raw or "").strip()
    if not raw:
        return "[错误] 没查到结果"
    return trim(raw)


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
        vtts = list(Path(td).glob("*.vtt"))
        if not vtts:
            return None
        vtts.sort(key=lambda p: (0 if ".en" in p.name.lower()
                                 else 1 if ".zh" in p.name.lower() else 2, p.name))
        try:
            return vtts[0].read_text(encoding="utf-8", errors="replace")
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
