"""
Mail Keeper — 回信待确认草稿（纯逻辑，可注入测试）。

发邮件是对外不可撤回动作，且邮件正文是外部内容（提示注入面）：**代码强制两轮**。
第 1 轮 draft_reply 只落盘草稿并把原文预览还给用户；第 2 轮用户自己说
"确认/发送" 才允许 send。三道确定性闸门（不依赖 LLM 自觉）：

    1. 草稿 created_at 必须 < 本轮开始时间 turn_at —— 同一轮里 draft+send 被拒，
       所以被注入的邮件无法自问自答把信发出去
    2. 本轮用户原话须命中 CONFIRM_RE —— LLM 编不出用户的确认
    3. 草稿超过 TTL_S（30 分钟）过期作废

收件人不在草稿里由 LLM 指定：draft 时由 gmail_provider.reply_recipient 从原信
Reply-To/From 算出（改不了收件人 = 泄密面只能回到原发件人）。

草稿存 data/.state/.mail_drafts.json（点前缀 = 运行时瞬态，不进备份），每成员一条。
"""

from __future__ import annotations

import re
import time

import jsonfile
import paths as _paths

TTL_S = 1800
CONFIRM_RE = re.compile(
    r"(确认|确定|发送|发出去|发出|发吧|就发|可以发|同意|没问题|ok|okay|yes|send|好的|行)",
    re.I)


def store_path():
    return _paths.state_file(".mail_drafts.json")


def _load() -> dict:
    return jsonfile.load_dict(store_path())


def _save(d: dict) -> None:
    jsonfile.save(store_path(), d)


def put(member: str, draft: dict, *, now: float | None = None) -> dict:
    """存/覆盖该成员的待确认草稿（一人一条，新的顶掉旧的）。"""
    d = _load()
    draft = {**draft, "created_at": time.time() if now is None else now}
    d[member] = draft
    _save(d)
    return draft


def get(member: str) -> dict | None:
    v = _load().get(member)
    return v if isinstance(v, dict) else None


def drop(member: str) -> None:
    d = _load()
    if d.pop(member, None) is not None:
        _save(d)


def check(member: str, *, turn_at: float, text: str,
          now: float | None = None) -> tuple[dict | None, str]:
    """三道闸门。通过返回 (草稿, "")；否则 (None, 给 LLM 的错误原因)。"""
    draft = get(member)
    if not draft:
        return None, "[错误] 没有待发送的回信草稿，先用 draft_reply 起草。"
    created = float(draft.get("created_at") or 0)
    if (time.time() if now is None else now) - created > TTL_S:
        drop(member)
        return None, "[错误] 草稿已过期（超过 30 分钟），请重新起草。"
    if created >= turn_at:
        return None, ("[错误] 草稿是这一轮刚起草的，不能同一轮发送。"
                      "把草稿原样给用户看，等用户下一条消息说\"确认/发送\"再调本工具。")
    if not CONFIRM_RE.search(text or ""):
        return None, ("[错误] 用户这条消息没有明确确认发送。先把草稿给用户确认，"
                      "得到\"确认/发送\"这类回复后再调本工具。")
    return draft, ""


def preview(draft: dict) -> str:
    """给用户看的草稿全文（LLM 应原样转述，不改内容）。"""
    return (f"待确认回信：\n收件人：{draft.get('to', '')}\n"
            f"主题：{draft.get('subject', '')}\n---\n{draft.get('body', '')}\n---\n"
            f"回复\"确认发送\"我就发出；改内容就直接说怎么改（30 分钟内有效）。")
