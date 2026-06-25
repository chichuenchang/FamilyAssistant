"""
Family Assistant — Note Keeper CLI

个人备忘记录/检索/置顶。Agent 经白名单子命令调用，输出纯文本。
用法: python .codewhale/skills/Note_Keeper/cli.py <command> [args]

测试钩子：环境变量 NOTE_DB_PATH 可覆盖数据库路径（仅测试用）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 把本 skill 目录加入 sys.path（同目录 note_db）+ Agent_Runtime（paths）
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))

import note_db
import sheet_db
import chart
import paths as _paths

# 测试钩子：覆盖数据库路径，避免测试碰真实账本
_DB_OVERRIDE = os.environ.get("NOTE_DB_PATH") or None


def _db_for(member: str) -> str:
    """备忘库按成员私有：data/<成员目录>/notes/notes.db。NOTE_DB_PATH 测试时覆盖。"""
    if _DB_OVERRIDE:
        return _DB_OVERRIDE
    return str(_paths.member_store(member, "notes"))


def _mark_backup_dirty() -> None:
    """写入后通知备份引擎（失败静默，绝不影响写入本身）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass


def _fmt_note(note: dict) -> str:
    """单条备忘的显示格式。"""
    pinned_mark = " [置顶]" if note["pinned"] else ""
    lines = [f"#{note['id']}{pinned_mark} {note['created_at']} {note['content']}"]
    if note.get("source_image"):
        lines.append(f"  图片: {note['source_image']}")
    return "\n".join(lines)


def cmd_note_add(args):
    note_id = note_db.add_note(
        member=args.member,
        content=args.content,
        source_image=args.source_image or "",
        pinned=args.pinned,
        db_path=_db_for(args.member),
    )
    _mark_backup_dirty()
    print(f"已记录备忘 #{note_id}")


def cmd_note_list(args):
    rows = note_db.list_notes(
        member=args.member,
        limit=args.limit,
        db_path=_db_for(args.member),
    )
    if not rows:
        print("（无备忘）")
        return
    for r in rows:
        print(_fmt_note(r))


def cmd_note_search(args):
    rows = note_db.search_notes(
        member=args.member,
        keyword=args.keyword,
        db_path=_db_for(args.member),
    )
    if not rows:
        print("（无匹配）")
        return
    for r in rows:
        print(_fmt_note(r))


def cmd_note_delete(args):
    ok = note_db.delete_note(
        member=args.member,
        note_id=args.id,
        db_path=_db_for(args.member),
    )
    if ok:
        _mark_backup_dirty()
        print(f"已删除备忘 #{args.id}")
    else:
        print(f"[错误] 无此备忘", file=sys.stderr)
        sys.exit(1)


def cmd_note_pin(args):
    pinned = not args.unpin
    ok = note_db.set_pinned(
        member=args.member,
        note_id=args.id,
        pinned=pinned,
        db_path=_db_for(args.member),
    )
    if ok:
        _mark_backup_dirty()
        if pinned:
            print(f"已置顶备忘 #{args.id}")
        else:
            print(f"已取消置顶 #{args.id}")
    else:
        print(f"[错误] 无此备忘", file=sys.stderr)
        sys.exit(1)


def _fmt_sheet(s: dict) -> str:
    head = f"📊 {s['title']}（{s['kind']}）" + ("  📌" if s["pinned"] else "")
    lines = [head]
    if s["kind"] == "kv":
        if not s["kv_data"]:
            lines.append("  （空）")
        for k, v in s["kv_data"].items():
            lines.append(f"  {k}: {v}")
    else:
        if not s["rows"]:
            lines.append("  （无行）")
        for row in s["rows"]:
            cells = "  ".join(f"{k}={v}" for k, v in row["row_data"].items())
            lines.append(f"  #{row['id']}  {cells}")
    return "\n".join(lines)


def cmd_sheet_create(args):
    sid = sheet_db.create_sheet(args.member, args.title, args.kind,
                                pinned=args.pinned, db_path=_db_for(args.member))
    _mark_backup_dirty()
    print(f"已创建工作表 #{sid}：{args.title}（{args.kind}）")


def cmd_sheet_list(args):
    rows = sheet_db.list_sheets(args.member, db_path=_db_for(args.member))
    if not rows:
        print("（无工作表）")
        return
    for r in rows:
        mark = "  📌" if r["pinned"] else ""
        print(f"📊 {r['title']}（{r['kind']}, {r['size']}）{mark}")


def cmd_sheet_show(args):
    s = sheet_db.get_sheet(args.member, args.title, db_path=_db_for(args.member))
    if s is None:
        print("[错误] 无此工作表", file=sys.stderr)
        sys.exit(1)
    print(_fmt_sheet(s))


def cmd_sheet_set(args):
    ok = sheet_db.set_field(args.member, args.title, args.field, args.value,
                            db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已更新 {args.title}.{args.field} = {args.value}")
    else:
        print("[错误] 无此 kv 工作表", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_unset(args):
    ok = sheet_db.unset_field(args.member, args.title, args.field,
                              db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已删除字段 {args.title}.{args.field}")
    else:
        print("[错误] 无此字段", file=sys.stderr)
        sys.exit(1)


def _parse_data(raw: str) -> dict:
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("--data 必须是 JSON 对象")
    return obj


def cmd_sheet_row_add(args):
    rid = sheet_db.add_row(args.member, args.title, _parse_data(args.data),
                           db_path=_db_for(args.member))
    if rid is None:
        print("[错误] 无此 table 工作表", file=sys.stderr)
        sys.exit(1)
    _mark_backup_dirty()
    print(f"已添加行 #{rid}")


def cmd_sheet_row_edit(args):
    ok = sheet_db.edit_row(args.member, args.title, args.row_id,
                           _parse_data(args.data), db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已更新行 #{args.row_id}")
    else:
        print("[错误] 无此行", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_row_delete(args):
    ok = sheet_db.delete_row(args.member, args.title, args.row_id,
                             db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已删除行 #{args.row_id}")
    else:
        print("[错误] 无此行", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_rename(args):
    ok = sheet_db.rename_sheet(args.member, args.title, args.new_title,
                               db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已重命名为 {args.new_title}")
    else:
        print("[错误] 重命名失败（无此表或新名已存在）", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_pin(args):
    pinned = not args.unpin
    ok = sheet_db.set_pinned(args.member, args.title, pinned,
                             db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(("已置顶 " if pinned else "已取消置顶 ") + args.title)
    else:
        print("[错误] 无此工作表", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_delete(args):
    ok = sheet_db.delete_sheet(args.member, args.title, db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已删除工作表 {args.title}")
    else:
        print("[错误] 无此工作表", file=sys.stderr)
        sys.exit(1)


def _chart_retention_days() -> int:
    try:
        cfg = json.loads((Path(__file__).resolve().parents[3] / "config.json")
                         .read_text(encoding="utf-8"))
        return int((cfg.get("notes") or {}).get("chart_retention_days") or 7)
    except Exception:
        return 7


def cmd_chart_render(args):
    spec = json.loads(args.spec)   # JSONDecodeError -> ValueError -> main() returns 1
    try:
        rel = chart.render_chart(spec, member=args.member,
                                 retention_days=_chart_retention_days())
    except RuntimeError as e:      # matplotlib 未安装
        print(f"[错误] {e}（pip install matplotlib）", file=sys.stderr)
        sys.exit(1)
    _mark_backup_dirty()           # harmless; charts excluded from backup anyway
    print(rel)


def main() -> int:
    parser = argparse.ArgumentParser(description="Family Assistant — Note Keeper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("note-add", help="添加一条备忘")
    p.add_argument("--member", required=True, help="归属成员")
    p.add_argument("--content", required=True, help="备忘内容")
    p.add_argument("--source-image", help="来源图片路径")
    p.add_argument("--pinned", action="store_true", help="同时置顶")

    p = sub.add_parser("note-list", help="列出备忘")
    p.add_argument("--member", required=True, help="归属成员")
    p.add_argument("--limit", type=int, default=20, help="最多条数（默认 20）")

    p = sub.add_parser("note-search", help="搜索备忘")
    p.add_argument("--member", required=True, help="归属成员")
    p.add_argument("--keyword", required=True, help="搜索关键词")

    p = sub.add_parser("note-delete", help="删除备忘")
    p.add_argument("--member", required=True, help="归属成员")
    p.add_argument("--id", type=int, required=True, help="备忘 ID")

    p = sub.add_parser("note-pin", help="置顶/取消置顶备忘")
    p.add_argument("--member", required=True, help="归属成员")
    p.add_argument("--id", type=int, required=True, help="备忘 ID")
    p.add_argument("--unpin", action="store_true", help="取消置顶（默认置顶）")

    p = sub.add_parser("sheet-create", help="创建工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--kind", required=True, choices=["kv", "table"])
    p.add_argument("--pinned", action="store_true")

    p = sub.add_parser("sheet-list", help="列出工作表")
    p.add_argument("--member", required=True)

    p = sub.add_parser("sheet-show", help="显示工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)

    p = sub.add_parser("sheet-set", help="设置 kv 字段")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)

    p = sub.add_parser("sheet-unset", help="删除 kv 字段")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--field", required=True)

    p = sub.add_parser("sheet-row-add", help="表格加行")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--data", required=True, help="JSON 对象")

    p = sub.add_parser("sheet-row-edit", help="表格改行")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--row-id", type=int, required=True, dest="row_id")
    p.add_argument("--data", required=True, help="JSON 对象")

    p = sub.add_parser("sheet-row-delete", help="表格删行")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--row-id", type=int, required=True, dest="row_id")

    p = sub.add_parser("sheet-rename", help="重命名工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--new-title", required=True, dest="new_title")

    p = sub.add_parser("sheet-pin", help="置顶/取消置顶工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--unpin", action="store_true")

    p = sub.add_parser("sheet-delete", help="删除工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)

    p = sub.add_parser("chart-render", help="渲染工作表数据为图表 PNG")
    p.add_argument("--member", required=True)
    p.add_argument("--spec", required=True, help="JSON 图表规格")

    args = parser.parse_args()

    dispatch = {
        "note-add": cmd_note_add,
        "note-list": cmd_note_list,
        "note-search": cmd_note_search,
        "note-delete": cmd_note_delete,
        "note-pin": cmd_note_pin,
        "sheet-create": cmd_sheet_create,
        "sheet-list": cmd_sheet_list,
        "sheet-show": cmd_sheet_show,
        "sheet-set": cmd_sheet_set,
        "sheet-unset": cmd_sheet_unset,
        "sheet-row-add": cmd_sheet_row_add,
        "sheet-row-edit": cmd_sheet_row_edit,
        "sheet-row-delete": cmd_sheet_row_delete,
        "sheet-rename": cmd_sheet_rename,
        "sheet-pin": cmd_sheet_pin,
        "sheet-delete": cmd_sheet_delete,
        "chart-render": cmd_chart_render,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
