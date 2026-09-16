"""Document_Keeper 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

文档归档 / 到期 / 成员资料（profiles，家庭共享）/ 发送文档原件。
doc-remove 仅限本机，不进 AGENT_COMMANDS。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import tool_runtime as rt
from tool_runtime import fn, s, int_, boolean

_log = logging.getLogger("familyassist.agent")

ORDER = 30

COMMANDS = {"doc-add", "doc-list", "doc-show", "doc-due", "doc-update", "doc-ack",
            "doc-remove", "doc-file", "profile-set", "profile-unset", "profile-list"}
AGENT_COMMANDS = COMMANDS - {"doc-remove"}

DOC_TYPES = list(rt.CONFIG.get("doc_types") or ["other"])
DOC_STATUSES = ["active", "expired", "archived", "superseded"]


def tool_send_document(args):
    return rt.run_cli("doc-file", {"id": args.get("id")})


TOOLS = {
    "add_document": "doc-add",
    "list_documents": "doc-list",
    "show_document": "doc-show",
    "due_documents": "doc-due",
    "update_document": "doc-update",
    "ack_document": "doc-ack",
    "set_profile_field": "profile-set",
    "remove_profile_field": "profile-unset",
    "send_document": tool_send_document,
}

MEMBER_LOCKED = {"add_document", "send_document"}
DOC_TOOLS = {"send_document"}

SCHEMAS = [
    fn("add_document", "归档一份家庭重要文档（合同/保单/证件等），登记到期日以便提醒", {
        "type": s("文档类型", enum=DOC_TYPES),
        "title": s("文档名称，如 2026公寓租约"),
        "issuer": s("签发方：房东/保险公司/政府机构"),
        "number": s("编号：保单号/证件号"),
        "issue-date": s("签发日期 YYYY-MM-DD"),
        "expiry": s("到期日期 YYYY-MM-DD；长期有效不填"),
        "action-note": s("到期要做什么，如 提前60天通知房东"),
        "remind-days": int_("提前几天提醒（不填用默认值）"),
        "file": s("原始文件路径（图片已保存的路径）"),
        "ocr-text": s("OCR 识别全文，用于日后关键词检索"),
        "notes": s("备注"),
        "force": boolean("跳过重复检查强制写入（仅在用户确认非重复后用）"),
    }, ["type", "title"]),
    fn("list_documents", "查询已归档的家庭文档", {
        "type": s("文档类型", enum=DOC_TYPES),
        "member": s("按成员过滤"),
        "keyword": s("关键词，匹配标题/OCR全文/备注"),
        "status": s("状态（默认隐藏 archived/superseded）", enum=DOC_STATUSES),
        "limit": int_("最多返回条数"),
    }),
    fn("show_document", "查看某文档完整信息（含文件路径）", {
        "id": int_("文档 id"),
    }, ["id"]),
    fn("due_documents", "查询即将到期/已过期的文档", {
        "days": int_("查看几天内到期（不填按各文档默认提前量）"),
    }),
    fn("update_document", "更新文档信息（续约改到期日、改状态归档等）", {
        "id": int_("文档 id"),
        "type": s("文档类型", enum=DOC_TYPES),
        "title": s("文档名称"),
        "issuer": s("签发方"),
        "number": s("编号"),
        "issue-date": s("签发日期 YYYY-MM-DD"),
        "expiry": s("新到期日 YYYY-MM-DD（改后重新进入提醒）"),
        "action-note": s("到期要做什么"),
        "remind-days": int_("提前几天提醒"),
        "status": s("状态", enum=DOC_STATUSES),
        "notes": s("备注"),
    }, ["id"]),
    fn("ack_document", "确认某文档的到期提醒（之后不再每日重复提醒）", {
        "id": int_("文档 id"),
    }, ["id"]),
    fn("send_document", "把已归档的文档原件（租约/保单/证件等）发给用户。"
       "先用 list_documents/show_document 找到对应文档的 id", {
        "id": int_("文档 id"),
    }, ["id"]),
    fn("set_profile_field", "写/改一条家庭成员资料（法定名/生日/电话/邮箱/住址/证件卡号等"
       "长期个人事实，全家共享，全家可见可改）。用户提供这类信息时随手存这里，别存备忘。", {
        "member-name": s("这条事实属于谁：登记成员显示名；家庭层面（住址等）用 Family"),
        "field": s("字段名，如 生日 / 电话 / LAP卡号"),
        "value": s("值"),
    }, ["member-name", "field", "value"]),
    fn("remove_profile_field", "删一条家庭成员资料。", {
        "member-name": s("成员显示名或 Family"),
        "field": s("字段名"),
    }, ["member-name", "field"]),
]

PROMPT_SECTIONS = [
    f"""## 文档管理（家庭重要文档归档与到期提醒）
- 文档类型: {"/".join(DOC_TYPES)}
- 用户发来 合同/保单/证件 等重要文档，或说"存一下这个文件"→ add_document（尽量带 expiry 到期日和 action-note 到期动作）
- 用户问"租约什么时候到期""我们有哪些保险""找一下XX保单"→ list_documents / show_document
- 用户问"有什么要到期的""最近有什么要办的"→ due_documents
- 用户说"续约了""换新证了"→ update_document 改到期日；旧文档另存时把旧的 status 改 superseded
- 用户说"知道了""别再提醒"→ ack_document""",
]

PROMPT_RULES = [
    "用户提供长期个人事实（法定名/生日/电话/邮箱/住址/证件卡号等）→ set_profile_field 存到家庭成员资料（member-name 填事实属于谁，家庭层面如住址用 Family），不要存成备忘；这类信息全家共享",
    '填 PDF 表格时建议值优先取自"家庭成员资料"块（仍然逐字段问用户确认）',
    '用户说"把我的租约/保单发给我""发我那个文件/那张图"→ send_document（先 list/show 拿 id）或 send_file（data 内相对路径）；文件会自动发给用户',
]


def profiles_context(member: str | None = None, db_path: str | None = None) -> str:
    """家庭成员资料块（家庭共享，注入所有成员对话）。失败/为空返回空串。"""
    try:
        import doc_db as _doc_db
        rows = _doc_db.list_profiles(db_path=db_path)
        if not rows:
            return ""
        lines, cur = [], None
        for r in rows:
            if r["member"] != cur:
                cur = r["member"]
                lines.append(f"【{cur}】")
            lines.append(f"  {r['field']}: {r['value']}")
        return ("\n\n## 家庭成员资料（全家共享，可用 set_profile_field 更新）\n"
                + "\n".join(lines))
    except Exception:
        _log.exception("成员资料上下文注入失败（已跳过）")
        return ""


CONTEXT_FNS = [profiles_context]

from reminder import check_and_push as doc_reminder_check  # noqa: E402

SLOW_TICKS = [doc_reminder_check]
