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
    python .codewhale/skills/Agent_Runtime/telegram_bot.py
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

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 agent_core

import logging

from agent_core import Agent, receipt_month_dir, setup_logging
from members import resolve

log = logging.getLogger("familyassist.telegram")

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Document_Keeper"))
from reminder import check_and_push as _doc_reminder_check

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Remote_Backup"))
from backup_sync import mark_dirty as _backup_mark_dirty, backup_tick as _backup_tick

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"

# 上次处理的 update_id（避免重复）
OFFSET_FILE = ROOT / "data" / ".telegram_offset"


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


def download_photo(file_id: str) -> Path | None:
    """getFile 拿到路径后下载图片到票据收件箱，返回保存路径。"""
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
    dest = receipt_month_dir(now) / f"{ts}_telegram.jpg"
    try:
        dest.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        _backup_mark_dirty()
        return dest
    except Exception as e:
        print(f"[tg] 图片下载失败: {e}", file=sys.stderr)
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

    agent = Agent()
    offset = _load_offset()
    print(f"[tg] 等待消息... (Ctrl+C 停止)")

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
            update_id = update["update_id"]
            msg = update.get("message", {})
            if not msg:
                offset = max(offset, update_id)
                continue

            chat_id = msg["chat"]["id"]
            # 成员闸门：未注册 id 静默丢弃（不回复、不进 LLM），本地留一行日志
            member = resolve("telegram", str(chat_id))
            if member is None:
                print(f"[tg] 忽略未注册来源 chat_id={chat_id}")
                offset = max(offset, update_id)
                continue
            user_name = msg.get("from", {}).get("first_name", "unknown")
            text = msg.get("text", "")

            # 处理 /start 命令
            if msg.get("entities") and msg["entities"][0].get("type") == "bot_command":
                cmd = text.strip().split()[0]
                if cmd == "/start":
                    send_message(chat_id,
                        "👋 你好！我是 Family Assistant。\n"
                        "可以直接跟我说话，比如：\n"
                        "  • \"花了45块 午餐\" — 记账\n"
                        "  • \"这个月花了多少\" — 查账\n"
                        "  • \"美元汇率\" — 查汇率")
                offset = max(offset, update_id)
                continue

            # 图片消息 → 下载到票据收件箱 → OCR 记账流程（与微信一致）
            photos = msg.get("photo") or []
            if photos:
                print(f"[tg] 图片消息 from {user_name}")
                file_id = photos[-1].get("file_id", "")  # 最后一个 = 最大尺寸
                dest = download_photo(file_id) if file_id else None
                log.debug("图片 from %s(%s) → %s", user_name, member, dest)
                if dest:
                    reply = agent.handle_image(str(dest), user=str(chat_id), member=member)
                else:
                    reply = "图片下载失败，请重发。"
                log.debug("图片回复 → %s", reply[:200])
                send_message(chat_id, reply)
                offset = max(offset, update_id)
                continue

            if not text:
                continue

            print(f"[tg] {user_name}: {text[:60]}")
            log.debug("文字 from %s(%s): %s", user_name, member, text)

            # 处理消息
            reply = agent.handle(text, user=str(chat_id), member=member)
            log.debug("文字回复 → %s", (reply or "")[:200])
            if reply:
                send_message(chat_id, reply)

            offset = max(offset, update_id)

        _save_offset(offset)

        # 文档到期提醒：每天最多推一次（reminder 内部按日去重）
        try:
            _doc_reminder_check(send_message, "telegram")
        except Exception as e:
            print(f"[tg] 文档提醒检查异常: {e}", file=sys.stderr)
            log.exception("文档提醒检查异常")

        # 用户数据备份：脏 + 静默期满则镜像一轮（backup_sync 内部把关，永不抛）
        _backup_tick()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Family Assistant — Telegram Bot")
    parser.add_argument("--debug", action="store_true",
                        help="开启调试日志（写 data/bot_debug.log，默认关）")
    args = parser.parse_args()
    setup_logging(args.debug)
    print("Family Assistant — Telegram Bot")
    print(f"Token: {'已设置' if TOKEN else '未设置'}")
    run()


if __name__ == "__main__":
    main()
