"""Note_Keeper 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

备忘 / 工作表 / 图表，全部按成员私有：读写一律强制注入发送者 member。
"""

from __future__ import annotations

import logging


import paths as _paths
import tool_runtime as rt
from tool_runtime import fn, s, int_, boolean

_log = logging.getLogger("familyassist.agent")

ORDER = 50

NOTE_COMMANDS = {"note-add", "note-list", "note-search", "note-edit", "note-delete", "note-pin"}
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


_SHEET_OPS = {   # op → (CLI 子命令, 透传的参数键)
    "set": ("sheet-set", ("field", "value")),
    "unset": ("sheet-unset", ("field",)),
    "add_row": ("sheet-row-add", ("data",)),
    "edit_row": ("sheet-row-edit", ("row_id", "data")),
    "delete_row": ("sheet-row-delete", ("row_id",)),
    "rename": ("sheet-rename", ("new_title",)),
    "pin": ("sheet-pin", ()),
    "unpin": ("sheet-pin", ()),
}


def tool_edit_worksheet(args):
    """一次调用按序执行多个编辑 op，每个 op 独立出结果（一条失败不挡后面的）。"""
    title = args.get("title") or ""
    ops = args.get("ops") or []
    if not isinstance(ops, list) or not ops:
        return "[错误] ops 至少一项"
    lines = []
    for i, op in enumerate(ops, 1):
        kind = op.get("op") if isinstance(op, dict) else None
        if kind not in _SHEET_OPS:
            lines.append(f"{i}) [错误] 未知 op: {kind}（可选 {'/'.join(_SHEET_OPS)}）")
            continue
        cmd, keys = _SHEET_OPS[kind]
        cli = {"member": args.get("member", ""), "title": title,
               **{k.replace("_", "-"): op.get(k) for k in keys}}
        if kind == "unpin":
            cli["unpin"] = True
        if "data" in keys:
            cli = rt.json_arg(cli, "data")
        out = rt.run_cli(cmd, cli)
        if kind == "rename" and out.startswith("已重命名"):
            title = op.get("new_title") or title   # 后续 op 用新名
        lines.append(f"{i}) {out}")
    return "\n".join(lines)


def tool_visualize_data(args):
    return rt.run_cli("chart-render", rt.json_arg(args, "spec"))


TOOLS = {
    "save_note": tool_save_note,
    "list_notes": "note-list",
    "search_notes": "note-search",
    "update_note": "note-edit",
    "delete_note": "note-delete",
    "pin_note": "note-pin",
    "create_worksheet": "sheet-create",
    "list_worksheets": "sheet-list",
    "show_worksheet": "sheet-show",
    "edit_worksheet": tool_edit_worksheet,
    "delete_worksheet": "sheet-delete",
    "visualize_data": tool_visualize_data,
}

NOTE_TOOLS = {"save_note", "search_notes", "list_notes", "update_note", "delete_note", "pin_note"}
SHEET_TOOLS = {"create_worksheet", "list_worksheets", "show_worksheet",
               "edit_worksheet", "delete_worksheet", "visualize_data"}
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
    fn("update_note", "改写本人的一条备忘（信息变了/补充时用，别删了重记）", {
        "id": int_("备忘 id"),
        "content": s("新的完整内容（整条覆盖，要保留的旧内容一并写上）"),
    }, ["id", "content"]),
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
    fn("edit_worksheet", "编辑一张工作表：ops 按序执行，可一次做多件事（如记三行、改两个字段再置顶），"
       "每个 op 返回各自结果", {
        "title": s("工作表名"),
        "ops": {"type": "array", "description": "编辑操作列表", "items": {
            "type": "object", "properties": {
                "op": s("set/unset=kv 字段设值/删字段；add_row/edit_row/delete_row=table 行"
                        "（edit_row 整行覆盖）；rename=改表名；pin/unpin=置顶（每次对话自动带全表）",
                        enum=list(_SHEET_OPS)),
                "field": s("set/unset：字段名"),
                "value": s("set：字段值"),
                "data": {"type": "object", "description": "add_row/edit_row：一行数据，键=列名（列可动态新增）"},
                "row_id": int_("edit_row/delete_row：行 id（show_worksheet 的 #号）"),
                "new_title": s("rename：新表名"),
            }, "required": ["op"]}},
    }, ["title", "ops"]),
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
    "备忘 = 非记账类杂项信息（按成员私有）；工作表 = 用户要长期结构化跟踪的字段或流水；要画图就从工作表取数调 visualize_data（图自动发出）",
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

IMAGE_ROUTES = [
    "其他有信息价值的杂项（路由器标签/课表/名片/告示等）→ 备忘：整理成一两句关键信息，带 source_image",
]
