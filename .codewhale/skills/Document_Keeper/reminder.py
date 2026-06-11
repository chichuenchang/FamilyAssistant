"""
Document Keeper — 每日到期提醒

传输层在轮询循环里反复调 check_and_push(send_fn, channel)：
每频道每天最多推送一次，有到期未确认文档才推。无新进程、无定时器。
状态存 data/.doc_reminder_state（JSON：{频道: 最后运行日期}）；
推送失败不记状态，下一轮自动重试。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))                                   # 同目录 doc_db
sys.path.insert(0, str(HERE.parent / "Agent_Runtime"))          # 成员注册表

import doc_db
from members import load_members

STATE_FILE = ROOT / "data" / ".doc_reminder_state"


def due_message(db_path: str | None = None) -> str | None:
    """到期未确认文档的提醒文本；没有则返回 None。"""
    docs = [d for d in doc_db.due_documents(db_path=db_path) if not d["acknowledged"]]
    if not docs:
        return None
    lines = ["📋 文档到期提醒："]
    for d in docs:
        left = d["days_left"]
        when = f"已过期 {-left} 天" if left < 0 else f"{left} 天后到期（{d['expiry_date']}）"
        line = f"#{d['id']} {d['title']}（{d['doc_type']}）{when}"
        if d["action_note"]:
            line += f" — {d['action_note']}"
        lines.append(line)
    lines.append("处理完成后说\"确认 #编号\"即可不再重复提醒。")
    return "\n".join(lines)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def check_and_push(send_fn, channel: str, db_path: str | None = None) -> bool:
    """每频道每日一次：有到期未确认文档则推送给该频道所有已登记 id。

    send_fn(channel_id, text)；抛异常视为失败（不记状态，下一轮重试）。
    返回本次是否推送了消息。
    """
    today = date.today().isoformat()
    state = _load_state()
    if state.get(channel) == today:
        return False
    msg = due_message(db_path=db_path)
    if msg is None:
        state[channel] = today
        _save_state(state)
        return False
    ids = [cid for bindings in load_members().values()
           for cid in (bindings.get(channel) or [])]
    try:
        for cid in ids:
            send_fn(cid, msg)
    except Exception as e:
        print(f"[doc-reminder] 推送失败({channel}): {e}", file=sys.stderr)
        return False
    state[channel] = today
    _save_state(state)
    return bool(ids)
