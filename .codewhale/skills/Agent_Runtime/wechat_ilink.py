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

    # 调试日志默认开（写 data/bot_debug.log）；关闭用 --no-debug
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --no-debug

安全:
    所有 CLI 调用受同目录 agent_core.py 白名单约束。
    凭据加密存储在 data/wechat_creds.json，不对外传输。
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import OrderedDict
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import logging

from agent_core import Agent, setup_logging, split_reply as _split_reply
from members import resolve
from transport_base import (Transport, with_quote as _with_quote,
                            knowking_deliver as _knowking_deliver)
import paths as _paths

log = logging.getLogger("familyassist.wechat")

# 凭据存储路径（跟随 data_root；备份硬排除任何含 "creds" 的文件名）
CREDS_FILE = _paths.data_root() / "wechat_creds.json"


_LOCK_PORT = 47831  # 单实例锁端口（仅 localhost，不对外）
_LOCK_SOCK = None   # 持有的锁 socket；进程退出/崩溃时 OS 自动释放


def _acquire_single_instance_lock(port: int = _LOCK_PORT) -> bool:
    """单实例锁：绑定 localhost 端口。双开 Bot 会各自轮询同一账号 → 每条消息处理两次、回复两次。"""
    global _LOCK_SOCK
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        return False
    s.listen(1)
    _LOCK_SOCK = s
    return True


_RECENT_MSGS: "OrderedDict[str, str]" = OrderedDict()
_RECENT_MSGS_CAP = 200
_RECENT_MSGS_FILE = ROOT / "data" / "wechat_recent_msgs.json"


def _remember_msg(message_id, text, persist_file=None) -> None:
    """缓存近期消息 message_id → 文本，供引用反查。超出上限逐出最旧。"""
    if not message_id or not text:
        return
    key = str(message_id)
    _RECENT_MSGS[key] = text
    _RECENT_MSGS.move_to_end(key)
    while len(_RECENT_MSGS) > _RECENT_MSGS_CAP:
        _RECENT_MSGS.popitem(last=False)
    if persist_file is not None:
        _save_recent_msgs(persist_file)


def _save_recent_msgs(path=None) -> None:
    """持久化缓存（bot 重启后仍可反查引用）。失败仅记录，不影响消息处理。"""
    p = Path(path) if path else _RECENT_MSGS_FILE
    try:
        p.write_text(json.dumps(_RECENT_MSGS, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.exception("近期消息缓存写入失败（忽略）")


def _load_recent_msgs(path=None) -> None:
    """启动时恢复缓存。文件缺失/损坏静默跳过。"""
    p = Path(path) if path else _RECENT_MSGS_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        if isinstance(v, str):
            _remember_msg(k, v)


# bot 出站回复：服务端不回传 message_id（send 响应 {}，轮询不回显 BOT 消息，
# 均实测 2026-07-10），引用 bot 回复只能按时间对齐 —— 记录每次发送的时间戳+文本。
_SENT_REPLIES: list = []          # [[ts_ms, text], ...] 按发送顺序
_SENT_REPLIES_CAP = 100
_SENT_REPLIES_FILE = ROOT / "data" / "wechat_sent_msgs.json"
_SENT_MATCH_WINDOW_MS = 15_000    # 引用时间戳与发送时间允许的最大偏差


def _remember_sent(text: str, ts_ms=None, persist_file=None) -> None:
    """记录一条 bot 出站文字（发送时刻 + 内容），供引用时间戳匹配。"""
    if not text:
        return
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    _SENT_REPLIES.append([int(ts_ms), text])
    del _SENT_REPLIES[:-_SENT_REPLIES_CAP]
    if persist_file is not None:
        try:
            Path(persist_file).write_text(
                json.dumps(_SENT_REPLIES, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.exception("出站消息记录写入失败（忽略）")


def _load_sent_replies(path=None) -> None:
    """启动时恢复出站记录。文件缺失/损坏静默跳过。"""
    p = Path(path) if path else _SENT_REPLIES_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(data, list):
        _SENT_REPLIES.extend(
            [int(e[0]), e[1]] for e in data
            if isinstance(e, list) and len(e) == 2 and isinstance(e[1], str))
        del _SENT_REPLIES[:-_SENT_REPLIES_CAP]


def _match_sent_by_time(ts_ms: int, window_ms: int = _SENT_MATCH_WINDOW_MS):
    """按时间戳找最接近的 bot 出站回复；偏差超窗口返回 None。"""
    best, best_diff = None, window_ms + 1
    for sent_ts, text in _SENT_REPLIES:
        diff = abs(sent_ts - ts_ms)
        if diff <= window_ms and diff < best_diff:
            best, best_diff = text, diff
    return best


def _quoted_text(raw_item: dict):
    """解析引用消息的原文。

    iLink 实际报文（实测 2026-07-10）里 ref_msg 只带被引消息的 msg_id 和
    create_time_ms，没有 title/内容——SDK 的 quoted_title 因此恒为空。
    这里先兼容 title（万一未来补上），否则拿 msg_id 反查本进程近期消息缓存；
    查不到（引用 bot 自己的回复、或重启前的消息）退化为时间占位，
    让 agent 至少知道用户在回复某条历史消息。
    """
    ref = raw_item.get("ref_msg") or {}
    title = ref.get("title")
    if title:
        return title
    item = ref.get("message_item") or {}
    rid = str(item.get("msg_id") or "")
    if not rid:
        return None
    cached = _RECENT_MSGS.get(rid)
    if cached:
        return cached
    ts = item.get("create_time_ms") or 0
    if ts:
        sent = _match_sent_by_time(ts)
        if sent:
            body = sent[:200] + ("…" if len(sent) > 200 else "")
            return f"我此前的回复「{body}」"
        log.debug("引用未命中: msg_id=%s create_time_ms=%s", rid, ts)
        when = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        return f"{when} 的一条消息（原文不可见，可能是我此前的回复）"
    return "一条历史消息（原文不可见）"


class WeChatTransport(Transport):
    """微信：target = 收到的 msg（reply_* 回原会话）或 wxid（后台推送，经 bot.send_text）。"""
    channel = "wechat"
    tag = "wx"

    def __init__(self, bot=None, agent=None):
        super().__init__(agent)
        self.bot = bot

    def send_text(self, target, text: str) -> None:
        if hasattr(target, "reply_text"):
            target.reply_text(text)
        else:
            self.bot.send_text(target, text)

    def send_photo(self, target, path: str) -> None:
        target.reply_image(path)

    def send_document(self, target, path: str) -> None:
        target.reply_file(path)

    def after_text_sent(self, target, text: str) -> None:
        _remember_sent(text, persist_file=_SENT_REPLIES_FILE)   # bot 出站记录（引用反查）

    def save_incoming(self, msg, member: str, ext: str):
        """来件落盘到成员 inbox；失败返回 None（on_media 会提示重发）。"""
        try:
            path = self.inbox_path(member, ext)
            msg.save(str(path))
            self.mark_dirty()
            return path
        except Exception:
            log.exception("来件保存失败")
            return None


# ── 模式 1: 运行 Bot ────────────────────────────────────────

def run_bot(relogin: bool = False) -> None:
    """扫码登录并启动长轮询 Bot。"""
    from weixin_ilink import WeixinBot, login

    if not _acquire_single_instance_lock():
        print("[wechat_ilink] 已有 Bot 实例在运行（单实例锁被占用），本进程退出。")
        print("  双开会导致每条消息被处理两次、回复两次。")
        sys.exit(1)

    _load_recent_msgs()    # 重启后仍能反查引用的历史消息
    _load_sent_replies()   # bot 出站记录（引用 bot 回复按时间匹配）

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

    # 自愈：陈旧 cursor 会让 getupdates 持续返回 ret=-1，而 SDK 只特判 ret=-14
    # (SESSION_EXPIRED)，其余 ret 一律以同一个坏 cursor 无限重试 → 静默收不到消息。
    # 连续 3 次 ret=-1 就清空 cursor 并删除 .sync 文件，下一轮以空 buf 重新拉取自愈。
    _orig_poll = bot.client.poll
    _poll_neg1 = {"n": 0}

    def _poll_with_selfheal():
        resp = _orig_poll()
        if (resp.get("ret") or 0) == -1:
            _poll_neg1["n"] += 1
            if _poll_neg1["n"] >= 3:
                print("[wechat_ilink] poll ret=-1 连续 3 次 → 清空陈旧 cursor 自愈")
                log.warning("poll ret=-1 x3 → 重置 cursor 并删除 .sync 文件")
                bot.client.cursor = ""
                if bot._cursor_file:
                    try:
                        bot._cursor_file.unlink()
                    except OSError:
                        pass
                _poll_neg1["n"] = 0
        else:
            _poll_neg1["n"] = 0
        return resp

    bot.client.poll = _poll_with_selfheal

    t = WeChatTransport(bot)

    @bot.on_text
    def handle_text(msg):
        member = t.gate(msg.from_user)
        if member is None:
            return
        _remember_msg(msg.message_id, msg.text, persist_file=_RECENT_MSGS_FILE)
        print(f"[wx] 文字消息 from {msg.from_user}({member}): {msg.text[:60]}")
        t.on_text(msg, msg.from_user, member, msg.text, quoted=_quoted_text(msg.raw_item))

    @bot.on_image
    def handle_image(msg):
        member = t.gate(msg.from_user)
        if member is None:
            return
        print(f"[wx] 图片消息 from {msg.from_user}({member})")
        t.on_media(msg, msg.from_user, member, t.save_incoming(msg, member, ".jpg"))

    @bot.on_file
    def handle_file(msg):
        member = t.gate(msg.from_user)
        if member is None:
            return
        name = msg.file_name or ""
        if not name.lower().endswith(".pdf"):
            msg.reply_text(f"收到文件: {name}（暂不支持文件处理，PDF 可以）")
            return
        print(f"[wx] 文件消息 from {msg.from_user}({member}): {name}")
        t.on_media(msg, msg.from_user, member, t.save_incoming(msg, member, ".pdf"))

    # 其他消息类型：友好提示（未注册来源静默）
    @bot.on_voice
    def handle_voice(msg):
        if resolve("wechat", msg.from_user) is None:
            return
        msg.reply_text("目前不支持语音消息，请发文字或图片。")

    @bot.on_video
    def handle_video(msg):
        if resolve("wechat", msg.from_user) is None:
            return
        msg.reply_text("收到视频（暂不支持视频处理）")

    # weixin-ilink bot.run() 阻塞、无轮询钩子 → 后台线程：提醒+备份 600s、懂王投递 20s
    t.start_background_threads()

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
        text, imgs, docs = _split_reply(reply)
        for rel in imgs:
            print(f"助手> [图片] {rel}")
        for rel in docs:
            print(f"助手> [文件] {rel}")
        if text:
            print(f"助手> {text}")
        print()


# ── 入口 ────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="微信 iLink Bot 传输层")
    parser.add_argument("--mode", choices=["run", "test"],
                        default="run", help="运行模式 (默认: run)")
    parser.add_argument("--relogin", action="store_true",
                        help="重新扫码登录（忽略已有凭据）")
    parser.add_argument("--debug", action="store_true", default=True,
                        help="开启调试日志（写 data/bot_debug.log，默认开）")
    parser.add_argument("--no-debug", dest="debug", action="store_false",
                        help="关闭调试日志")
    args = parser.parse_args()
    setup_logging(args.debug)

    if args.mode == "test":
        run_test()
    else:
        run_bot(relogin=args.relogin)


if __name__ == "__main__":
    main()
