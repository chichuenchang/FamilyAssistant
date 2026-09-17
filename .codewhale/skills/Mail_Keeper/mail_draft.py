"""
Mail Keeper — 待确认邮件草稿（回信 kind="reply" / 新信 kind="new"，纯逻辑，可注入测试）。

发邮件是对外不可撤回动作，且邮件正文是外部内容（提示注入面）：**代码强制两轮**。
第 1 轮 draft_reply / compose_mail 只落盘草稿并把原文预览还给用户；第 2 轮用户自己说
"确认/发送" 才允许 send。三道确定性闸门（不依赖 LLM 自觉）：

    1. 确认必须落在**紧接着预览的那一轮**：草稿记下起草时的 turn_id（每用户单调递增，
       agent_core._apply_context 注入），send 时要求 turn_id == 草稿 turn_id + 1 且同一
       用户。同一轮 draft+send 不成立（差 0），所以被注入的邮件无法自问自答把信发出去；
       隔了别的对话也不成立——用户那句"确认"必须是对着刚看到的预览说的
    2. 本轮用户原话（去标点后）须**整句**是一句发送确认 —— LLM 编不出用户的确认；
       整句匹配而非子串：「不行，先别发送」「看下银行那封」「ok」都不算
    3. 草稿超过 TTL_S（30 分钟）过期作废（进程重启后 turn_id 归零，旧草稿一并失效）

回信（kind="reply"）的收件人不由 LLM 指定：draft 时由 gmail_provider.reply_recipient
从原信 Reply-To/From 算出（改不了收件人 = 泄密面只能回到原发件人）。新信（kind="new"）
的收件人与附件由 LLM 填，全靠预览 + 用户确认把关——收件人和附件路径都在 preview 里，
SHOW_TOOLS 保证它原文到用户眼前。

附件在草稿里存 data 相对路径清单；发送前由 agent_tools 用
tool_runtime.resolve_sendable 重新过闸（存在 + 属家庭或本成员）。

草稿存 data/.state/.mail_drafts.json（点前缀 = 运行时瞬态，不进备份），每成员一条。
"""

from __future__ import annotations

import re
import time

import jsonfile
import paths as _paths

TTL_S = 1800
# 整句匹配（confirmed() 先去掉空白/标点）：必须带明确的"发/确认"，单独的 好的/ok/行 不算
CONFIRM_RE = re.compile(
    r"(好的?|行|可以|嗯+|ok|okay|yes)?"
    r"(确认发送|确认发出|确认|发送|发出去|发出|发吧|就发|可以发|confirm|sendit|send)"
    r"[吧了啊呀]?", re.I)


def store_path():
    return _paths.state_file(".mail_drafts.json")


def _load() -> dict:
    return jsonfile.load_dict(store_path())


def _save(d: dict) -> None:
    jsonfile.save(store_path(), d)


def put(member: str, draft: dict, *, turn_id: int = 0, user: str = "",
        now: float | None = None) -> dict:
    """存/覆盖该成员的待确认草稿（一人一条，新的顶掉旧的）。

    turn_id / user = 起草这一轮的身份，check 据此要求确认落在紧接着的下一轮。
    """
    d = _load()
    draft = {**draft, "created_at": time.time() if now is None else now,
             "turn_id": int(turn_id), "turn_user": str(user or "")}
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


def confirmed(text: str) -> bool:
    """用户这句话整句就是发送确认（不是含确认词的别的话）。"""
    return bool(CONFIRM_RE.fullmatch(re.sub(r"[\W_]+", "", text or "")))


def check(member: str, *, turn_id: int, user: str, text: str,
          now: float | None = None) -> tuple[dict | None, str]:
    """三道闸门。通过返回 (草稿, "")；否则 (None, 给 LLM 的错误原因)。"""
    draft = get(member)
    if not draft:
        return None, "[错误] 没有待发送的邮件草稿，先用 draft_reply / compose_mail 起草。"
    created = float(draft.get("created_at") or 0)
    if (time.time() if now is None else now) - created > TTL_S:
        drop(member)
        return None, "[错误] 草稿已过期（超过 30 分钟），请重新起草。"
    expected_turn = int(draft.get("turn_id") or 0) + 1      # 预览的下一轮，就这一轮
    if int(turn_id) != expected_turn or str(user or "") != str(draft.get("turn_user") or ""):
        return None, ("[错误] 这份草稿不是上一轮刚给用户看过的那份"
                      "（同一轮起草即发、或中间隔了别的对话）。"
                      "重新起草一份给用户过目，等他下一条消息说\"确认/发送\"再调本工具。")
    if not confirmed(text):
        return None, ("[错误] 用户这条消息没有明确确认发送。先把草稿给用户确认，"
                      "得到\"确认/发送\"这类回复后再调本工具。")
    return draft, ""


def preview(draft: dict) -> str:
    """给用户看的草稿全文（起草工具在 SHOW_TOOLS：代码直接附给用户，不经 LLM 转述）。"""
    att = draft.get("attachments") or []
    head = "待确认回信" if draft.get("kind", "reply") == "reply" else "待确认新邮件"
    rows = "".join(f"  {a}\n" for a in att)
    att_block = f"附件（{len(att)} 个）：\n{rows}" if att else ""
    return (f"{head}：\n收件人：{draft.get('to', '')}\n"
            f"主题：{draft.get('subject', '')}\n{att_block}---\n{draft.get('body', '')}\n---\n"
            f"回复\"确认发送\"我就发出；改内容就直接说怎么改（30 分钟内有效）。")
