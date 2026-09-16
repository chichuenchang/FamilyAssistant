"""对话历史预算：token 粗估 + 整轮裁剪。纯函数，无 IO。"""

from __future__ import annotations

import json

# 历史存档里单条工具结果的字符上限（本轮内不截断，只影响跨轮存档）：
# 工具结果必须跨轮保留（填表会话 id 只出现在工具结果里，丢了模型下轮就瞎编），
# 但 OCR/网页全文动辄上万字，原样存会挤爆 token 预算 → 截断留头部（id 都在首行）。
HIST_TOOL_CAP = 1500


def estimate_tokens(text: str) -> int:
    """粗估文本 token 数：CJK ≈ 1 token/字，ASCII ≈ 4 字符/token。

    宁可略高估（裁剪触发更早），不追求精确——只用于历史预算，不用于计费。"""
    if not text:
        return 0
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return (len(text) - ascii_n) + (ascii_n + 3) // 4


def msg_tokens(m: dict) -> int:
    """粗估一条历史消息的 token（content + tool_calls 参数）。"""
    n = estimate_tokens(m.get("content") or "")
    if m.get("tool_calls"):
        n += estimate_tokens(json.dumps(m["tool_calls"], ensure_ascii=False))
    return n


def clip_tool_result(result: str) -> str:
    """跨轮存档用：超 HIST_TOOL_CAP 截断留头部。"""
    if len(result) <= HIST_TOOL_CAP:
        return result
    return result[:HIST_TOOL_CAP] + "\n…（历史存档截断）"


def trim_history(h: list[dict], history_size: int, max_tokens: int) -> int:
    """就地裁剪：先按轮数上限，再按 token 预算整轮丢最旧（至少留最近一轮）。
    轮 = 一条 user 到下一条 user 之前；绝不留下没有配对 tool_calls 的孤儿 tool 消息。
    返回按预算丢弃的轮数。"""
    def _turn_starts() -> list[int]:
        return [i for i, m in enumerate(h) if m.get("role") == "user"]

    starts = _turn_starts()
    if len(starts) > history_size:
        del h[:starts[len(starts) - history_size]]
    trimmed = 0
    if max_tokens > 0:
        while True:
            starts = _turn_starts()
            if len(starts) <= 1 or sum(msg_tokens(m) for m in h) <= max_tokens:
                break
            del h[:starts[1]]
            trimmed += 1
    return trimmed
