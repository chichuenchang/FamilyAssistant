"""对话长期存档：每轮追加一行到 data/.state/chat_history.jsonl，供 chat_history 工具回读。

每行 {ts, channel, user, said, reply}：said = 用户亲手打的字，reply = 最终回复（不含工具
调用/结果）。只追加不清理，重启照常续写。.state 不入 git、不进云备份（backup_sync）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import paths as _paths

CLIP = 500    # 回读时每条文字最多字符数
EMPTY = "（没有对话记录）"

_log = logging.getLogger("familyassist.agent")


def _path() -> Path:
    return _paths.state_file("chat_history.jsonl")


def append(channel: str, user, said: str, reply: str) -> None:
    """追加一轮；写失败只记日志，不影响回复。"""
    row = {"ts": datetime.now().isoformat(timespec="seconds"), "channel": channel or "",
           "user": str(user), "said": said, "reply": reply}
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with _path().open("a+b") as f:
            if f.seek(0, 2):
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    f.write(b"\n")   # 上次写入中崩溃留的半行无换行：先补，免得本行接上去一起作废
            f.write(data)
    except OSError:
        _log.warning("对话存档写入失败", exc_info=True)


def _rows(channel: str, user: str):
    try:
        with _path().open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue   # 半行（写入中崩溃）跳过
                if r.get("user") == user and r.get("channel", "") == channel:
                    yield r
    except FileNotFoundError:
        return


def _clip(text: str) -> str:
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def read(channel: str, user, query: str = "", limit: int = 20) -> str:
    """该用户最近 limit 轮（旧在前），query 非空时只留问或答含该词的轮（不分大小写）。"""
    needle = query.strip().lower()
    turns = [r for r in _rows(channel or "", str(user))
             if not needle or needle in r.get("said", "").lower()
             or needle in r.get("reply", "").lower()]
    turns = turns[-max(1, min(int(limit or 20), 50)):]
    if not turns:
        return EMPTY
    return "\n".join(
        f"[{r.get('ts', '')[:16].replace('T', ' ')}] 用户: {_clip(r.get('said', ''))}\n"
        f"    助手: {_clip(r.get('reply', '')) or '（无回复）'}" for r in turns)
