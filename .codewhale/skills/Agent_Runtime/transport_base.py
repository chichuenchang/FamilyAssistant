"""频道传输层共用生命周期（微信 / Telegram / 未来频道共用）。

子类只做三件事：实现 send_text / send_photo / send_document，把 SDK 收到的消息
翻译成 on_text / on_media 调用，以及决定后台节拍怎么跑（轮询循环内调 background_tick，
或 start_background_threads 起守护线程）。闸门、来图落盘、来件攒到下条文字、哨兵拆分投递、异常兜底在这里，
各频道零复制。节拍钩子来自各 skill manifest（MESSAGE_TICKS / SLOW_TICKS / FAST_TICKS，
契约见 skill_registry.py）；本模块不 import 任何 skill。

target = 子类 send_* 认得的投递目标（Telegram 是 chat_id；微信可为 msg 或 wxid）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path


import backup_hook
import paths as _paths
import tool_runtime as rt
from agent_core import (Agent, REGISTRY, is_clear_command, is_command, member_inbox_dir,
                        split_reply)
from members import resolve
from skill_registry import run_ticks

log = logging.getLogger("familyassist.transport")


def with_quote(text: str, quoted) -> str:
    """被引用内容套围栏前置注入正文，让 Agent 看到用户在回复什么（引用可能是转发来的外部文本）。"""
    if quoted:
        return f"[引用]\n{rt.fence(str(quoted), 'quote')}\n{text}"
    return text


def sendable(rel: str) -> Path | None:
    """哨兵路径闸门：须存在且在 data_root 内；否则 None。"""
    try:
        root = _paths.data_root().resolve()
        ap = _paths.resolve_rel(rel).resolve()
        if ap.exists() and ap.is_relative_to(root):
            return ap
    except Exception:
        pass
    return None


def stamp_name(channel: str, ext: str, now: datetime) -> str:
    """来件文件名 <ts>_<6位随机>_<channel><ext>：相册/连发同秒落盘不互相覆盖。"""
    return f"{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{channel}{ext}"


class Transport:
    channel = ""   # members.json 频道键（"telegram"/"wechat"）；也是 Agent(channel=) 与后台投递路由键
    tag = ""       # 控制台前缀，如 "tg"/"wx"

    def __init__(self, agent: Agent | None = None):
        self._agent = agent
        self._pending: dict[str, list[tuple[str, float]]] = {}   # 用户 id → [(来件路径, 收到时刻)]
        self._pending_lock = threading.Lock()      # 频道回调可能并发

    @property
    def agent(self) -> Agent:
        """惰性构造：投递/闸门等不需要 LLM 的路径不为此付启动成本。"""
        if self._agent is None:
            self._agent = Agent(channel=self.channel)
        return self._agent

    # ── 子类实现 ────────────────────────────────────────────
    def send_text(self, target, text: str) -> None:
        raise NotImplementedError

    def send_photo(self, target, path: str) -> None:
        raise NotImplementedError

    def send_document(self, target, path: str) -> None:
        raise NotImplementedError

    def after_text_sent(self, target, text: str) -> None:
        """钩子：文字已发出（微信用它记出站消息供引用反查）。"""

    # ── 闸门 / 节拍 ────────────────────────────────────────
    def gate(self, channel_id) -> str | None:
        """未注册来源 → None（不回复、不进 LLM，本地留一行日志）。"""
        member = resolve(self.channel, str(channel_id))
        if member is None:
            print(f"[{self.tag}] 忽略未注册来源 {channel_id}")
        return member

    def tick(self) -> None:
        """已注册成员来消息 → 各 skill 的 MESSAGE_TICKS（节流各自把关）。"""
        run_ticks(REGISTRY.message_ticks)

    def inbox_path(self, member: str, ext: str) -> Path:
        """来件暂存路径 data/<成员>/inbox/YYYY-MM/<stamp_name>。"""
        now = datetime.now()
        return member_inbox_dir(member, now) / stamp_name(self.channel, ext, now)

    @staticmethod
    def mark_dirty() -> None:
        backup_hook.mark_dirty()

    # ── 收消息 ──────────────────────────────────────────────
    def on_text(self, target, user, member: str, text: str, quoted=None,
                media: list[str] | None = None) -> None:
        """攒着的来件随这条文字一起交 Agent（文字即指令）；没有来件走普通对话。
        攒超 Agent.idle_clear_seconds 的来件作废（同闲置清上下文：隔久了多半是新话题）。
        命令（/clear、/model…）绕开来件照常执行：/clear 连来件一起清，其余命令来件继续等。
        来件与文字是否相关由 LLM 判（无关即作废）；交出去就不再攒，处理出错则放回。
        media 给定 = 附言自带的来件（Telegram 图/PDF 附言）：只配这些，攒着的不动。"""
        self.tick()
        key = str(user)
        held = self._take_held(key, text, media)
        media = [p for p, _ in held]
        log.debug("文字 from %s(%s) 引用=%s 来件=%d: %s", user, member, quoted or "-",
                  len(media), text)
        try:
            body = with_quote(text, quoted)
            if media:
                reply = self.agent.handle_media(media, body, user=key, member=member, said=text)
            else:
                reply = self.agent.handle(body, user=key, member=member, said=text)
            log.debug("文字回复 → %s", (reply or "")[:200])
            self.deliver(target, reply)
        except Exception as e:
            log.exception("文字处理出错")
            if held:   # 放回，用户重发指令即可，不必重传
                with self._pending_lock:
                    self._pending[key] = held + self._pending.get(key, [])
            self._safe_send(target, f"处理出错: {e}")

    def _take_held(self, key: str, text: str,
                   media: list[str] | None) -> list[tuple[str, float]]:
        """本条文字要带的来件 [(路径, 收到时刻)]，已剔超时。规则见 on_text。"""
        own = [(str(p), time.time()) for p in media or []]
        with self._pending_lock:
            if is_clear_command(text):
                self._pending.pop(key, None)
            if is_command(text):
                if own:   # 附言是命令：来件照攒
                    self._pending.setdefault(key, []).extend(own)
                return []
            held = own if media is not None else self._pending.pop(key, [])
        ttl = getattr(self.agent, "idle_clear_seconds", 0)
        if ttl <= 0:
            return held
        fresh = [h for h in held if time.time() - h[1] < ttl]
        if len(fresh) < len(held):
            log.debug("来件超时作废 %d 件 from %s", len(held) - len(fresh), key)
        return fresh

    def on_media(self, target, user, member: str, path: Path | str | None) -> None:
        """图片/PDF 已落盘 → 静默攒着，等用户下条文字指令（on_text）。path 为 None = 下载失败，
        照样提示——否则用户以为已收到。"""
        self.tick()
        if not path:
            self._safe_send(target, "文件下载失败，请重发。")
            return
        log.debug("来件 from %s(%s) → %s（待文字指令）", user, member, path)
        with self._pending_lock:
            self._pending.setdefault(str(user), []).append((str(path), time.time()))

    # ── 投递 ────────────────────────────────────────────────
    def deliver(self, target, reply: str) -> None:
        """拆哨兵：先发图，再发文档，最后发文字。单件失败仅记录，不影响其余。"""
        text, imgs, docs = split_reply(reply or "")
        for rel in imgs:
            ap = sendable(rel)
            if ap:
                try:
                    self.send_photo(target, str(ap))
                except Exception:
                    log.exception("发送图片失败（跳过）: %s", rel)
        for rel in docs:
            ap = sendable(rel)
            if ap:
                try:
                    self.send_document(target, str(ap))
                except Exception:
                    log.exception("发送文件失败（跳过）: %s", rel)
        if text:
            self.send_text(target, text)
            self.after_text_sent(target, text)

    def _safe_send(self, target, text: str) -> None:
        try:
            self.send_text(target, text)
        except Exception:
            log.exception("发送失败: %s", text[:80])

    # ── 后台节拍 ────────────────────────────────────────────
    def push_text(self, user, text: str) -> bool:
        """后台推送（提醒/懂王报告/新邮件）：按频道内用户 id 发。没送达 = 返回 False 或抛错
        （send_text 吞错只回 False 的频道如 Telegram 靠返回值；mail_watch 据此决定游标动不动）。"""
        return self.send_text(user, text) is not False

    def slow_tick(self) -> None:
        run_ticks(REGISTRY.slow_ticks, self.push_text, self.channel)

    def fast_tick(self) -> None:
        run_ticks(REGISTRY.fast_ticks, self.push_text, self.channel)

    def background_tick(self) -> None:
        """一轮后台工作：全部 SLOW_TICKS + FAST_TICKS（轮询型 SDK 每轮调一次）。"""
        self.slow_tick()
        self.fast_tick()

    def start_background_threads(self, slow_every: int = 600, fast_every: int = 20) -> None:
        """SDK 自带阻塞事件循环（无轮询钩子）时用：慢/快各一条守护线程。"""
        def _loop(fn, every):
            while True:
                fn()
                time.sleep(every)

        threading.Thread(target=_loop, args=(self.slow_tick, slow_every),
                         daemon=True, name="slow-tick").start()
        threading.Thread(target=_loop, args=(self.fast_tick, fast_every),
                         daemon=True, name="fast-tick").start()
