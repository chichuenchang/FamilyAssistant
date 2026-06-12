"""
Calendar Keeper — Google Calendar + Google Tasks Provider（用户私有实现）

实现 calendar_sync 调用契约（下方 8 个函数），零外部依赖（仅标准库 urllib，
Calendar REST API v3 + Tasks REST API v1 + OAuth refresh token）。

认证（全部走环境变量，不进代码、不进日志、不进 git）：
    GCAL_CLIENT_ID / GCAL_CLIENT_SECRET  ← Google Cloud Console OAuth 客户端
                                            （Desktop app 类型；可与 Remote_Backup
                                            复用同一个客户端，但 scope 不同需
                                            单独 --auth 授权一次）
    GCAL_REFRESH_TOKEN                   ← 一次性授权获取：
                                            python calendar_provider.py --auth
    GCAL_CALENDAR_ID                     ← 可选，默认 primary（主日历）。
                                            日历 id 形如邮箱，属隐私 → 环境变量，
                                            不进 config.json。

权限范围（最小化）：
    calendar.events  只能读写日历上的活动，不能管理/删除日历本身
    tasks            读写 Google Tasks 待办（@default 清单）

契约（其他用户换日历服务时，按此重写本文件即可，引擎零改动）：
    is_configured() -> bool          三个环境变量未配齐 → False（引擎优雅跳过）
    list_events(time_min_iso, time_max_iso) -> list[dict]
        窗口内的活动（重复活动已展开为实例）。dict:
        {uid, title, start, end, all_day, location, notes}
        start/end：定时活动 'YYYY-MM-DDTHH:MM'；全天 'YYYY-MM-DD'（end 独占）。
    create_event(item) -> uid        item 取 cal_db 行字段
                                     （title/start_at/end_at/all_day/location/notes）
    delete_event(uid)                远端不存在视为成功
    list_tasks() -> list[dict]       @default 清单全部待办（含已完成）。dict:
                                     {uid, title, due('YYYY-MM-DD'或''), notes, done}
    create_task(item) -> uid         item 取 cal_db 行字段（title/start_at=截止日/notes）
    complete_task(uid)               远端不存在视为成功
    delete_task(uid)                 远端不存在视为成功
    网络/API 错误抛 RuntimeError（引擎记录并在下一轮重试）。

提交 provider 前自查：代码里不得出现任何字面 token/key，凭据只能 os.environ 读取。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

SCOPES = ("https://www.googleapis.com/auth/calendar.events "
          "https://www.googleapis.com/auth/tasks")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_API = "https://www.googleapis.com/calendar/v3"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
TASK_LIST = "@default"

# 进程内缓存（access token ~1h 有效）
_token_cache: dict = {"access": None, "exp": 0.0}


def is_configured() -> bool:
    """三个环境变量齐备即视为已配置。"""
    return all(os.environ.get(v) for v in
               ("GCAL_CLIENT_ID", "GCAL_CLIENT_SECRET", "GCAL_REFRESH_TOKEN"))


def _calendar_id() -> str:
    return os.environ.get("GCAL_CALENDAR_ID") or "primary"


def _tz_offset() -> str:
    """本机时区偏移 '+08:00' 格式（家庭假定单一时区，即日历所在时区）。"""
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _http(method: str, url: str, data: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    """唯一 HTTP 出口（测试在此打桩）。返回 (status, body)。"""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _token() -> str:
    """refresh token 换 access token，进程内缓存到过期前 1 分钟。"""
    if _token_cache["access"] and time.time() < _token_cache["exp"]:
        return _token_cache["access"]
    data = urllib.parse.urlencode({
        "client_id": os.environ["GCAL_CLIENT_ID"],
        "client_secret": os.environ["GCAL_CLIENT_SECRET"],
        "refresh_token": os.environ["GCAL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    status, body = _http("POST", TOKEN_URL, data,
                         {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise RuntimeError(f"Google OAuth token 刷新失败 {status}: {body[:200]!r}")
    tok = json.loads(body)
    _token_cache["access"] = tok["access_token"]
    _token_cache["exp"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return _token_cache["access"]


def _api(method: str, url: str, payload: dict | None = None,
         ok_missing: bool = False) -> dict:
    """带认证调 API。ok_missing=True 时 404/410 视为成功（幂等删除/完成）。"""
    headers = {"Authorization": f"Bearer {_token()}"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    status, body = _http(method, url, data, headers)
    if ok_missing and status in (404, 410):
        return {}
    if status >= 300:
        raise RuntimeError(f"Google API {method} {url} → {status}: {body[:200]!r}")
    return json.loads(body) if body else {}


# ── 活动（Google Calendar） ─────────────────────────────────────


def list_events(time_min_iso: str, time_max_iso: str) -> list[dict]:
    """窗口内活动，重复活动展开为实例，分页拉全。"""
    cal = urllib.parse.quote(_calendar_id())
    out: list[dict] = []
    page_token = None
    while True:
        params = {"timeMin": time_min_iso, "timeMax": time_max_iso,
                  "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": 250}
        if page_token:
            params["pageToken"] = page_token
        r = _api("GET", f"{CAL_API}/calendars/{cal}/events?"
                 + urllib.parse.urlencode(params))
        for e in r.get("items", []):
            if e.get("status") == "cancelled":
                continue
            start = e.get("start") or {}
            end = e.get("end") or {}
            all_day = "date" in start
            out.append({
                "uid": e.get("id", ""),
                "title": e.get("summary") or "(无标题)",
                "start": start.get("date") or (start.get("dateTime") or "")[:16],
                "end": end.get("date") or (end.get("dateTime") or "")[:16],
                "all_day": all_day,
                "location": e.get("location") or "",
                "notes": e.get("description") or "",
            })
        page_token = r.get("nextPageToken")
        if not page_token:
            break
    return out


def create_event(item: dict) -> str:
    """新建活动，返回远端 uid。item 字段同 cal_db 行。"""
    cal = urllib.parse.quote(_calendar_id())
    start_at = item.get("start_at") or ""
    body: dict = {"summary": item.get("title") or "(无标题)"}
    if item.get("location"):
        body["location"] = item["location"]
    if item.get("notes"):
        body["description"] = item["notes"]
    if item.get("all_day") or len(start_at) == 10:
        day = start_at[:10]
        next_day = (datetime.strptime(day, "%Y-%m-%d")
                    + timedelta(days=1)).strftime("%Y-%m-%d")
        body["start"] = {"date": day}
        body["end"] = {"date": next_day}  # 全天活动 end 独占
    else:
        off = _tz_offset()
        end_at = item.get("end_at") or ""
        if not end_at:
            end_at = (datetime.strptime(start_at, "%Y-%m-%dT%H:%M")
                      + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        body["start"] = {"dateTime": f"{start_at}:00{off}"}
        body["end"] = {"dateTime": f"{end_at}:00{off}"}
    r = _api("POST", f"{CAL_API}/calendars/{cal}/events", body)
    return r.get("id", "")


def delete_event(uid: str) -> None:
    """删除活动。远端不存在视为成功。"""
    cal = urllib.parse.quote(_calendar_id())
    _api("DELETE", f"{CAL_API}/calendars/{cal}/events/{urllib.parse.quote(uid)}",
         ok_missing=True)


# ── 待办（Google Tasks，@default 清单） ─────────────────────────


def _tasks_url(suffix: str = "") -> str:
    return (f"{TASKS_API}/lists/{urllib.parse.quote(TASK_LIST)}/tasks"
            + (f"/{urllib.parse.quote(suffix)}" if suffix else ""))


def list_tasks() -> list[dict]:
    """@default 清单全部待办（含已完成/隐藏，便于对账），分页拉全。"""
    out: list[dict] = []
    page_token = None
    while True:
        params = {"showCompleted": "true", "showHidden": "true", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        r = _api("GET", _tasks_url() + "?" + urllib.parse.urlencode(params))
        for t in r.get("items", []):
            if t.get("deleted"):
                continue
            out.append({
                "uid": t.get("id", ""),
                "title": t.get("title") or "(无标题)",
                "due": (t.get("due") or "")[:10],
                "notes": t.get("notes") or "",
                "done": t.get("status") == "completed",
            })
        page_token = r.get("nextPageToken")
        if not page_token:
            break
    return out


def create_task(item: dict) -> str:
    """新建待办，返回远端 uid。截止日取 item['start_at']（'YYYY-MM-DD' 或空）。"""
    body: dict = {"title": item.get("title") or "(无标题)"}
    if item.get("notes"):
        body["notes"] = item["notes"]
    due = (item.get("start_at") or "")[:10]
    if due:
        body["due"] = f"{due}T00:00:00.000Z"  # Tasks API 只认日期部分
    r = _api("POST", _tasks_url(), body)
    return r.get("id", "")


def complete_task(uid: str) -> None:
    """标记待办完成。远端不存在视为成功。"""
    _api("PATCH", _tasks_url(uid), {"status": "completed"}, ok_missing=True)


def delete_task(uid: str) -> None:
    """删除待办。远端不存在视为成功。"""
    _api("DELETE", _tasks_url(uid), ok_missing=True)


# ── 一次性授权：python calendar_provider.py --auth ──────────────
# 本地回环 OAuth：起临时 http 服务接 code，浏览器里用户批准，换 refresh token。

def _run_auth() -> None:
    import http.server
    import threading
    import webbrowser

    client_id = os.environ.get("GCAL_CLIENT_ID", "")
    client_secret = os.environ.get("GCAL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("先设置 GCAL_CLIENT_ID / GCAL_CLIENT_SECRET 环境变量"
              "（Google Cloud Console → OAuth 客户端，Desktop app 类型；"
              "可复用 Remote_Backup 的同一客户端），然后开新终端重跑。")
        raise SystemExit(1)

    code_holder: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = (qs.get("code") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>授权完成，可以关掉这个页面回到终端。</h2>".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect = f"http://127.0.0.1:{port}"
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    })
    print("浏览器即将打开 Google 授权页（只授予 日历活动 + 待办 的读写权限）…")
    print(f"没自动打开就手动访问：\n{url}\n")
    webbrowser.open(url)

    print("等待授权回调…")
    deadline = time.time() + 300
    while "code" not in code_holder and time.time() < deadline:
        time.sleep(0.5)
    server.server_close()
    code = code_holder.get("code")
    if not code:
        print("5 分钟内未收到授权回调，重跑 --auth 再试。")
        raise SystemExit(1)

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    }).encode()
    status, body = _http("POST", TOKEN_URL, data,
                         {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        print(f"换取 token 失败 {status}: {body[:300]!r}")
        raise SystemExit(1)
    refresh = json.loads(body).get("refresh_token", "")
    if not refresh:
        print("响应里没有 refresh_token（多半是之前授权过且未带 prompt=consent）。"
              "去 https://myaccount.google.com/permissions 移除本应用授权后重跑。")
        raise SystemExit(1)

    print("\n授权成功。在你自己的终端执行（之后开新终端生效）：\n")
    print(f'  setx GCAL_REFRESH_TOKEN "{refresh}"')
    print("\n然后 config.json 设 calendar.enabled: true，重启机器人即可。")


if __name__ == "__main__":
    import sys
    if "--auth" in sys.argv:
        _run_auth()
    else:
        print(__doc__)
        print(f"configured: {is_configured()}")
        print("一次性授权：python calendar_provider.py --auth")
