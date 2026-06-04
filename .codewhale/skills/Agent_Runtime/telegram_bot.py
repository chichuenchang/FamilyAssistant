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
sys.path.insert(0, str(ROOT))  # scripts/cli.py、OCR skill（经 agent_core）

from agent_core import Agent

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BASE = f"https://api.telegram.org/bot{TOKEN}"

# 上次处理的 update_id（避免重复）
OFFSET_FILE = ROOT / "data" / ".telegram_offset"


def _load_offset() -> int:
    if OFFSET_FILE.exists():
        return int(OFFSET_FILE.read_text().strip())
    return 0


def _save_offset(update_id: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(update_id))


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

            if not text:
                continue

            print(f"[tg] {user_name}: {text[:60]}")

            # 处理消息
            reply = agent.handle(text, user=str(chat_id))
            if reply:
                send_message(chat_id, reply)

            offset = max(offset, update_id)

        _save_offset(offset)


if __name__ == "__main__":
    print("Family Assistant — Telegram Bot")
    print(f"Token: {'已设置' if TOKEN else '未设置'}")
    run()
