"""
Family Assistant — Note Keeper CLI

个人备忘记录/检索/置顶。Agent 经白名单子命令调用，输出纯文本。
用法: python .codewhale/skills/Note_Keeper/cli.py <command> [args]

测试钩子：环境变量 NOTE_DB_PATH 可覆盖数据库路径（仅测试用）。
"""

import argparse
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

# 把本 skill 目录加入 sys.path（同目录 note_db）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import note_db

# 测试钩子：覆盖数据库路径，避免测试碰真实账本
_DB_OVERRIDE = os.environ.get("NOTE_DB_PATH") or None


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
        db_path=_DB_OVERRIDE,
    )
    print(f"已记录备忘 #{note_id}")


def cmd_note_list(args):
    rows = note_db.list_notes(
        member=args.member,
        limit=args.limit,
        db_path=_DB_OVERRIDE,
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
        db_path=_DB_OVERRIDE,
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
        db_path=_DB_OVERRIDE,
    )
    if ok:
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
        db_path=_DB_OVERRIDE,
    )
    if ok:
        if pinned:
            print(f"已置顶备忘 #{args.id}")
        else:
            print(f"已取消置顶 #{args.id}")
    else:
        print(f"[错误] 无此备忘", file=sys.stderr)
        sys.exit(1)


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

    args = parser.parse_args()

    dispatch = {
        "note-add": cmd_note_add,
        "note-list": cmd_note_list,
        "note-search": cmd_note_search,
        "note-delete": cmd_note_delete,
        "note-pin": cmd_note_pin,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
