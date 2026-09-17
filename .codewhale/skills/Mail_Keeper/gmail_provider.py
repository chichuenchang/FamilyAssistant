"""
Mail Keeper — Gmail REST v1 provider（零外部依赖，仅标准库 urllib）。

凭据全走环境变量 <prefix>_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN
（prefix 来自成员 members.json 的 mail.cred_prefix，缺省 GMAIL）。
一次性授权：python gmail_provider.py --auth [--prefix GMAIL]

scope：gmail.readonly（搜/读）+ gmail.send（只发，不能删改信件）。
网络/API 错误抛 RuntimeError。代码里不得出现字面 token/key。

新邮件播报（mail_watch 用）要的三件：
    profile_history_id(prefix) -> str                       当前游标（首次起点）
    history_since(cursor, prefix) -> (rows | None, 新游标)   rows=[{id, thread_id, labels}]（新进收件箱）；
                                                            None = 游标过旧已失效，新游标是重新起点
    message_metas(ids, prefix) -> [{id, from, subject, date, labels, unread}]   不含正文；
                                                            已不存在的信（404）直接略过
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录
import google_oauth

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCOPES = ("https://www.googleapis.com/auth/gmail.readonly "
          "https://www.googleapis.com/auth/gmail.send")
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_PREFIX = "GMAIL"
BODY_CAP = 6000
TIMEOUT_S = 15             # 单次 HTTP 上限：播报节拍跑在传输层轮询循环里，慢了全员卡住
META_WORKERS = 5           # 信头并发数
HISTORY_PAGES = 5          # history.list 翻页上限（一轮最多看这么多页，防爆量时卡住节拍）
# 新进收件箱但不该播报的标签（自己发的、草稿、垃圾、已删）
_SKIP_LABELS = {"SENT", "DRAFT", "SPAM", "TRASH"}

_token_cache: dict[str, tuple[str, float]] = {}   # prefix → (access, exp)


class ApiError(RuntimeError):
    """带 HTTP 状态码的 Gmail 错误（本模块内部按码分流，如 history 起点过旧 404）。"""

    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def is_configured(prefix: str = DEFAULT_PREFIX) -> bool:
    return all(os.environ.get(f"{prefix}_{v}")
               for v in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"))


def _http(method: str, url: str, data: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    """唯一 HTTP 出口（测试在此打桩）。"""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _token(prefix: str) -> str:
    access, exp = _token_cache.get(prefix, ("", 0.0))
    if access and time.time() < exp:
        return access
    data = urllib.parse.urlencode({
        "client_id": os.environ[f"{prefix}_CLIENT_ID"],
        "client_secret": os.environ[f"{prefix}_CLIENT_SECRET"],
        "refresh_token": os.environ[f"{prefix}_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    status, body = _http("POST", TOKEN_URL, data,
                         {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise RuntimeError(f"Google OAuth token 刷新失败 {status}: {body[:200]!r}")
    tok = json.loads(body)
    _token_cache[prefix] = (tok["access_token"],
                            time.time() + int(tok.get("expires_in", 3600)) - 60)
    return tok["access_token"]


def _api(prefix: str, method: str, path: str, params: dict | list | None = None,
         payload: dict | None = None) -> dict:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"Authorization": f"Bearer {_token(prefix)}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    status, body = _http(method, url, data, headers)
    if status >= 300:
        raise ApiError(status, f"Gmail API {method} {path} → {status}: {body[:200]!r}")
    return json.loads(body) if body else {}


# ── 解析 ────────────────────────────────────────────────────

def _headers(payload: dict) -> dict:
    """头名小写 → 值（同名取首个）。"""
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        out.setdefault(h.get("name", "").lower(), h.get("value", ""))
    return out


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def body_text(payload: dict) -> str:
    """MIME 树取正文：优先 text/plain，无则 text/html 去标签。附件忽略。"""
    plain, rich = [], []

    def walk(p: dict):
        mime = p.get("mimeType", "")
        data = (p.get("body") or {}).get("data")
        if data and not p.get("filename"):
            if mime == "text/plain":
                plain.append(_b64(data))
            elif mime == "text/html":
                rich.append(_b64(data))
        for c in p.get("parts") or []:
            walk(c)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    return _html_to_text("\n".join(rich))


# ── 契约 ────────────────────────────────────────────────────

def message_meta(msg_id: str, prefix: str = DEFAULT_PREFIX) -> dict:
    """一封的信头（无正文）：{id, thread_id, from, subject, date, snippet, labels, unread}。"""
    full = _api(prefix, "GET", f"/messages/{urllib.parse.quote(msg_id, safe='')}",
                [("format", "metadata")] + [("metadataHeaders", h)
                                            for h in ("From", "Subject", "Date")])
    h = _headers(full.get("payload") or {})
    return {"id": full["id"], "thread_id": full.get("threadId", ""),
            "from": h.get("from", ""), "subject": h.get("subject", ""),
            "date": h.get("date", ""), "snippet": html.unescape(full.get("snippet", "")),
            "labels": list(full.get("labelIds") or []),
            "unread": "UNREAD" in (full.get("labelIds") or [])}


def message_metas(ids: list[str], prefix: str = DEFAULT_PREFIX) -> list[dict]:
    """并发取一批信头，保持 ids 的顺序。列出后又被彻底删除的信（404）略过——
    history/搜索结果里仍带着它的 id，一封取不到不能拖垮整批。"""
    def one(mid: str) -> dict | None:
        try:
            return message_meta(mid, prefix)
        except ApiError as e:
            if e.status == 404:
                return None
            raise

    if not ids:
        return []
    _token(prefix)                        # 先刷好 token，免得各线程各刷一次
    with ThreadPoolExecutor(max_workers=META_WORKERS) as pool:
        return [m for m in pool.map(one, ids) if m]


def search(query: str, max_results: int = 10, prefix: str = DEFAULT_PREFIX) -> list[dict]:
    """Gmail 搜索语法（同网页搜索框）。返回 message_meta 列表。"""
    r = _api(prefix, "GET", "/messages",
             {"q": query, "maxResults": max(1, min(int(max_results), 20))})
    return message_metas([m["id"] for m in (r.get("messages") or [])], prefix)


def profile_history_id(prefix: str = DEFAULT_PREFIX) -> str:
    """邮箱当前 historyId（播报游标的起点）。"""
    return str(_api(prefix, "GET", "/profile").get("historyId") or "")


def history_since(start_history_id: str, prefix: str = DEFAULT_PREFIX,
                  label: str = "INBOX") -> tuple[list[dict] | None, str]:
    """自游标以来新进 label 的邮件（按时间升序去重）+ 新游标。

    起点过旧（Gmail 只保证约一周）→ Gmail 回 404 → 返回 (None, 新起点)，
    调用方存下新游标并跳过这一轮（不知道错过了什么，宁可不播报）。
    """
    ids: list[dict] = []
    seen: set[str] = set()
    token = ""
    hid = str(start_history_id)
    for _ in range(HISTORY_PAGES):
        params = {"startHistoryId": str(start_history_id),
                  "historyTypes": "messageAdded", "labelId": label}
        if token:
            params["pageToken"] = token
        try:
            r = _api(prefix, "GET", "/history", params)
        except ApiError as e:
            if e.status == 404:
                return None, profile_history_id(prefix)
            raise
        for rec in r.get("history") or []:
            for add in rec.get("messagesAdded") or []:
                m = add.get("message") or {}
                labels = set(m.get("labelIds") or [])
                if m.get("id") in seen or label not in labels or labels & _SKIP_LABELS:
                    continue
                seen.add(m["id"])
                ids.append({"id": m["id"], "thread_id": m.get("threadId", ""),
                            "labels": sorted(labels)})
        hid = str(r.get("historyId") or hid)
        token = r.get("nextPageToken") or ""
        if not token:
            break
    return ids, hid


def get_message(msg_id: str, prefix: str = DEFAULT_PREFIX) -> dict:
    """单封全文。回信所需头（message-id/references/reply-to）一并返回。"""
    full = _api(prefix, "GET", f"/messages/{urllib.parse.quote(msg_id, safe='')}",
                {"format": "full"})
    payload = full.get("payload") or {}
    h = _headers(payload)
    return {"id": full["id"], "thread_id": full.get("threadId", ""),
            "from": h.get("from", ""), "to": h.get("to", ""), "cc": h.get("cc", ""),
            "reply_to": h.get("reply-to", ""), "subject": h.get("subject", ""),
            "date": h.get("date", ""), "message_id": h.get("message-id", ""),
            "references": h.get("references", ""), "body": body_text(payload)}


def reply_recipient(msg: dict) -> str:
    """回信收件人由代码从原信定（Reply-To 优先，否则 From），LLM 不能指定。"""
    return parseaddr(msg.get("reply_to") or msg.get("from") or "")[1]


def reply_subject(subject: str) -> str:
    return subject if re.match(r"(?i)^\s*re\s*:", subject or "") else f"Re: {subject}".strip()


def send_reply(msg: dict, body: str, prefix: str = DEFAULT_PREFIX) -> str:
    """同线程回信给原发件人。返回新邮件 id。"""
    to = reply_recipient(msg)
    if not to:
        raise RuntimeError("原邮件无可回复地址")
    em = EmailMessage()
    em["To"] = to
    em["Subject"] = reply_subject(msg.get("subject", ""))
    if msg.get("message_id"):
        em["In-Reply-To"] = msg["message_id"]
        em["References"] = f"{msg.get('references', '')} {msg['message_id']}".strip()
    em.set_content(body)
    raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
    r = _api(prefix, "POST", "/messages/send",
             payload={"raw": raw, "threadId": msg.get("thread_id", "")})
    return r.get("id", "")


if __name__ == "__main__":
    prefix = DEFAULT_PREFIX
    if "--prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--prefix") + 1]
    if "--auth" in sys.argv:
        google_oauth.run_loopback_auth(
            prefix, SCOPES, consent="只授予 读邮件 + 发邮件 权限，不能删改",
            client_hint="；可复用 GCAL 的同一客户端，需先启用 Gmail API",
            next_step="然后 data/members.json 给该成员加 mail 块，重启机器人。")
    else:
        print(__doc__)
        print(f"configured ({prefix}): {is_configured(prefix)}")
