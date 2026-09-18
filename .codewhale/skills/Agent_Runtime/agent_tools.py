"""Agent_Runtime 自带工具的 manifest（契约见 skill_registry.py）。

- knowking：懂王舆情桥，后台跑；代码注入 __channel/__user（LLM 拿不到也不该拿）
- send_file：把 data 内文件发给用户（闸门 tool_runtime.resolve_sendable）
- chat_history：回读已移出上下文的本用户对话（chat_archive）；代码注入 __user，读不到别人的
"""

from __future__ import annotations


import chat_archive
import knowking_jobs as _knowking
import tool_runtime as rt
from tool_runtime import fn, s

ORDER = 90


def tool_knowking(args):
    """懂王：跨社交平台舆情搜集。后台跑（数分钟），出报告后由传输层推给发起人。
    仅在正式频道（注入了 __channel/__user 上下文）可用。"""
    topic = (args.get("topic") or "").strip()
    if not topic:
        return "[错误] 缺少查询主题"
    channel = args.get("__channel", "")
    user = args.get("__user", "")
    if not (channel and user):
        return "[错误] KnowKing 仅在微信/Telegram 频道可用，当前无频道上下文（如本地测试）。"
    _job_id, ack = _knowking.submit(topic, channel, user, args.get("member", ""))
    return ack


def tool_send_file(args):
    rel = rt.resolve_sendable(args.get("path", ""), args.get("member", ""))
    return rel if rel else "[错误] 路径不允许或文件不存在"


def tool_chat_history(args):
    user = args.get("__user", "")
    if not user:
        return "[错误] 当前无用户上下文"
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    return chat_archive.read(user, str(args.get("query") or ""), limit)


TOOLS = {"knowking": tool_knowking, "send_file": tool_send_file,
         "chat_history": tool_chat_history}
FAST_TICKS = [_knowking.poll_and_deliver]

MEMBER_LOCKED = {"send_file"}
CONTEXT_TOOLS = {"knowking", "chat_history"}
UNTRUSTED_TOOLS = {"chat_history"}   # 旧回复可能转述过网页/OCR 原文
DOC_TOOLS = {"send_file"}

SCHEMAS = [
    fn("send_file", "把 data 目录内的一个文件发给用户（path 为 data 相对路径）。"
       "只能发家庭共享文件或你自己的文件", {
        "path": s("文件的 data 相对路径"),
    }, ["path"]),
    fn("knowking", "懂王（KnowKing / kk）：跨社交平台（YouTube/X/Reddit/TikTok/Instagram/"
       "Bilibili/Zhihu）搜集\"大家在怎么说某话题\"，出中立第三方舆情报告。"
       "**仅当用户明确说出触发词 knowking / kk / 懂王 时才用**（如\"用 knowking 查 X\""
       "\"kk 一下大家怎么看 X\"）；普通查事实/新闻/股价仍用 anysearch_search/web_search，不要用它。"
       "耗时数分钟，后台运行，出报告会自动推送给用户——你只需把本工具返回的\"已开始\"提示原样转达，"
       "不要等待、不要编造报告内容。", {
        "topic": s("要查的主题：去掉 knowking/kk/懂王 触发词，保留真正要查的内容 + 用户给的额外背景/角度/时间范围"),
    }, ["topic"]),
    fn("chat_history", "回读与本用户更早的对话（因闲置/清除/超长已移出你的上下文的部分；"
       "当前上下文里已有的不在其中）。每轮只有用户原话和你的最终回复。", {
        "query": s("可选关键词，只返回问或答含该词的轮；留空 = 最近几轮"),
        "limit": {"type": "integer", "description": "最多返回几轮，默认 20，上限 50"},
    }, []),
]

PROMPT_RULES = [
    '用户提到上下文里找不到的更早内容（"上次/之前/刚才说的""那个 X 怎么样了"）→ 先调 chat_history（可带关键词）回读再答；上下文够用就别调。回读不到就如实说',
    '用户**明确说出 knowking / kk / 懂王**（如"用 knowking 查大家怎么看 X""kk 一下 Y"）→ 调 knowking 做跨社交平台舆情搜集（topic 去掉触发词只留要查的内容）。这是**唯一**触发条件：没说这几个词就别用它，普通查事实/新闻用 anysearch_search。它耗时数分钟、后台跑、出报告自动推送——把返回的"已开始"提示原样转达即可，别等待、别自己编报告',
]
