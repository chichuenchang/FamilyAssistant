"""
Family Assistant — Calendar Keeper CLI

家庭日程/待办的记录、查询、完成、取消与远程日历同步。
Agent 经白名单子命令调用，输出纯文本。

用法: python .codewhale/skills/Calendar_Keeper/cli.py <command> [args]

命令：
    cal-add     新增活动/待办（写本地后即时尽力推送远程；未配置则留待同步）
    cal-list    未来 N 天日程 + 开放待办
    cal-done    完成一条待办（活动请用 cal-delete 取消）
    cal-delete  取消一条日程（已上云的会同步删除远端）
    cal-sync    立即强制刷新（忽略节流）
    cal-status  同步状态

测试钩子：环境变量 CAL_DB_PATH 可覆盖数据库路径（仅测试用）。
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 把本 skill 目录加入 sys.path（同目录 cal_db / calendar_sync）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cal_db
import calendar_sync

# 测试钩子：覆盖数据库路径，避免测试碰真实账本
_DB_OVERRIDE = os.environ.get("CAL_DB_PATH") or None


def _mark_backup_dirty() -> None:
    """写入后通知备份引擎（失败静默，绝不影响写入本身）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass


def _push_quietly() -> None:
    """写入后即时尽力推送（provider 未配置/失败都静默，留待下轮 tick）。"""
    try:
        calendar_sync.push_pending(db_path=_DB_OVERRIDE)
    except Exception:
        pass


def _sync_suffix(item_id: int) -> str:
    item = cal_db.get_item(item_id, db_path=_DB_OVERRIDE)
    return "已同步到日历" if item and item["synced"] == 1 else "待同步"


_WEEKDAYS = "一二三四五六日"


def _fmt_when(item: dict) -> str:
    """日程时间的人类可读格式。"""
    s = item["start_at"]
    if not s:
        return ""
    sd, st = s[:10], s[11:16]
    try:
        wd = "(" + _WEEKDAYS[date.fromisoformat(sd).weekday()] + ")"
    except ValueError:
        wd = ""
    day = f"{sd[5:]}{wd}"
    if item["kind"] == "task":
        return f"截止 {day}"
    if item["all_day"] or not st:
        return f"{day} 全天"
    e = item["end_at"]
    if e and e[:10] == sd and e[11:16]:
        return f"{day} {st}–{e[11:16]}"
    if e and e[:10] != sd:
        return f"{day} {st} → {e[5:10]} {e[11:16]}".rstrip()
    return f"{day} {st}"


def _fmt_item(item: dict) -> str:
    tag = "活动" if item["kind"] == "event" else "待办"
    state = {"done": "[已完成] ", "cancelled": "[已取消] "}.get(item["status"], "")
    when = _fmt_when(item)
    parts = [f"#{item['id']} [{tag}] {state}{item['title']}"]
    if when:
        parts.append(when)
    if item["location"]:
        parts.append(f"@{item['location']}")
    if item["member"]:
        parts.append(f"·{item['member']}")
    return " ".join(parts)


def cmd_cal_add(args):
    if args.kind == "event" and not args.date:
        print("错误: 活动必须带 --date", file=sys.stderr)
        sys.exit(1)
    if args.start and not args.date:
        print("错误: --start 必须配合 --date", file=sys.stderr)
        sys.exit(1)
    start_at = ""
    end_at = ""
    if args.date:
        start_at = args.date
        if args.kind == "event" and args.start and not args.all_day:
            start_at = f"{args.date}T{args.start}"
            if args.end:
                end_at = f"{args.date}T{args.end}"
    item_id = cal_db.add_item(
        kind=args.kind,
        title=args.title,
        start_at=start_at,
        end_at=end_at,
        all_day=args.all_day,
        location=args.location or "",
        notes=args.notes or "",
        member=args.member,
        db_path=_DB_OVERRIDE,
    )
    _mark_backup_dirty()
    _push_quietly()
    tag = "活动" if args.kind == "event" else "待办"
    print(f"已添加{tag} #{item_id}（{_sync_suffix(item_id)}）")


def cmd_cal_list(args):
    rows = cal_db.list_upcoming(
        days=args.days,
        kind=args.kind,
        member=args.member,
        include_closed=args.all,
        db_path=_DB_OVERRIDE,
    )
    if not rows:
        print("（无日程）")
        return
    for r in rows:
        print(_fmt_item(r))


def cmd_cal_done(args):
    item = cal_db.get_item(args.id, db_path=_DB_OVERRIDE)
    if item is None:
        print("[错误] 无此日程", file=sys.stderr)
        sys.exit(1)
    if item["kind"] != "task":
        print(f"[错误] #{args.id} 是活动，取消请用 cal-delete", file=sys.stderr)
        sys.exit(1)
    cal_db.set_status(args.id, "done", db_path=_DB_OVERRIDE)
    _mark_backup_dirty()
    _push_quietly()
    print(f"已完成待办 #{args.id}（{_sync_suffix(args.id)}）")


def cmd_cal_delete(args):
    item = cal_db.get_item(args.id, db_path=_DB_OVERRIDE)
    if item is None:
        print("[错误] 无此日程", file=sys.stderr)
        sys.exit(1)
    cal_db.set_status(args.id, "cancelled", db_path=_DB_OVERRIDE)
    _mark_backup_dirty()
    _push_quietly()
    print(f"已取消日程 #{args.id}「{item['title']}」（{_sync_suffix(args.id)}）")


def cmd_cal_sync(args):
    result = calendar_sync.force_sync(db_path=_DB_OVERRIDE)
    if result is None:
        print("日历 provider 未配置（需 GCAL_CLIENT_ID / GCAL_CLIENT_SECRET / "
              "GCAL_REFRESH_TOKEN 环境变量），当前仅本地记录。")
        return
    print(f"已刷新：推送 {result['pushed']} 条，"
          f"拉取活动 {result['events']} 个、待办 {result['tasks']} 条")
    for e in result["errors"]:
        print(f"  ⚠ {e}")


def cmd_cal_status(args):
    st = calendar_sync.status(db_path=_DB_OVERRIDE)
    print(f"日历同步: {'已启用' if st['enabled'] else '未启用（config.json calendar.enabled）'}")
    print(f"Provider: {'已配置' if st['configured'] else '未配置（GCAL_* 环境变量）'}")
    print(f"上次刷新: {st['last_refresh'] or '从未'}")
    print(f"待同步: {st['pending']} 条")
    if st["last_error"]:
        print(f"上次错误: {st['last_error']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Family Assistant — Calendar Keeper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("cal-add", help="新增活动/待办")
    p.add_argument("--member", required=True, help="创建成员（归属记录，家庭共享可见）")
    p.add_argument("--kind", required=True, choices=["event", "task"],
                   help="event=活动/安排，task=待办")
    p.add_argument("--title", required=True, help="标题")
    p.add_argument("--date", help="日期 YYYY-MM-DD（活动必填；待办=截止日，可省）")
    p.add_argument("--start", help="开始时间 HH:MM（不填或 --all-day 即全天）")
    p.add_argument("--end", help="结束时间 HH:MM")
    p.add_argument("--all-day", action="store_true", help="全天活动")
    p.add_argument("--location", help="地点")
    p.add_argument("--notes", help="备注")

    p = sub.add_parser("cal-list", help="未来 N 天日程 + 开放待办")
    p.add_argument("--days", type=int,
                   default=int(calendar_sync.CFG.get("lookahead_days", 10)),
                   help="窗口天数（默认 config calendar.lookahead_days）")
    p.add_argument("--kind", choices=["event", "task"], help="只看活动或待办")
    p.add_argument("--member", help="按创建成员过滤")
    p.add_argument("--all", action="store_true", help="包含已完成/已取消")

    p = sub.add_parser("cal-done", help="完成一条待办")
    p.add_argument("--id", type=int, required=True, help="日程 ID")

    p = sub.add_parser("cal-delete", help="取消一条日程（同步删除远端）")
    p.add_argument("--id", type=int, required=True, help="日程 ID")

    sub.add_parser("cal-sync", help="立即强制刷新远程日历")
    sub.add_parser("cal-status", help="同步状态")

    args = parser.parse_args()

    dispatch = {
        "cal-add": cmd_cal_add,
        "cal-list": cmd_cal_list,
        "cal-done": cmd_cal_done,
        "cal-delete": cmd_cal_delete,
        "cal-sync": cmd_cal_sync,
        "cal-status": cmd_cal_status,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
