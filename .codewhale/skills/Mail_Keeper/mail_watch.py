"""
Mail Keeper — 新邮件播报（FAST_TICKS 钩子，按成员 opt-in）。

为什么轮询而不用 Gmail 推送：见 SKILL.md「新邮件事件」。
传输层每 ~20 秒调 check_and_push(push_fn, channel)：对每个 mail.watch=true 的成员调
provider.history_since(游标)，新进收件箱的信只播报 **发件人 + 主题**，不带正文
（正文是外部内容/注入面，且没人要求就不该把它甩进聊天）。播报不经 LLM。

该播报哪些：命中 mail_rules 忽略规则的丢掉，其余全推（规则表空 = 全推）。
规则由用户教（"这种以后别推" → Agent 调 mail_mute），见 mail_rules.py。
播报过的信头记进 .mail_last_push.json：推送不经 LLM，用户回头说"别推这种"时
Agent 才有得可查（mail_last_push 工具）。

游标存 data/.state/.mail_history.json：{频道: {成员: {history_id, at}}}
（点前缀 = 运行时瞬态，不进备份）。按频道各存一份，微信/Telegram 都能收到同一封。

确定性规则：
  - 首次见到某(频道,成员) → 用 getProfile 的 historyId 落游标，不播报历史邮件
  - 游标过旧（history_since 返回 rows=None）→ 存新起点，这轮不播报
  - 推送或 API 失败 → 游标不动，下一轮重来（宁可重播一次，不可漏）
  - MIN_POLL_S 节流：传输层节拍比这更密也不会多打 Gmail
  - label 规则先用 history 带回的标签过一遍 → 命中的连信头都不取（省配额）
  - 一轮最多取 MAX_META 封信头；更早的只计入条数
  - 一条播报最多 MAX_LINES 行，其余只报条数（订阅邮件爆量不刷屏）
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import jsonfile
import members as _members
import paths as _paths

import mail_rules as _rules

_log = logging.getLogger("familyassist.mail")

STATE_NAME = ".mail_history.json"
LAST_PUSH_NAME = ".mail_last_push.json"
MAX_LINES = 5
MAX_META = 25
MIN_POLL_S = 15
LAST_PUSH_KEEP = 20


def store_path() -> Path:
    return _paths.state_file(STATE_NAME)


def last_push_path() -> Path:
    return _paths.state_file(LAST_PUSH_NAME)


def _load() -> dict:
    return jsonfile.load_dict(store_path())


def _save(d: dict) -> None:
    jsonfile.save(store_path(), d)


def last_push(member: str) -> list[dict]:
    """最近播报过的信头（新的在前），供 mail_last_push 工具回放给 Agent。"""
    items = (jsonfile.load_dict(last_push_path()).get(member) or {}).get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def record_last_push(member: str, metas: list[dict]) -> None:
    d = jsonfile.load_dict(last_push_path())
    items = [{"id": m.get("id", ""), "from": m.get("from", ""),
              "subject": m.get("subject", ""), "labels": list(m.get("labels") or [])}
             for m in metas]
    d[member] = {"at": time.time(), "items": (items + last_push(member))[:LAST_PUSH_KEEP]}
    jsonfile.save(last_push_path(), d)


def watch_enabled(member: str, members_path: Path | None = None) -> bool:
    """该成员是否要播报新邮件：mail 块 enabled + watch 都为 true（默认不播报）。"""
    pref = _members.mail_pref(member, members_path)
    return bool(pref and pref["enabled"] and pref["watch"])


def format_push(metas: list[dict], extra: int = 0) -> str:
    """播报文本。发件人/主题是外部内容，原样转述，不做任何解释。"""
    lines = [f"📬 新邮件 {len(metas) + extra} 封："]
    for m in metas:
        lines.append(f"- {m.get('from') or '(无发件人)'}｜{m.get('subject') or '(无主题)'}")
    if extra:
        lines.append(f"…还有 {extra} 封（说\"查邮箱\"我再细看）")
    return "\n".join(lines)


def _keep(rows: list[dict], mod, prefix: str, rules: list[dict]) -> tuple[list[dict], int]:
    """规则过滤后要播报的信头（最新的在最后）+ 只计数不列出的条数。

    两道：先用 history 带回的 labels 挡 label 规则（不花配额取信头），
    再对最新 MAX_META 封取信头按发件人/域/主题规则挡。
    """
    live = [r for r in rows if not (rules and _rules.match(r, rules))]
    head = live[-MAX_META:]
    metas: list[dict] = []
    for r in head:
        meta = mod.message_meta(r["id"], prefix)
        if rules and _rules.match(meta, rules):
            continue
        metas.append(meta)
    extra = len(live) - len(head) + max(0, len(metas) - MAX_LINES)
    return metas[-MAX_LINES:], extra


def check_and_push(push_fn: Callable[[str, str], object], channel: str, *,
                   provider_for: Callable[[str], tuple], members_path: Path | None = None,
                   now: float | None = None) -> int:
    """轮询本频道各成员邮箱，有新信则推送。返回播报的成员数。

    provider_for(member) → ((provider 模块, 凭据前缀), "") 或 (None, 错误文本)
    （由 agent_tools._provider 提供，避免本模块反向依赖 manifest）。
    单个成员出错只记日志，不影响其余。
    """
    now = time.time() if now is None else now
    state = _load()
    chan = dict(state.get(channel) or {})
    pushed = 0
    dirty = False
    for member, bindings in _members.load_members(members_path).items():
        if not isinstance(bindings, dict):
            continue
        ids = [str(i) for i in (bindings.get(channel) or [])]
        if not ids or not watch_enabled(member, members_path):
            continue
        cur = chan.get(member) or {}
        if now - float(cur.get("at") or 0) < MIN_POLL_S:
            continue
        got, err = provider_for(member)
        if err:
            continue                      # 没配/没凭据：工具那边已会如实告知用户
        mod, prefix = got
        try:
            hid = str(cur.get("history_id") or "")
            if not hid:                   # 首次：只落游标，不播报历史
                chan[member] = {"history_id": mod.profile_history_id(prefix), "at": now}
                dirty = True
                continue
            rows, new_hid = mod.history_since(hid, prefix)
            if rows is None:              # 游标过旧 → 重新起点，这轮不播报
                chan[member] = {"history_id": new_hid, "at": now}
                dirty = True
                continue
            metas, extra = _keep(rows, mod, prefix, _rules.load(member))
            if not metas:                 # 全被忽略规则挡掉（或本来就没新信）
                chan[member] = {"history_id": new_hid, "at": now}
                dirty = True
                continue
            text = format_push(metas, extra)
            for cid in ids:
                push_fn(cid, text)
            record_last_push(member, metas)
        except Exception:
            _log.exception("新邮件播报失败（游标不动，下轮重试）: %s/%s", channel, member)
            continue
        chan[member] = {"history_id": new_hid, "at": now}
        dirty = True
        pushed += 1
    if dirty:
        state[channel] = chan
        _save(state)
    return pushed
