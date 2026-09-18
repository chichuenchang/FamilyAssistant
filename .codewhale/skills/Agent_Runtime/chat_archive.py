"""移出上下文的对话（闲置清空、/clear、预算裁剪）按用户存内存，供 chat_history 工具回读。

每轮只留用户原话 + 最终回复（工具调用/结果不留）。不落盘，进程重启即丢。
"""

from __future__ import annotations

from collections import defaultdict, deque

CAP = 100     # 每用户最多留多少轮，超出丢最旧
CLIP = 500    # 回读时每条文字最多字符数
EMPTY = "（没有更早的对话记录）"

_ARCHIVE: dict[str, deque] = defaultdict(lambda: deque(maxlen=CAP))


def stash(user, msgs: list[dict]) -> None:
    """整轮消息序列（user 开头）拆成 (问, 答) 存档；答 = 该轮最后一条非空 assistant 文字。"""
    q = _ARCHIVE[str(user)]
    ask, reply = None, ""
    for m in msgs:
        role, text = m.get("role"), (m.get("content") or "").strip()
        if role == "user":
            if ask is not None:
                q.append((ask, reply))
            ask, reply = text, ""
        elif role == "assistant" and text:
            reply = text
    if ask is not None:
        q.append((ask, reply))


def _clip(text: str) -> str:
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def read(user, query: str = "", limit: int = 20) -> str:
    """最近 limit 轮（旧在前），query 非空时只留问或答含该词的轮（不分大小写）。"""
    turns = list(_ARCHIVE.get(str(user), ()))
    needle = query.strip().lower()
    if needle:
        turns = [t for t in turns if needle in t[0].lower() or needle in t[1].lower()]
    turns = turns[-max(1, min(int(limit or 20), 50)):]
    if not turns:
        return EMPTY
    return "\n".join(f"[{i}] 用户: {_clip(a)}\n    助手: {_clip(r) or '（无回复）'}"
                     for i, (a, r) in enumerate(turns, 1))
