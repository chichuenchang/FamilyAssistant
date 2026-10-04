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

    # 重新扫码（默认账号）
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --relogin

    # 新增/重扫另一个账号（同一进程同时服务全部已登录账号）
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --account mom

    # 调试日志默认开（写 data/.state/bot_debug.log）；关闭用 --no-debug
    python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run --no-debug

安全:
    所有 CLI 调用受同目录 agent_core.py 白名单约束。
    凭据加密存储在 data/.state/wechat_creds.json，不对外传输。
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
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
from transport_base import Transport, with_quote as _with_quote
import paths as _paths

log = logging.getLogger("familyassist.wechat")

# 凭据存储路径（跟随 data_root；备份硬排除任何含 "creds" 的文件名）
# 多账号：默认账号 wechat_creds.json，其余 wechat_creds_<标签>.json；SDK cursor 各自紧贴为 .sync
CREDS_FILE = _paths.state_file("wechat_creds.json")
_paths.state_file("wechat_creds.json.sync")  # SDK 的 cursor 文件紧贴凭据，随之迁入 .state/
_LABEL_RE = re.compile(r"[A-Za-z0-9_-]+")


def creds_path(label=None, state_dir=None) -> Path:
    """账号标签 → 凭据文件。None/"default" = 原单账号文件。"""
    d = Path(state_dir) if state_dir else CREDS_FILE.parent
    if label in (None, "default"):
        return d / "wechat_creds.json"
    if not _LABEL_RE.fullmatch(label):
        raise ValueError(f"账号标签只能含字母/数字/_/-: {label!r}")
    return d / f"wechat_creds_{label}.json"


def creds_files(state_dir=None) -> dict:
    """已登录的全部账号 {标签: 凭据文件}（.sync cursor 不算）。"""
    d = Path(state_dir) if state_dir else CREDS_FILE.parent
    out = {}
    for p in sorted(d.glob("wechat_creds*.json")):
        stem = p.stem
        if stem == "wechat_creds":
            out["default"] = p
        elif stem.startswith("wechat_creds_"):
            out[stem[len("wechat_creds_"):]] = p
    return out


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
_RECENT_MSGS_FILE = _paths.state_file("wechat_recent_msgs.json")


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
_SENT_REPLIES: list = []          # [[ts_ms, user, text], ...] 按发送顺序；user "" = 旧格式/不分用户
_SENT_REPLIES_CAP = 100
_SENT_REPLIES_FILE = _paths.state_file("wechat_sent_msgs.json")
_SENT_MATCH_WINDOW_MS = 15_000    # 引用时间戳与发送时间允许的最大偏差


def _remember_sent(text: str, ts_ms=None, persist_file=None, user: str = "") -> None:
    """记录一条 bot 出站文字（发送时刻 + 收件人 + 内容），供引用时间戳匹配。
    按收件人分开：多账号/多人同时段的回复不串到别人的引用里。"""
    if not text:
        return
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    _SENT_REPLIES.append([int(ts_ms), str(user or ""), text])
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
        for e in data:
            if not isinstance(e, list) or not e or not isinstance(e[-1], str):
                continue
            if len(e) == 2:     # 旧格式 [ts, text]
                _SENT_REPLIES.append([int(e[0]), "", e[1]])
            elif len(e) == 3:
                _SENT_REPLIES.append([int(e[0]), str(e[1]), e[2]])
        del _SENT_REPLIES[:-_SENT_REPLIES_CAP]


def _match_sent_by_time(ts_ms: int, window_ms: int = _SENT_MATCH_WINDOW_MS, user=None):
    """按时间戳找最接近的 bot 出站回复；给 user 则只看发给他的（及旧格式不分用户的）；
    偏差超窗口返回 None。"""
    best, best_diff = None, window_ms + 1
    for sent_ts, to, text in _SENT_REPLIES:
        if user is not None and to and to != str(user):
            continue
        diff = abs(sent_ts - ts_ms)
        if diff <= window_ms and diff < best_diff:
            best, best_diff = text, diff
    return best


def _quoted_text(raw_item: dict, user=None):
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
        sent = _match_sent_by_time(ts, user=user)
        if sent:
            body = sent[:200] + ("…" if len(sent) > 200 else "")
            return f"我此前的回复「{body}」"
        log.debug("引用未命中: msg_id=%s create_time_ms=%s", rid, ts)
        when = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        return f"{when} 的一条消息（原文不可见，可能是我此前的回复）"
    return "一条历史消息（原文不可见）"


class WeChatTransport(Transport):
    """微信：target = 收到的 msg（reply_* 回原会话）或 wxid（后台推送，经 bot.send_text）。
    多账号共用一个实例（后台节拍只跑一份，提醒不重发）；推送按 wxid 找对的 bot。"""
    channel = "wechat"
    tag = "wx"

    def __init__(self, bots, agent=None):
        super().__init__(agent)
        self.bots = list(bots)

    def _bot_for(self, user):
        """后台推送用哪个 bot：该用户发过消息的那个（context_token 存在 bot 自己的 _ctx_cache）。
        都没见过 → 第一个（SDK 照旧报"没有 context_token"）。"""
        for b in self.bots:
            if user in b._ctx_cache:
                return b
        return self.bots[0]

    def send_text(self, target, text: str) -> None:
        if hasattr(target, "reply_text"):
            target.reply_text(text)
        else:
            self._bot_for(target).send_text(target, text)

    def send_photo(self, target, path: str) -> None:
        target.reply_image(path)

    def send_document(self, target, path: str) -> None:
        target.reply_file(path)

    def after_text_sent(self, target, text: str) -> None:
        user = getattr(target, "from_user", target)
        _remember_sent(text, persist_file=_SENT_REPLIES_FILE, user=user)   # bot 出站记录（引用反查）

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

def _self_heal_poll(bot) -> None:
    """自愈：陈旧 cursor 会让 getupdates 持续返回 ret=-1，而 SDK 只特判 ret=-14
    (SESSION_EXPIRED)，其余 ret 一律以同一个坏 cursor 无限重试 → 静默收不到消息。
    连续 3 次 ret=-1 就清空 cursor 并删除 .sync 文件，下一轮以空 buf 重新拉取自愈。"""
    _orig_poll = bot.client.poll
    _poll_neg1 = {"n": 0}

    def _poll_with_selfheal():
        resp = _orig_poll()
        if (resp.get("ret") or 0) == -1:
            _poll_neg1["n"] += 1
            if _poll_neg1["n"] >= 3:
                print(f"[wechat_ilink] {bot.account_id} poll ret=-1 连续 3 次 → 清空陈旧 cursor 自愈")
                log.warning("%s poll ret=-1 x3 → 重置 cursor 并删除 .sync 文件", bot.account_id)
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


def _wire(bot, t: WeChatTransport) -> None:
    """一个账号的 bot 挂上自愈轮询 + 全部消息处理（各账号同一套，共用 t）。"""
    _self_heal_poll(bot)

    @bot.on_text
    def handle_text(msg):
        member = t.gate(msg.from_user)
        if member is None:
            return
        _remember_msg(msg.message_id, msg.text, persist_file=_RECENT_MSGS_FILE)
        print(f"[wx] 文字消息 from {msg.from_user}({member}): {msg.text[:60]}")
        t.on_text(msg, msg.from_user, member, msg.text, quoted=_quoted_text(msg.raw_item, user=msg.from_user))

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


_POLL_DONE = object()


def serve(bots) -> None:
    """每个 bot 一条轮询线程，消息汇入队列，由调用线程逐条分发（串行）。
    Agent/引用缓存/状态文件原本只被单线程碰，多账号并发处理会抢 → 宁可排队。
    不用 bot.run()：它阻塞且装 signal 处理器（只许主线程）。
    全部轮询结束（测试）或 Ctrl+C 返回/抛出；退出时 stop 全部 bot。"""
    q: queue.Queue = queue.Queue()

    def _poll(bot):
        try:
            for msg in bot.messages():
                q.put((bot, msg))
        except Exception:
            log.exception("轮询线程异常退出")
        finally:
            q.put((bot, _POLL_DONE))

    for i, b in enumerate(bots):
        threading.Thread(target=_poll, args=(b,), daemon=True, name=f"wx-poll-{i}").start()
    live = len(bots)
    try:
        while live:
            try:   # 带超时：Windows 上无限期 get 收不到 Ctrl+C
                bot, msg = q.get(timeout=1)
            except queue.Empty:
                continue
            if msg is _POLL_DONE:
                live -= 1
            else:
                bot._dispatch(msg)
    finally:
        for b in bots:
            b.stop()


def run_bot(relogin: bool = False, account: str | None = None) -> None:
    """加载全部已登录账号（缺则扫码）并启动长轮询。account = 本次扫码写哪个账号的凭据。"""
    from weixin_ilink import WeixinBot

    if not _acquire_single_instance_lock():
        print("[wechat_ilink] 已有 Bot 实例在运行（单实例锁被占用），本进程退出。")
        print("  双开会导致每条消息被处理两次、回复两次。")
        sys.exit(1)

    _load_recent_msgs()    # 重启后仍能反查引用的历史消息
    _load_sent_replies()   # bot 出站记录（引用 bot 回复按时间匹配）

    # 要求重新登录 / 指定账号尚无凭据 / 一个账号都没有 → 扫码
    target = creds_path(account)
    if relogin or not creds_files() or (account and not target.exists()):
        print(f"[wechat_ilink] 等待扫码（账号 {account or 'default'}）...")
        print("  将打开二维码，请用微信扫码授权。")
        print("  注意：需要在微信 ClawBot 插件中先启用。")
        print()
        WeixinBot.from_login(save_to=str(target))

    t = WeChatTransport([])
    for label, path in creds_files().items():
        bot = WeixinBot(credentials_file=str(path))
        print(f"[wechat_ilink] 已加载账号 {label}: {bot.account_id}")
        _wire(bot, t)
        t.bots.append(bot)
    print("[wechat_ilink] 等待微信消息... (Ctrl+C 停止)")

    # 轮询线程阻塞在 SDK 里、无轮询钩子 → 后台线程：提醒+备份 600s、懂王投递 20s（全账号一份）
    t.start_background_threads()

    try:
        serve(t.bots)
    except KeyboardInterrupt:
        print("\n[wechat_ilink] 已停止。")


# ── 模式 2: 测试 (命令行交互) ───────────────────────────────

def run_test() -> None:
    """本地命令行测试，无需微信。"""
    print("Family Assistant — 微信通道测试模式")
    print("全量上下文 Agent，跟 CodeWhale 一样的工作方式。")
    import llm_client
    print(llm_client.ready_note())
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
    parser.add_argument("--account", metavar="LABEL",
                        help="扫码写入哪个账号（wechat_creds_<LABEL>.json；无凭据即扫码新增）；"
                             "运行时总是加载全部账号")
    parser.add_argument("--debug", action="store_true", default=True,
                        help="开启调试日志（写 data/.state/bot_debug.log，默认开）")
    parser.add_argument("--no-debug", dest="debug", action="store_false",
                        help="关闭调试日志")
    args = parser.parse_args()
    setup_logging(args.debug)

    if args.mode == "test":
        run_test()
    else:
        run_bot(relogin=args.relogin, account=args.account)


if __name__ == "__main__":
    main()
