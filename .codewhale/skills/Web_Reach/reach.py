# Web_Reach — read-only web / news / YouTube fetch for the agent runtime.
#
# Pure logic (trim/parse_vtt/summarize_youtube/web_search/web_read) takes its I/O as
# injected callables so it is unit-testable without network. The thin adapters
# (rapidapi_search/jina_fetch/ytdlp_subs/ytdlp_meta) do the real I/O, wired in by cli.py.
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CAP = 6000              # max chars handed back to the LLM (DeepSeek max_tokens is tight)
_TRUNC = "…[截断]"
_JINA_TIMEOUT = 20      # under _run_cli's 30s subprocess cap
_SUBS_TIMEOUT = 18      # subs + meta worst case 26s, still under 30s
_META_TIMEOUT = 8
_JINA_READER = "https://r.jina.ai/"                  # keyless; cleans any URL to markdown
_RAPID_HOST = "real-time-web-search.p.rapidapi.com"  # RapidAPI "Real-Time Web Search" (paid)
_RAPID_NUM = 10         # `limit` ignored by API; `num` honoured

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


def _organic(payload):
    """Result list from either response shape: data=[…] or data={organic_results:[…]}."""
    data = (payload or {}).get("data")
    if isinstance(data, dict):
        data = data.get("organic_results")
    return [r for r in (data or []) if isinstance(r, dict)]


def format_results(payload):
    """RapidAPI search JSON -> numbered markdown list (title / url / snippet)."""
    lines = []
    for i, r in enumerate(_organic(payload), 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        if not (title or url):
            continue
        lines.append("\n".join(p for p in (f"{i}. {title}", url, snippet) if p))
    return "\n\n".join(lines)


def web_search(query, *, search):
    """Search via injected `search(q) -> dict` (RapidAPI JSON); return markdown results."""
    q = (query or "").strip()
    if not q:
        return "[错误] 空查询"
    try:
        payload = search(q)
    except Exception as e:                       # noqa: BLE001 — relay any fetch failure
        return f"[错误] 搜索失败：{e}"
    status = (payload or {}).get("status")
    if status and status != "OK":
        return f"[错误] 搜索失败：{payload.get('error') or status}"
    out = format_results(payload)
    if not out:
        return "[错误] 没查到结果"
    return trim(out)


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


def rapidapi_search(query, *, num=_RAPID_NUM, timeout=_JINA_TIMEOUT):
    """GET RapidAPI Real-Time Web Search /search; return parsed JSON. Raises on failure."""
    _load_env()
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key:
        raise RuntimeError("未配置 RAPIDAPI_KEY")
    url = f"https://{_RAPID_HOST}/search?" + urllib.parse.urlencode({"q": query, "num": num})
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": key,
        "x-rapidapi-host": _RAPID_HOST,
        "User-Agent": "FamilyAssistant/1.0 (+web_reach)",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"HTTP {e.code} {body}") from None


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
