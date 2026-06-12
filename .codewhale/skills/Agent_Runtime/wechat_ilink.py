"""
微信 iLink Bot 传输层 — 将微信消息接入 Family Assistant。

基于 weixin-ilink SDK（腾讯 iLink Bot 协议的 Python 实现）。
扫码登录，长轮询，零 OpenClaw 依赖。

前置条件:
    1. 微信中开通 ClawBot 插件（搜索 "ClawBot" 或 "OpenClaw"）
    2. 微信版本 ≥ 8.0.70

安装:
    pip install "weixin-ilink[qr]"

用法:
    # 测试模式（命令行交互，不需要微信）
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode test

    # 运行模式（扫码登录 + 长轮询）
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run

    # 重新扫码（切换账号）
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --relogin

    # 调试日志（写 data/bot_debug.log，默认关）
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --debug

安全:
    所有 CLI 调用受同目录 agent_core.py 白名单约束。
    凭据加密存储在 data/wechat_creds.json，不对外传输。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目根（本文件向上 3 级）
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 agent_core

import logging

from agent_core import Agent, receipt_month_dir, setup_logging
from members import resolve

log = logging.getLogger("familyassist.wechat")

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Document_Keeper"))
from reminder import check_and_push as _doc_reminder_check

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Remote_Backup"))
from backup_sync import mark_dirty as _backup_mark_dirty, backup_tick as _backup_tick

sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Calendar_Keeper"))
from calendar_sync import calendar_tick as _calendar_tick

# 凭据存储路径
CREDS_FILE = ROOT / "data" / "wechat_creds.json"


# ── 模式 1: 运行 Bot ────────────────────────────────────────

def run_bot(relogin: bool = False) -> None:
    """扫码登录并启动长轮询 Bot。"""
    from weixin_ilink import WeixinBot, login

    # 如果要求重新登录或凭据文件不存在，走扫码流程
    if relogin or not CREDS_FILE.exists():
        print("[wechat_ilink] 等待扫码...")
        print("  将打开二维码，请用微信扫码授权。")
        print("  注意：需要在微信 ClawBot 插件中先启用。")
        print()
        bot = WeixinBot.from_login(save_to=str(CREDS_FILE))
    else:
        print(f"[wechat_ilink] 加载已有凭据: {CREDS_FILE}")
        bot = WeixinBot(credentials_file=str(CREDS_FILE))

    print(f"[wechat_ilink] 登录成功 — 账号: {bot.account_id}")
    print("[wechat_ilink] 等待微信消息... (Ctrl+C 停止)")

    agent = Agent()

    # 注册文字消息处理器
    @bot.on_text
    def handle_text(msg):
        member = resolve("wechat", msg.from_user)
        if member is None:
            print(f"[wx] 忽略未注册来源 {msg.from_user}")
            return
        print(f"[wx] 文字消息 from {msg.from_user}({member}): {msg.text[:60]}")
        log.debug("文字 from %s(%s): %s", msg.from_user, member, msg.text)
        _calendar_tick()  # 已注册成员消息 → 静默节流刷新远程日历（内部把关，永不抛）
        try:
            reply = agent.handle(msg.text, user=msg.from_user, member=member)
            log.debug("文字回复 → %s", (reply or "")[:200])
            msg.reply_text(reply)
        except Exception as e:
            log.exception("文字处理出错")
            msg.reply_text(f"处理出错: {e}")

    # 注册图片消息处理器
    @bot.on_image
    def handle_image(msg):
        member = resolve("wechat", msg.from_user)
        if member is None:
            print(f"[wx] 忽略未注册来源 {msg.from_user}")
            return
        print(f"[wx] 图片消息 from {msg.from_user}({member})")
        _calendar_tick()
        try:
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            img_path = receipt_month_dir(now) / f"{ts}_wechat.jpg"
            msg.save(str(img_path))
            _backup_mark_dirty()
            log.debug("图片 from %s(%s) 保存 → %s", msg.from_user, member, img_path)
            reply = agent.handle_image(str(img_path), user=msg.from_user, member=member)
            log.debug("图片回复 → %s", (reply or "")[:200])
            msg.reply_text(reply)
        except Exception as e:
            log.exception("图片处理出错")
            msg.reply_text(f"图片处理出错: {e}")

    # 其他消息类型：友好提示
    @bot.on_voice
    def handle_voice(msg):
        if resolve("wechat", msg.from_user) is None:
            return
        msg.reply_text("目前不支持语音消息，请发文字或图片。")

    @bot.on_file
    def handle_file(msg):
        if resolve("wechat", msg.from_user) is None:
            return
        msg.reply_text(f"收到文件: {msg.file_name}（暂不支持文件处理）")

    @bot.on_video
    def handle_video(msg):
        if resolve("wechat", msg.from_user) is None:
            return
        msg.reply_text("收到视频（暂不支持视频处理）")

    # 文档到期提醒：后台线程每 10 分钟检查（reminder 内部按日去重，
    # weixin-ilink bot.run() 阻塞，无轮询循环可挂钩）
    import threading
    import time as _time

    def _reminder_loop():
        while True:
            try:
                _doc_reminder_check(lambda wxid, text: bot.send_text(wxid, text), "wechat")
            except Exception as e:
                print(f"[wx] 文档提醒检查异常: {e}", file=sys.stderr)
                log.exception("文档提醒检查异常")
            _backup_tick()
            _time.sleep(600)

    threading.Thread(target=_reminder_loop, daemon=True, name="doc-reminder").start()

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n[wechat_ilink] 已停止。")


# ── 模式 2: 测试 (命令行交互) ───────────────────────────────

def run_test() -> None:
    """本地命令行测试，无需微信。"""
    print("Family Assistant — 微信通道测试模式")
    print("全量上下文 Agent，跟 CodeWhale 一样的工作方式。")
    llm_ready = bool(os.environ.get("DEEPSEEK_API_KEY"))
    print(f"LLM: {'已启用' if llm_ready else '未配置 — 设置 DEEPSEEK_API_KEY'}")
    print("-" * 40)
    agent = Agent()
    while True:
        try:
            msg = input("微信> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in ("quit", "exit", "q"):
            break
        reply = agent.handle(msg, member="本地测试")
        print(f"助手> {reply}")
        print()


# ── 入口 ────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="微信 iLink Bot 传输层")
    parser.add_argument("--mode", choices=["run", "test"],
                        default="run", help="运行模式 (默认: run)")
    parser.add_argument("--relogin", action="store_true",
                        help="重新扫码登录（忽略已有凭据）")
    parser.add_argument("--debug", action="store_true",
                        help="开启调试日志（写 data/bot_debug.log，默认关）")
    args = parser.parse_args()
    setup_logging(args.debug)

    if args.mode == "test":
        run_test()
    else:
        run_bot(relogin=args.relogin)


if __name__ == "__main__":
    main()
