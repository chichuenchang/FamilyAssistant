"""
Remote Backup — Google Drive Provider（用户私有实现）

实现 backup_sync 调用契约（5 个函数），零外部依赖（仅标准库 urllib，
Drive REST API v3 + OAuth refresh token）。

认证（全部走环境变量，不进代码、不进日志）：
    GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET  ← Google Cloud Console
                                                OAuth 客户端（Desktop app 类型）
    GDRIVE_REFRESH_TOKEN                     ← 一次性授权获取：
                                                python backup_provider.py --auth

权限范围 drive.file（最小权限）：只能看到/操作本应用自己创建的文件，
看不到用户 Drive 里的其他任何内容。

云端布局：所有文件平铺在一个 remote_root 文件夹里（config.json
backup.remote_root），相对路径存在每个文件的 appProperties.rel 元数据中
（Drive 不是文件系统，平铺 + 路径标签比维护文件夹树简单且无竞态）。
注意：appProperties 值上限 124 字节，本项目相对路径远低于此。

契约行为：
- upload 覆盖同 rel 的已有远端文件（镜像语义）
- delete 文件不存在视为成功
- list_remote() → {rel: {"size": int}}
- 网络/API 错误抛 RuntimeError（引擎记录并在下一轮重试）
- is_configured() False ⇔ 三个环境变量未配齐（引擎据此优雅跳过）
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Windows 控制台编码容错（--auth 流程要打印中文提示，cp1252 控制台会炸）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"

ROOT = Path(__file__).resolve().parents[3]

# 进程内缓存（access token ~1h 有效；remote_root 文件夹 id 不变）
_token_cache: dict = {"access": None, "exp": 0.0}
_folder_cache: dict = {"id": None}


def _remote_root() -> str:
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("backup") or {}).get("remote_root") or "FamilyAssistant"
    except Exception:
        return "FamilyAssistant"


def is_configured() -> bool:
    """三个环境变量齐备即视为已配置。"""
    return all(os.environ.get(v) for v in
               ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"))


def _http(method: str, url: str, data: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    """唯一 HTTP 出口（测试在此打桩）。返回 (status, body)。"""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _token() -> str:
    """refresh token 换 access token，进程内缓存到过期前 1 分钟。"""
    if _token_cache["access"] and time.time() < _token_cache["exp"]:
        return _token_cache["access"]
    data = urllib.parse.urlencode({
        "client_id": os.environ["GDRIVE_CLIENT_ID"],
        "client_secret": os.environ["GDRIVE_CLIENT_SECRET"],
        "refresh_token": os.environ["GDRIVE_REFRESH_TOKEN"],
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


def _api_json(method: str, url: str, payload: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    status, body = _http(method, url, data, headers)
    if status >= 300:
        raise RuntimeError(f"Drive API {method} {url} → {status}: {body[:200]!r}")
    return json.loads(body) if body else {}


def _q(value: str) -> str:
    """Drive 查询字符串里的单引号/反斜杠转义。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _folder_id() -> str:
    """remote_root 文件夹 id，不存在则创建。进程内缓存。"""
    if _folder_cache["id"]:
        return _folder_cache["id"]
    root_name = _remote_root()
    query = (f"name = '{_q(root_name)}' and mimeType = '{_FOLDER_MIME}' "
             f"and trashed = false")
    r = _api_json("GET", f"{API}/files?" + urllib.parse.urlencode(
        {"q": query, "fields": "files(id)", "pageSize": 10}))
    files = r.get("files") or []
    if files:
        _folder_cache["id"] = files[0]["id"]
    else:
        created = _api_json("POST", f"{API}/files",
                            {"name": root_name, "mimeType": _FOLDER_MIME})
        _folder_cache["id"] = created["id"]
    return _folder_cache["id"]


def _find(remote_rel: str) -> str | None:
    """按 appProperties.rel 找文件 id；不存在返回 None。"""
    query = (f"appProperties has {{ key='rel' and value='{_q(remote_rel)}' }} "
             f"and '{_folder_id()}' in parents and trashed = false")
    r = _api_json("GET", f"{API}/files?" + urllib.parse.urlencode(
        {"q": query, "fields": "files(id)", "pageSize": 2}))
    files = r.get("files") or []
    return files[0]["id"] if files else None


def upload(local_path: Path, remote_rel: str) -> None:
    """上传/覆盖：已有同 rel 文件则原地更新内容，否则新建。"""
    content = Path(local_path).read_bytes()
    existing = _find(remote_rel)
    meta: dict = {"name": remote_rel.rsplit("/", 1)[-1],
                  "appProperties": {"rel": remote_rel}}
    if existing is None:
        meta["parents"] = [_folder_id()]
        method, url = "POST", f"{UPLOAD_API}/files?uploadType=multipart"
    else:
        method, url = "PATCH", f"{UPLOAD_API}/files/{existing}?uploadType=multipart"
    boundary = "codewhale-backup-7e3f9d"
    body = ((f"--{boundary}\r\n"
             f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
             f"{json.dumps(meta, ensure_ascii=False)}\r\n"
             f"--{boundary}\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode()
            + content + f"\r\n--{boundary}--".encode())
    headers = {"Authorization": f"Bearer {_token()}",
               "Content-Type": f"multipart/related; boundary={boundary}"}
    status, resp = _http(method, url, body, headers)
    if status >= 300:
        raise RuntimeError(f"Drive 上传失败 {remote_rel} → {status}: {resp[:200]!r}")


def delete(remote_rel: str) -> None:
    """删除云端文件。不存在视为成功。"""
    fid = _find(remote_rel)
    if fid is None:
        return
    headers = {"Authorization": f"Bearer {_token()}"}
    status, body = _http("DELETE", f"{API}/files/{fid}", None, headers)
    if status >= 300 and status != 404:
        raise RuntimeError(f"Drive 删除失败 {remote_rel} → {status}: {body[:200]!r}")


def list_remote() -> dict:
    """remote_root 下全部带 rel 标签的文件：{rel: {"size": int}}。分页拉全。"""
    out: dict = {}
    page_token = None
    while True:
        params = {"q": f"'{_folder_id()}' in parents and trashed = false",
                  "fields": "nextPageToken, files(id, size, appProperties)",
                  "pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        r = _api_json("GET", f"{API}/files?" + urllib.parse.urlencode(params))
        for f in r.get("files", []):
            rel = (f.get("appProperties") or {}).get("rel")
            if rel:
                out[rel] = {"size": int(f.get("size") or 0)}
        page_token = r.get("nextPageToken")
        if not page_token:
            break
    return out


def download(remote_rel: str, local_path: Path) -> None:
    """下载云端文件到本地（覆盖），自动建父目录。"""
    fid = _find(remote_rel)
    if fid is None:
        raise RuntimeError(f"云端不存在: {remote_rel}")
    headers = {"Authorization": f"Bearer {_token()}"}
    status, body = _http("GET", f"{API}/files/{fid}?alt=media", None, headers)
    if status >= 300:
        raise RuntimeError(f"Drive 下载失败 {remote_rel} → {status}: {body[:200]!r}")
    target = Path(local_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


# ── 一次性授权：python backup_provider.py --auth ────────────────
# 本地回环 OAuth：起临时 http 服务接 code，浏览器里用户批准，换 refresh token。

def _run_auth() -> None:
    import http.server
    import threading
    import webbrowser

    client_id = os.environ.get("GDRIVE_CLIENT_ID", "")
    client_secret = os.environ.get("GDRIVE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("先设置 GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET 环境变量"
              "（Google Cloud Console → OAuth 客户端，Desktop app 类型），"
              "然后开新终端重跑。")
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
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    print("浏览器即将打开 Google 授权页（只授予本应用自建文件的权限）…")
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
    print(f'  setx GDRIVE_REFRESH_TOKEN "{refresh}"')
    print("\n然后告诉助手继续（启用备份 + 首次全量上传）。")


if __name__ == "__main__":
    if "--auth" in sys.argv:
        _run_auth()
    else:
        print(__doc__)
        print(f"configured: {is_configured()}")
        print("一次性授权：python backup_provider.py --auth")
