"""
Telegram Bot 传输层 — 通过 Telegram 与 Family Assistant 通信。

Telegram Bot API 是全球最开放的 IM Bot 协议：
    - 零审核、零门槛、完全免费
    - 支持私聊 + 群聊，天然多人
    - 任何人搜到 Bot 就能对话

前置条件:
    1. Telegram 里搜 @BotFather → /newbot → 获取 Token
    2. 设环境变量 TELEGRAM_BOT_TOKEN

用法:
    python .codewhale/skills/Agent_Runtime/telegram_bot.py [--no-debug]

    调试日志默认开（写 data/bot_debug.log）；关闭用 --no-debug
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import logging

from agent_core import receipt_month_dir, member_inbox_dir, setup_logging
from transport_base import Transport, with_quote as _with_quote
import paths as _paths

log = logging.getLogger("familyassist.telegram")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"

# 上次处理的 update_id（避免重复）；跟随 data_root，备份硬排除该文件名
OFFSET_FILE = _paths.data_root() / ".telegram_offset"


def _load_offset() -> int:
    if OFFSET_FILE.exists():
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    return 0


def _save_offset(update_id: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(update_id), encoding="utf-8")


def _api(method: str, data: dict | None = None) -> dict | None:
    """调 Telegram Bot API。"""
    import urllib.request
    url = f"{BASE}/{method}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"} if body else {})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        print(f"[tg] API 错误: {e}", file=sys.stderr)
        return None


def download_photo(file_id: str, member: str = "") -> Path | None:
    """getFile 拿到路径后下载图片到发送成员的 inbox 暂存，返回保存路径。"""
    import urllib.request
    r = _api("getFile", {"file_id": file_id})
    if not r or not r.get("ok"):
        return None
    file_path = r["result"].get("file_path", "")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    staging = member_inbox_dir(member, now) if member else receipt_month_dir(now)
    dest = staging / f"{ts}_telegram.jpg"
    try:
        dest.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        Transport.mark_dirty()
        return dest
    except Exception as e:
        print(f"[tg] 图片下载失败: {e}", file=sys.stderr)
        return None


def download_document(file_id: str, file_name: str, member: str = "") -> Path | None:
    """下载 Telegram 文档（PDF）到发送成员 inbox，保留 .pdf 后缀。"""
    import urllib.request
    r = _api("getFile", {"file_id": file_id})
    if not r or not r.get("ok"):
        return None
    file_path = r["result"].get("file_path", "")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    suffix = ".pdf"   # 仅 PDF 走此函数（调用方已判定）；强制 .pdf，确保 ocr_image 走 PDF 分支
    staging = member_inbox_dir(member, now) if member else receipt_month_dir(now)
    dest = staging / f"{ts}_telegram{suffix}"
    try:
        dest.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        Transport.mark_dirty()
        return dest
    except Exception as e:
        print(f"[tg] 文档下载失败: {e}", file=sys.stderr)
        return None


def send_message(chat_id: int | str, text: str) -> bool:
    """发消息。超过 4000 字符自动分段。"""
    if len(text) <= 4000:
        r = _api("sendMessage", {"chat_id": chat_id, "text": text})
        return r is not None and r.get("ok")
    # 分段发送
    for i in range(0, len(text), 4000):
        chunk = text[i:i+4000]
        _api("sendMessage", {"chat_id": chat_id, "text": chunk})
        time.sleep(0.3)
    return True


def send_photo(chat_id: int | str, path: str, caption: str = "") -> bool:
    """sendPhoto 多部分上传（urllib，无新依赖）。"""
    import mimetypes, urllib.request, uuid
    boundary = uuid.uuid4().hex
    p = Path(path)
    try:
        file_bytes = p.read_bytes()
    except OSError:
        return False
    parts = []

    def _field(name, value):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode())

    _field("chat_id", str(chat_id))
    if caption:
        _field("caption", caption)
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; "
                  f'name="photo"; filename="{p.name}"\r\n'
                  f"Content-Type: {mimetypes.guess_type(p.name)[0] or 'image/png'}"
                  "\r\n\r\n").encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{BASE}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return bool(r and r.get("ok"))
    except Exception as e:
        print(f"[tg] sendPhoto 错误: {e}", file=sys.stderr)
        return False


def send_document(chat_id: int | str, path: str, caption: str = "") -> bool:
    """sendDocument 多部分上传（urllib，无新依赖）。"""
    import mimetypes, urllib.request, uuid
    boundary = uuid.uuid4().hex
    p = Path(path)
    try:
        file_bytes = p.read_bytes()
    except OSError:
        return False
    parts = []

    def _field(name, value):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode())

    _field("chat_id", str(chat_id))
    if caption:
        _field("caption", caption)
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; "
                  f'name="document"; filename="{p.name}"\r\n'
                  f"Content-Type: {mimetypes.guess_type(p.name)[0] or 'application/octet-stream'}"
                  "\r\n\r\n").encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{BASE}/sendDocument", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return bool(r and r.get("ok"))
    except Exception as e:
        print(f"[tg] sendDocument 错误: {e}", file=sys.stderr)
        return False


def _tg_quoted_text(msg: dict):
    """引用/回复消息的原文。Telegram 与微信不同，reply_to_message 自带被引消息
    全文（text/caption），无需本地缓存反查；媒体退化为占位。超长截断防挤爆上下文。"""
    ref = msg.get("reply_to_message") or {}
    quoted = ref.get("text") or ref.get("caption") or ""
    if quoted:
        return quoted[:200] + ("…" if len(quoted) > 200 else "")
    if ref.get("photo"):
        return "[图片]"
    if ref.get("document"):
        name = (ref.get("document") or {}).get("file_name", "")
        return f"[文件] {name}".strip()
    return None


class TelegramTransport(Transport):
    """Telegram：target = chat_id。send_* 经模块级函数转发（测试 monkeypatch 这些名字）。"""
    channel = "telegram"
    tag = "tg"

    def send_text(self, target, text: str) -> None:
        send_message(target, text)

    def send_photo(self, target, path: str) -> None:
        send_photo(target, path)

    def send_document(self, target, path: str) -> None:
        send_document(target, path)


_TRANSPORT = TelegramTransport()


def _send_reply(chat_id, reply: str) -> None:
    """拆出图片/文档哨兵：先发图，再发文档，最后发文字（transport_base.deliver）。"""
    _TRANSPORT.deliver(chat_id, reply)


# ── 主循环 ──────────────────────────────────────────────────

def run() -> None:
    """长轮询主循环。"""
    if not TOKEN:
        print("[tg] 未设置 TELEGRAM_BOT_TOKEN。")
        print("  1. Telegram 搜 @BotFather → /newbot")
        print("  2. setx TELEGRAM_BOT_TOKEN \"你的token\"")
        return

    # 启动时验证 Token
    me = _api("getMe")
    if not me or not me.get("ok"):
        print(f"[tg] Token 无效: {me}")
        return
    print(f"[tg] 已连接 — @{me['result']['username']}")

    t = _TRANSPORT
    offset = _load_offset()
    print("[tg] 等待消息... (Ctrl+C 停止)")

    while True:
        try:
            resp = _api("getUpdates", {
                "offset": offset + 1,
                "timeout": 30,
                "allowed_updates": ["message"],
            })
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[tg] 轮询异常: {e}")
            time.sleep(5)
            continue

        if not resp or not resp.get("ok"):
            continue

        for update in resp.get("result", []):
            # 先推进 offset：任何类型的 update（含不支持的贴纸/语音）都只处理一次
            offset = max(offset, update["update_id"])
            msg = update.get("message", {})
            if not msg:
                continue

            chat_id = msg["chat"]["id"]
            member = t.gate(chat_id)
            if member is None:
                continue
            user_name = msg.get("from", {}).get("first_name", "unknown")
            text = msg.get("text", "")

            if msg.get("entities") and msg["entities"][0].get("type") == "bot_command":
                if text.strip().split()[0] == "/start":
                    send_message(chat_id,
                        "👋 你好！我是 Family Assistant。\n"
                        "可以直接跟我说话，比如：\n"
                        "  • \"花了45块 午餐\" — 记账\n"
                        "  • \"这个月花了多少\" — 查账\n"
                        "  • \"美元汇率\" — 查汇率")
                continue

            # 图片 → 下载到发送成员 inbox → OCR 分流
            photos = msg.get("photo") or []
            if photos:
                print(f"[tg] 图片消息 from {user_name}")
                file_id = photos[-1].get("file_id", "")  # 最后一个 = 最大尺寸
                t.on_media(chat_id, chat_id, member,
                           download_photo(file_id, member) if file_id else None)
                continue

            # 文档（PDF）→ 下载到 inbox → OCR 分流
            doc = msg.get("document")
            if doc:
                name = doc.get("file_name", "") or ""
                is_pdf = name.lower().endswith(".pdf") or \
                    doc.get("mime_type") == "application/pdf"
                if is_pdf:
                    file_id = doc.get("file_id", "")
                    t.on_media(chat_id, chat_id, member,
                               download_document(file_id, name, member) if file_id else None)
                else:
                    send_message(chat_id, f"收到文件 {name}（暂不支持，PDF 可以）")
                continue

            if not text:
                continue
            print(f"[tg] {user_name}: {text[:60]}")
            t.on_text(chat_id, chat_id, member, text, quoted=_tg_quoted_text(msg))

        _save_offset(offset)
        t.background_tick()   # 到期提醒 + 懂王投递 + 备份节拍（每轮 ≤30s）


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Family Assistant — Telegram Bot")
    parser.add_argument("--debug", action="store_true", default=True,
                        help="开启调试日志（写 data/bot_debug.log，默认开）")
    parser.add_argument("--no-debug", dest="debug", action="store_false",
                        help="关闭调试日志")
    args = parser.parse_args()
    setup_logging(args.debug)
    print("Family Assistant — Telegram Bot")
    print(f"Token: {'已设置' if TOKEN else '未设置'}")
    run()


if __name__ == "__main__":
    main()
