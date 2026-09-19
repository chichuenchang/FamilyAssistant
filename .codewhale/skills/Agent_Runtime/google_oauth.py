"""Google OAuth 一次性授权（各 Google provider 的 --auth 共用）。

本地回环 OAuth：起临时 http 服务接 code，浏览器里用户批准，换 refresh token。
凭据只从环境变量 <prefix>_CLIENT_ID / <prefix>_CLIENT_SECRET 读，结果打印 setx 命令。
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def run_loopback_auth(prefix: str, scope: str, *, consent: str, next_step: str,
                      client_hint: str = "") -> None:
    """consent：授权页权限说明；next_step：成功后提示；client_hint：OAuth 客户端补充说明。"""
    client_id = os.environ.get(f"{prefix}_CLIENT_ID", "")
    client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print(f"先设置 {prefix}_CLIENT_ID / {prefix}_CLIENT_SECRET 环境变量"
              f"（Google Cloud Console → OAuth 客户端，Desktop app 类型{client_hint}），"
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
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
    })
    print(f"浏览器即将打开 Google 授权页（{consent}）…")
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
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    if status != 200:
        print(f"换取 token 失败 {status}: {body[:300]!r}")
        raise SystemExit(1)
    refresh = json.loads(body).get("refresh_token", "")
    if not refresh:
        print("响应里没有 refresh_token（多半是之前授权过且未带 prompt=consent）。"
              "去 https://myaccount.google.com/permissions 移除本应用授权后重跑。")
        raise SystemExit(1)

    print("\n授权成功。在你自己的终端执行（之后开新终端生效）：\n")
    print(f'  setx {prefix}_REFRESH_TOKEN "{refresh}"')
    print(f"\n{next_step}")
