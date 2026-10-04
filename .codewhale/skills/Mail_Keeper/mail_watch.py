"""
Mail Keeper — 新邮件播报（FAST_TICKS 钩子，按成员 opt-in）。设计：docs/superpowers/specs/2026-10-04-mail-triage-design.md

为什么轮询而不用 Gmail 推送：见 SKILL.md「新邮件事件」。
传输层每 ~20 秒调 tick：本频道没 worker 在跑才起守护线程跑 check_and_push（LLM 要几秒到几十秒，
不能堵传输层轮询）。对每个 mail.watch=true 的成员调 provider.history_since(游标)。

该播报哪些（mail_rules 先，LLM 后）：
  - always 规则命中 → 推，不问 LLM；mute 命中 → 丢；同一封 always 胜
  - 其余整批交 mail_triage.judge → 需处理的推，附一行理由；LLM 挂了 → 全推，抬头标"未分拣"
  - 只推发件人 + 主题 + 理由，不带正文
没推的也记进 .mail_last_push.json（带原因）：播报不经 Agent，用户说"漏推了/别推这种"时
Agent 靠 mail_last_push 工具才查得到。

游标存 data/.state/.mail_history.<频道>.json：{成员: {history_id, at}}
（点前缀 = 运行时瞬态，不进备份）。按频道各存一份，微信/Telegram 都能收到同一封。
一频道一文件：各频道是独立进程，共用一个文件会互相用旧副本盖掉对方的游标。

确定性规则：
  - 首次见到某(频道,成员) → 用 getProfile 的 historyId 落游标，不播报历史邮件
  - 游标过旧（history_since 返回 rows=None）→ 存新起点，这轮不播报
  - API 失败、或该成员所有 id 都没推出去（push_fn 抛错或返回 False）→ 游标不动，
    下一轮重来（宁可重播一次，不可漏）；判决已缓存，不重付 LLM
  - MIN_POLL_S 节流（进程内存，不落盘）
  - 游标没变不写盘
  - 一轮总耗时超过 TICK_BUDGET_S → 剩下的成员留到下一轮
  - label mute 先用 history 带回的标签过一遍 → 命中的连信都不取（省配额、不进日志）；
    成员有非 label 的 always 规则时跳过这道（不取信头不知道它是不是 always）
  - 一轮最多取 MAX_META 封；更早的只计入条数
  - 一条播报最多 MAX_LINES 封，其余只报条数（订阅邮件爆量不刷屏）
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

import jsonfile
import members as _members
import paths as _paths

import mail_rules as _rules
import mail_triage as _triage

_log = logging.getLogger("familyassist.mail")

STATE_NAME = ".mail_history.{channel}.json"
LAST_PUSH_NAME = ".mail_last_push.json"
MAX_LINES = 5
MAX_META = 25
MIN_POLL_S = 15
LAST_PUSH_KEEP = 40
TICK_BUDGET_S = 20

_polled: dict[tuple[str, str], float] = {}    # (频道, 成员) → 上次轮询时刻
_lock = threading.Lock()
_running: set[str] = set()                     # 有 worker 在跑的频道


def store_path(channel: str) -> Path:
    return _paths.state_file(STATE_NAME.format(channel=channel))


def last_push_path() -> Path:
    return _paths.state_file(LAST_PUSH_NAME)


def _load(channel: str) -> dict:
    return jsonfile.load_dict(store_path(channel))


def _save(channel: str, d: dict) -> None:
    jsonfile.save(store_path(channel), d)


def _entry_items(entry) -> list[dict]:
    """一条成员记录里的信头列表（文件被手改坏也不炸）。"""
    items = (entry or {}).get("items")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def last_push(member: str) -> list[dict]:
    """最近经手的新邮件（新的在前）：{id, from, subject, labels, pushed, why}，供 mail_last_push 工具。"""
    return _entry_items(jsonfile.load_dict(last_push_path()).get(member))


def record(member: str, items: list[dict]) -> None:
    """同一封信各频道都会经手一次，按 id 去重只记一条。"""
    d = jsonfile.load_dict(last_push_path())
    old = _entry_items(d.get(member))
    known = {i.get("id") for i in old}
    new = [{"id": m.get("id", ""), "from": m.get("from", ""), "subject": m.get("subject", ""),
            "labels": list(m.get("labels") or []), "pushed": bool(m.get("pushed")),
            "why": m.get("why", "")}
           for m in reversed(items) if m.get("id") not in known]
    if not new:
        return
    d[member] = {"at": time.time(), "items": (new + old)[:LAST_PUSH_KEEP]}
    jsonfile.save(last_push_path(), d)


def watch_enabled(member: str, members_path: Path | None = None) -> bool:
    """该成员是否要播报新邮件：mail 块 enabled + watch 都为 true（默认不播报）。"""
    pref = _members.mail_pref(member, members_path)
    return bool(pref and pref["enabled"] and pref["watch"])


def format_push(items: list[dict], older: int = 0, sorted_ok: bool = True) -> str:
    """播报文本，只列最新 MAX_LINES 封。发件人/主题/理由是外部内容（理由由 LLM 读信产出），原样转述。"""
    shown = items[-MAX_LINES:]
    extra = len(items) - len(shown) + older
    head = "需处理" if sorted_ok else "新邮件"
    tail = "" if sorted_ok else "（未分拣）"
    lines = [f"📬 {head} {len(shown) + extra} 封{tail}："]
    for m in shown:
        lines.append(f"- {m.get('from') or '(无发件人)'}｜{m.get('subject') or '(无主题)'}")
        if m.get("why"):
            lines.append(f"  → {m['why']}")
    if extra:
        lines.append(f"…还有 {extra} 封（说\"查邮箱\"我再细看）")
    return "\n".join(lines)


def _sort(rows: list[dict], mod, prefix: str, rules: list[dict], chat
          ) -> tuple[list[dict], list[dict], int, bool]:
    """(要推的, 没推的, 超出 MAX_META 只计数的条数, 分拣是否成功)。顺序同 rows（旧→新）。"""
    if any(r.get("kind") != "label" for r in rules if (r.get("push") or "mute") == "always"):
        live = rows
    else:
        live = [r for r in rows if _rules.match(r, rules, "always") or not _rules.match(r, rules)]
    head = live[-MAX_META:]
    labels = {r["id"]: r.get("labels") or [] for r in head}
    mails = mod.get_messages([r["id"] for r in head], prefix)
    pending = []
    for m in mails:
        m["labels"] = labels.get(m["id"], [])
        if _rules.match(m, rules, "always"):
            m.update(pushed=True, why="")
        elif mute := _rules.match(m, rules):
            m.update(pushed=False, why=f"mute 规则 #{_rules.index_of(mute, rules)}")
        else:
            pending.append(m)
    verdicts, ok = _triage.judge(pending, chat=chat)
    for m in pending:
        act, why = verdicts[m["id"]]
        m.update(pushed=act, why=why if act else (f"无需处理：{why}" if why else "无需处理"))
    push = [m for m in mails if m["pushed"]]
    drop = [m for m in mails if not m["pushed"]]
    return push, drop, len(live) - len(head), ok


def _cursors(chan: dict) -> dict:
    """{成员: 游标}，用于判断这一轮有没有推进过（没变就不写盘）。"""
    return {m: v.get("history_id") for m, v in chan.items() if isinstance(v, dict)}


def _push_all(push_fn, ids: list[str], text: str) -> bool:
    """推给该成员在本频道的每个 id；至少一个送达才算成功。
    push_fn 抛错或返回 False = 没送达（Transport.push_text 的约定）。"""
    ok = False
    for cid in ids:
        try:
            ok = (push_fn(cid, text) is not False) or ok
        except Exception:
            _log.exception("新邮件播报推送失败: %s", cid)
    return ok


def check_and_push(push_fn: Callable[[str, str], object], channel: str, *,
                   provider_for: Callable[[str], tuple], members_path: Path | None = None,
                   now: float | None = None, chat=None) -> int:
    """轮询本频道各成员邮箱，有需处理的新信则推送。返回播报的成员数。同步；tick 放进线程跑。

    provider_for(member) → ((provider 模块, 凭据前缀), "") 或 (None, 错误文本)
    （由 agent_tools._provider 提供，避免本模块反向依赖 manifest）。
    chat 注入 llm_client.chat 的替身（测试用）。单个成员出错只记日志，不影响其余。
    """
    now = time.time() if now is None else now
    began = time.monotonic()
    chan = _load(channel)
    before = _cursors(chan)
    pushed = 0
    for member, bindings in _members.load_members(members_path).items():
        if not isinstance(bindings, dict):
            continue
        ids = [str(i) for i in (bindings.get(channel) or [])]
        if not ids or not watch_enabled(member, members_path):
            continue
        if now - _polled.get((channel, member), 0.0) < MIN_POLL_S:
            continue
        if time.monotonic() - began > TICK_BUDGET_S:
            break
        _polled[(channel, member)] = now
        cur = chan.get(member) if isinstance(chan.get(member), dict) else {}
        got, err = provider_for(member)
        if err:
            continue                      # 没配/没凭据：工具那边已会如实告知用户
        mod, prefix = got
        try:
            hid = str(cur.get("history_id") or "")
            if not hid:                   # 首次：只落游标，不播报历史
                chan[member] = {"history_id": mod.profile_history_id(prefix), "at": now}
                continue
            rows, new_hid = mod.history_since(hid, prefix)
            if rows is None:              # 游标过旧 → 重新起点，这轮不播报
                chan[member] = {"history_id": new_hid, "at": now}
                continue
            push, drop, older, ok = _sort(rows, mod, prefix, _rules.load(member), chat)
            if push and not _push_all(push_fn, ids, format_push(push, older, ok)):
                raise RuntimeError("所有 id 都推送失败")
            record(member, push + drop)
        except Exception:
            _log.exception("新邮件播报失败（游标不动，下轮重试）: %s/%s", channel, member)
            continue
        chan[member] = {"history_id": new_hid, "at": now}
        pushed += bool(push)
    if _cursors(chan) != before:
        _save(channel, chan)
    return pushed


def _spawn(fn) -> None:
    threading.Thread(target=fn, daemon=True, name="mail-watch").start()


def tick(push_fn, channel: str, *, provider_for, spawn=None) -> bool:
    """FAST_TICKS 钩子：本频道没 worker 在跑就起一个。返回是否起了。"""
    with _lock:
        if channel in _running:
            return False
        _running.add(channel)

    def run():
        try:
            check_and_push(push_fn, channel, provider_for=provider_for)
        except Exception:
            _log.exception("新邮件播报 worker 崩了: %s", channel)
        finally:
            with _lock:
                _running.discard(channel)

    try:
        (spawn or _spawn)(run)
    except Exception:
        _log.exception("新邮件播报线程启动失败: %s", channel)
        with _lock:
            _running.discard(channel)
        return False
    return True
