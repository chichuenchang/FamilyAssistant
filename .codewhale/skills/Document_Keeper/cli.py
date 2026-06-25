"""
Family Assistant — Document Keeper CLI

家庭重要文档归档/检索/到期提醒。Agent 经白名单子命令调用，输出纯文本。
用法: python .codewhale/skills/Document_Keeper/cli.py <command> [args]

测试钩子：环境变量 DOC_KEEPER_DB 可覆盖数据库路径（仅测试用）。
"""

import argparse
import os
import re
import shutil
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

# 把本 skill 目录加入 sys.path（同目录 doc_db / doc_models）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 成员注册表（Agent_Runtime skill；跨 skill 经 sys.path，与 Expense_Tracker 同模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import members as members_registry
import paths as _paths

import doc_db
from doc_models import DOC_TYPES, DOC_STATUSES, DOCUMENTS_DIR, REMINDER_LEAD_DAYS

ROOT = Path(__file__).resolve().parents[3]

# 测试钩子：覆盖数据库路径，避免测试碰真实账本
_DB_OVERRIDE = os.environ.get("DOC_KEEPER_DB") or None

# 备份脏标记：写入类命令成功后调用（Remote_Backup skill；失败静默，绝不影响写入）
_BACKUP_WRITE_COMMANDS = {"doc-add", "doc-update", "doc-ack", "doc-remove"}


def _mark_backup_dirty() -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass


def _validate_member(name: str) -> str:
    """非空成员名必须已登记；返回原值或抛 ValueError。空值放行（家庭级）。"""
    if not name:
        return ""
    known = members_registry.member_names()
    if name not in known:
        raise ValueError(
            f"未知成员 '{name}'。已登记: {', '.join(known) or '（无）'}。用 member-add 添加。")
    return name


def _store_file(src: str, doc_type: str, title: str, member: str = "") -> str:
    """复制文件到 documents/<doc_type>/，返回相对 data_root 的路径（正斜杠）。

    文件名 <成员>_<安全标题><ext>：doc_type 已体现在目录、长期文档不带日期，
    故均不入名；无成员（家庭共享）省略前缀。已在文档目录内的文件不复制，原样返回。
    """
    p = Path(src)
    abs_p = (p if p.is_absolute() else ROOT / p).resolve()
    if not abs_p.exists():
        raise ValueError(f"文件不存在: {src}")
    docs_root = DOCUMENTS_DIR.resolve()
    if abs_p.is_relative_to(docs_root):
        return _paths.to_rel(abs_p)
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:40] or "untitled"
    safe_member = re.sub(r'[\\/:*?"<>|\s]+', "_", member).strip("_")
    dest_dir = docs_root / doc_type
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_member}_{safe_title}" if safe_member else safe_title
    dest = dest_dir / f"{stem}{abs_p.suffix.lower()}"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{stem}_{n}{abs_p.suffix.lower()}"
        n += 1
    shutil.copy2(abs_p, dest)
    return _paths.to_rel(dest)


def _fmt_due(d: dict) -> str:
    left = d["days_left"]
    when = f"已过期 {-left} 天" if left < 0 else f"{left} 天后到期（{d['expiry_date']}）"
    ack = "" if not d["acknowledged"] else " [已确认]"
    note = f" — {d['action_note']}" if d["action_note"] else ""
    who = f" [{d['member']}]" if d["member"] else ""
    return f"#{d['id']} {d['title']}（{d['doc_type']}）{who} {when}{ack}{note}"


def cmd_doc_add(args):
    member = _validate_member(args.member or "")
    file_rel = ""
    if args.file:
        file_rel = _store_file(args.file, args.type, args.title, member)
    doc_id, dup = doc_db.add_document(
        doc_type=args.type,
        title=args.title,
        member=member,
        issuer=args.issuer or "",
        doc_number=args.number or "",
        issue_date=args.issue_date or "",
        expiry_date=args.expiry or "",
        action_note=args.action_note or "",
        remind_days=args.remind_days,
        file_path=file_rel,
        ocr_text=args.ocr_text or "",
        notes=args.notes or "",
        force=args.force,
        db_path=_DB_OVERRIDE,
    )
    if dup:
        print(f"⚠ 疑似重复！已存在 #{dup['id']} {dup['title']}（{dup['doc_type']}，"
              f"编号 {dup['doc_number'] or '无'}）。")
        print("未写入。如确认不是重复，请加 --force 强制写入。")
        return
    expiry = f"，到期 {args.expiry}" if args.expiry else ""
    saved = f"，文件 {file_rel}" if file_rel else ""
    print(f"已归档文档 #{doc_id}: {args.title}（{args.type}）{expiry}{saved}")


def cmd_doc_list(args):
    rows = doc_db.get_documents(
        doc_type=args.type, member=args.member, keyword=args.keyword,
        status=args.status, limit=args.limit or 200, db_path=_DB_OVERRIDE,
    )
    if not rows:
        print("没有找到文档。")
        return
    for r in rows:
        who = f" [{r['member']}]" if r["member"] else ""
        expiry = f" 到期 {r['expiry_date']}" if r["expiry_date"] else " 长期有效"
        print(f"#{r['id']} [{r['status']}] {r['title']}（{r['doc_type']}）{who}"
              f" | {r['issuer'] or '-'} | 编号 {r['doc_number'] or '-'} |{expiry}")


def cmd_doc_show(args):
    d = doc_db.get_document(args.id, db_path=_DB_OVERRIDE)
    if d is None:
        print(f"未找到文档 #{args.id}")
        return
    print(f"#{d['id']} {d['title']}（{d['doc_type']}）[{d['status']}]")
    print(f"成员: {d['member'] or '家庭'} | 签发方: {d['issuer'] or '-'} | 编号: {d['doc_number'] or '-'}")
    print(f"签发: {d['issue_date'] or '-'} | 到期: {d['expiry_date'] or '长期有效'}"
          f" | 提醒提前: {d['remind_days'] if d['remind_days'] is not None else REMINDER_LEAD_DAYS} 天"
          f" | 提醒{'已确认' if d['acknowledged'] else '未确认'}")
    if d["action_note"]:
        print(f"到期动作: {d['action_note']}")
    if d["file_path"]:
        print(f"文件: {d['file_path']}")
    if d["notes"]:
        print(f"备注: {d['notes']}")
    if d["ocr_text"]:
        excerpt = d["ocr_text"][:300]
        print(f"OCR 摘录: {excerpt}{'…' if len(d['ocr_text']) > 300 else ''}")


def cmd_doc_file(args):
    d = doc_db.get_document(args.id, db_path=_DB_OVERRIDE)
    if d is None or not d.get("file_path"):
        print("[错误] 文档无文件", file=sys.stderr)
        sys.exit(1)
    print(d["file_path"])


def cmd_doc_due(args):
    rows = doc_db.due_documents(days=args.days, db_path=_DB_OVERRIDE)
    if not rows:
        print("没有即将到期的文档。")
        return
    for d in rows:
        print(_fmt_due(d))


def cmd_doc_update(args):
    fields = {}
    mapping = {
        "type": "doc_type", "title": "title", "issuer": "issuer",
        "number": "doc_number", "issue_date": "issue_date", "expiry": "expiry_date",
        "action_note": "action_note", "remind_days": "remind_days",
        "status": "status", "notes": "notes",
    }
    for arg_name, col in mapping.items():
        v = getattr(args, arg_name)
        if v is not None:
            fields[col] = v
    if args.member is not None:
        fields["member"] = _validate_member(args.member)
    ok = doc_db.update_document(args.id, db_path=_DB_OVERRIDE, **fields)
    print(f"{'已更新' if ok else '未找到'} 文档 #{args.id}")


def cmd_doc_ack(args):
    ok = doc_db.ack_document(args.id, db_path=_DB_OVERRIDE)
    print(f"{'已确认提醒' if ok else '未找到'} 文档 #{args.id}")


def cmd_doc_remove(args):
    ok = doc_db.remove_document(args.id, delete_file=args.delete_file, db_path=_DB_OVERRIDE)
    extra = "（含原始文件）" if ok and args.delete_file else ""
    print(f"{'已删除' if ok else '未找到'} 文档 #{args.id}{extra}")


def main():
    doc_db.init_db(db_path=_DB_OVERRIDE)

    parser = argparse.ArgumentParser(description="Family Assistant — Document Keeper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doc-add", help="归档一份文档")
    p.add_argument("--type", required=True, choices=DOC_TYPES)
    p.add_argument("--title", required=True)
    p.add_argument("--member", help="归属成员（须已登记；空 = 家庭级）")
    p.add_argument("--issuer", help="签发方：房东/保险公司/政府机构")
    p.add_argument("--number", help="编号：保单号/证件号")
    p.add_argument("--issue-date", help="签发日期 YYYY-MM-DD")
    p.add_argument("--expiry", help="到期日期 YYYY-MM-DD；长期有效不填")
    p.add_argument("--action-note", help="到期要做什么，如 提前60天通知房东")
    p.add_argument("--remind-days", type=int, help=f"提前几天提醒（默认 {REMINDER_LEAD_DAYS}）")
    p.add_argument("--file", help="原始文件路径；自动复制到文档目录")
    p.add_argument("--ocr-text", help="OCR 全文，用于关键词检索")
    p.add_argument("--notes")
    p.add_argument("--force", action="store_true", help="跳过重复检查，强制写入")

    p = sub.add_parser("doc-list", help="查询文档")
    p.add_argument("--type", choices=DOC_TYPES)
    p.add_argument("--member")
    p.add_argument("--keyword", help="匹配 标题/全文/备注")
    p.add_argument("--status", choices=list(DOC_STATUSES))
    p.add_argument("--limit", type=int)

    p = sub.add_parser("doc-show", help="查看文档详情")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("doc-file", help="打印文档原件的 data 相对路径（用于发送）")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("doc-due", help="即将到期/已过期的文档")
    p.add_argument("--days", type=int, help="查看几天内到期（默认按各文档提前量）")

    p = sub.add_parser("doc-update", help="更新文档（续约改到期日、归档等）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--type", choices=DOC_TYPES)
    p.add_argument("--title")
    p.add_argument("--member")
    p.add_argument("--issuer")
    p.add_argument("--number")
    p.add_argument("--issue-date")
    p.add_argument("--expiry", help="新到期日（会重新进入提醒）")
    p.add_argument("--action-note")
    p.add_argument("--remind-days", type=int)
    p.add_argument("--status", choices=list(DOC_STATUSES))
    p.add_argument("--notes")

    p = sub.add_parser("doc-ack", help="确认到期提醒（每日推送跳过）")
    p.add_argument("--id", type=int, required=True)

    # 仅本机使用；不在 wechat.allowed_commands 白名单内，Agent 调不到
    p = sub.add_parser("doc-remove", help="删除文档记录（仅本机）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--delete-file", action="store_true", help="同时删除原始文件")

    args = parser.parse_args()

    dispatch = {
        "doc-add": cmd_doc_add,
        "doc-list": cmd_doc_list,
        "doc-show": cmd_doc_show,
        "doc-file": cmd_doc_file,
        "doc-due": cmd_doc_due,
        "doc-update": cmd_doc_update,
        "doc-ack": cmd_doc_ack,
        "doc-remove": cmd_doc_remove,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # finally：doc-add 可能已复制文件后才失败，文件已落盘也要标脏
        if args.command in _BACKUP_WRITE_COMMANDS:
            _mark_backup_dirty()


if __name__ == "__main__":
    main()
