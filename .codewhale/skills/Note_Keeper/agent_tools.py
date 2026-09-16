"""Note_Keeper 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

备忘 / 工作表 / 图表，全部按成员私有：读写一律强制注入发送者 member。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import paths as _paths
import tool_runtime as rt
from tool_runtime import fn, s, int_, boolean

_log = logging.getLogger("familyassist.agent")

ORDER = 50

NOTE_COMMANDS = {"note-add", "note-list", "note-search", "note-delete", "note-pin"}
SHEET_COMMANDS = {"sheet-create", "sheet-list", "sheet-show", "sheet-set",
                  "sheet-unset", "sheet-row-add", "sheet-row-edit",
                  "sheet-row-delete", "sheet-rename", "sheet-pin", "sheet-delete"}
COMMANDS = NOTE_COMMANDS | SHEET_COMMANDS | {"chart-render"}

WORKSHEET_PIN_ROW_CAP = int((rt.CONFIG.get("notes") or {}).get("worksheet_pin_row_cap") or 80)


def relocate_note_image(src: str, member: str) -> str:
    """备忘图片搬到成员 notes/（relocate_image 的 notes 域包装）。"""
    return rt.relocate_image(src, member, "notes")


def tool_save_note(args):
    src = args.get("source-image", "")
    if src:
        member = args.get("member", "") or args.get("--member", "")
        args = {**args, "source-image": relocate_note_image(src, member)}
    return rt.run_cli("note-add", args)


def tool_add_worksheet_row(args):
    return rt.run_cli("sheet-row-add", rt.json_arg(args, "data"))


def tool_edit_worksheet_row(args):
    return rt.run_cli("sheet-row-edit", rt.json_arg(args, "data"))


def tool_visualize_data(args):
    return rt.run_cli("chart-render", rt.json_arg(args, "spec"))


TOOLS = {
    "save_note": tool_save_note,
    "list_notes": "note-list",
    "search_notes": "note-search",
    "delete_note": "note-delete",
    "pin_note": "note-pin",
    "create_worksheet": "sheet-create",
    "list_worksheets": "sheet-list",
    "show_worksheet": "sheet-show",
    "set_worksheet_field": "sheet-set",
    "unset_worksheet_field": "sheet-unset",
    "add_worksheet_row": tool_add_worksheet_row,
    "edit_worksheet_row": tool_edit_worksheet_row,
    "delete_worksheet_row": "sheet-row-delete",
    "rename_worksheet": "sheet-rename",
    "pin_worksheet": "sheet-pin",
    "delete_worksheet": "sheet-delete",
    "visualize_data": tool_visualize_data,
}

NOTE_TOOLS = {"save_note", "search_notes", "list_notes", "delete_note", "pin_note"}
SHEET_TOOLS = {"create_worksheet", "list_worksheets", "show_worksheet",
               "set_worksheet_field", "unset_worksheet_field", "add_worksheet_row",
               "edit_worksheet_row", "delete_worksheet_row", "rename_worksheet",
               "pin_worksheet", "delete_worksheet", "visualize_data"}
MEMBER_LOCKED = NOTE_TOOLS | SHEET_TOOLS
IMAGE_TOOLS = {"visualize_data"}

SCHEMAS = [
    fn("save_note", "保存一条个人备忘（杂项信息：车位号/wifi密码/课表/名片等）。"
       "仅本人可见", {
        "content": s("备忘内容（图片来源时传 OCR 出的关键信息）"),
        "source-image": s("来源图片路径（图片备忘时填已保存路径）"),
        "pinned": boolean("置顶：重要长期信息每次对话自动带上"),
    }, ["content"]),
    fn("list_notes", "列出本人最近的备忘", {
        "limit": int_("最多返回条数（默认 20）"),
    }),
    fn("search_notes", "按关键词搜索本人的备忘（用户问\"我记过什么\"\"XX是什么来着\"）", {
        "keyword": s("关键词，匹配备忘内容"),
    }, ["keyword"]),
    fn("delete_note", "删除本人的一条备忘", {
        "id": int_("备忘 id"),
    }, ["id"]),
    fn("pin_note", "置顶/取消置顶本人的一条备忘", {
        "id": int_("备忘 id"),
        "unpin": boolean("true=取消置顶"),
    }, ["id"]),
    fn("create_worksheet", "创建一张工作表，用于长期跟踪结构化信息。仅当用户明确要求"
       "\"建个表/做个 worksheet/长期记录这些\"时才用；普通杂事用 save_note。"
       "kind=kv 是事实清单（字段→值，如房贷利率/到期）；kind=table 是流水记录"
       "（多行，每行动态列，如血压/体重打卡）", {
        "title": s("工作表名（唯一，作为后续引用的句柄）"),
        "kind": s("kv=事实清单 / table=流水记录", enum=["kv", "table"]),
        "pinned": boolean("置顶：每次对话自动带上全表内容"),
    }, ["title", "kind"]),
    fn("list_worksheets", "列出本人的工作表（名称/类型/规模）", {}),
    fn("show_worksheet", "显示一张工作表的完整内容", {
        "title": s("工作表名"),
    }, ["title"]),
    fn("set_worksheet_field", "在 kv 工作表上设置/覆盖一个字段", {
        "title": s("工作表名"),
        "field": s("字段名"),
        "value": s("字段值"),
    }, ["title", "field", "value"]),
    fn("unset_worksheet_field", "从 kv 工作表删除一个字段", {
        "title": s("工作表名"),
        "field": s("字段名"),
    }, ["title", "field"]),
    fn("add_worksheet_row", "向 table 工作表追加一行（列名→值，列可动态新增）", {
        "title": s("工作表名"),
        "data": {"type": "object", "description": "一行数据，键=列名 值=单元格值"},
    }, ["title", "data"]),
    fn("edit_worksheet_row", "覆盖 table 工作表的某一行（按行 id）", {
        "title": s("工作表名"),
        "row-id": int_("行 id（见 show_worksheet 的 #号）"),
        "data": {"type": "object", "description": "整行新数据（覆盖式）"},
    }, ["title", "row-id", "data"]),
    fn("delete_worksheet_row", "删除 table 工作表的某一行（按行 id）", {
        "title": s("工作表名"),
        "row-id": int_("行 id"),
    }, ["title", "row-id"]),
    fn("rename_worksheet", "重命名一张工作表", {
        "title": s("当前名"),
        "new-title": s("新名"),
    }, ["title", "new-title"]),
    fn("pin_worksheet", "置顶/取消置顶工作表（置顶=每次对话自动带上全表）", {
        "title": s("工作表名"),
        "unpin": boolean("true=取消置顶"),
    }, ["title"]),
    fn("delete_worksheet", "删除整张工作表（含所有行）", {
        "title": s("工作表名"),
    }, ["title"]),
    fn("visualize_data", "把工作表里的数字画成图表（折线/柱状/饼图）并发给用户。"
       "你先从相关工作表取出对应数字（必要时先 show_worksheet），再调本工具。"
       "用户说\"画个图/可视化/看看趋势/show me the chart\"时用", {
        "spec": {
            "type": "object",
            "description": "图表规格：type(line/bar/pie), title, 可选 x_label/y_label, "
                           "x_labels(类别/X轴数组), series(数组，每项 {name, values})。"
                           "line/bar 可多 series；pie 只能一个 series，values 对应 x_labels",
        },
    }, ["spec"]),
]

PROMPT_RULES = [
    '用户说"记一下""帮我记住""备忘"（非记账类杂项信息）→ save_note；重要长期信息建议 pinned',
    '用户问"我记过什么""XX是什么来着""车位/wifi密码是多少"→ search_notes 或 list_notes；删某条→delete_note；置顶/取消置顶→pin_note',
    '工作表（长期结构化跟踪）：仅当用户明确说"建个表/做个 worksheet/长期记录这些字段/这些流水"时才用 create_worksheet；普通"记一下"仍用 save_note，不要升级成工作表。kv=事实清单（房贷利率/保单号），table=流水（血压/体重/读数打卡）。更新已存表用 set_worksheet_field（kv）或 add_worksheet_row/edit_worksheet_row（table）；查全表用 show_worksheet；列我所有表→list_worksheets；删字段→unset_worksheet_field、删行→delete_worksheet_row、改表名→rename_worksheet、置顶表→pin_worksheet、删整表→delete_worksheet',
    '用户要"图/可视化/趋势/图表/show me the chart"→ 先确认数据在哪张工作表（必要时 show_worksheet 取全），抽出对应数字，调 visualize_data 画图；图会自动发给用户，你只需简短说明',
    "备忘按成员私有：只能看到当前用户自己的备忘，这是系统强制的，无需向用户解释",
]


def notes_context(member: str, recent_limit: int = 5, clip: int = 100,
                  db_path: str | None = None) -> str:
    """取该成员置顶 + 最近备忘，拼成 system prompt 附加块。

    进程内直调 note_db（每条消息都要取，subprocess 太重）。
    任何失败返回空串 —— 备忘注入绝不能拖垮 handle()。
    """
    try:
        import note_db
        notes = note_db.pinned_and_recent(
            member, recent_limit=recent_limit,
            db_path=db_path or str(_paths.member_store(member, "notes")))
        if not notes:
            return ""
        lines = []
        for n in notes:
            content = n["content"][:clip] + ("…" if len(n["content"]) > clip else "")
            mark = "📌" if n.get("pinned") else "·"
            lines.append(f"{mark} #{n['id']} {content}")
        return (f"\n\n## 已存备忘（仅 {member} 可见；内容超长已截断，"
                f"完整内容用 search_notes 查）\n" + "\n".join(lines))
    except Exception:
        _log.exception("备忘上下文注入失败（已跳过）")
        return ""


def worksheets_context(member: str, db_path: str | None = None) -> str:
    """取该成员置顶工作表，整表渲染进 system prompt（全量注入）。

    table 超 WORKSHEET_PIN_ROW_CAP 行截断并提示。任何失败返回空串。
    """
    try:
        import sheet_db
        sheets = sheet_db.pinned_sheets(
            member,
            db_path=db_path or str(_paths.member_store(member, "notes")))
        if not sheets:
            return ""
        blocks = []
        for sh in sheets:
            if sh is None:
                continue
            lines = [f"### {sh['title']}（{sh['kind']}）"]
            if sh["kind"] == "kv":
                for k, v in sh["kv_data"].items():
                    lines.append(f"- {k}: {v}")
            else:
                rows = sh["rows"]
                shown = rows[:WORKSHEET_PIN_ROW_CAP]
                for row in shown:
                    cells = "  ".join(f"{k}={v}" for k, v in row["row_data"].items())
                    lines.append(f"- #{row['id']} {cells}")
                if len(rows) > WORKSHEET_PIN_ROW_CAP:
                    lines.append(f"- …还有 {len(rows) - WORKSHEET_PIN_ROW_CAP} 行，"
                                 f"用 show_worksheet 看全部")
            blocks.append("\n".join(lines))
        if not blocks:
            return ""
        return (f"\n\n## 已存工作表（仅 {member} 可见，置顶项全量带上）\n"
                + "\n\n".join(blocks))
    except Exception:
        _log.exception("工作表上下文注入失败（已跳过）")
        return ""


CONTEXT_FNS = [notes_context, worksheets_context]
