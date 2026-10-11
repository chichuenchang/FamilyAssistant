"""
Family Assistant — Calendar Keeper CLI

家庭日程/待办直连远程日历（remote_schedule），无本地缓存。
Agent 经白名单子命令调用，输出纯文本；失败写 stderr，退出码 1。

用法: python .codewhale/skills/Calendar_Keeper/cli.py <command> [args]

命令：
    cal-add     新增活动/待办
    cal-list    未来 N 天日程 + 开放待办；--from/--to 查任意窗口（含历史）
    cal-update  编辑一条（只改给了的字段）
    cal-done    完成一条待办（活动请用 cal-delete）
    cal-delete  删除一条日程/待办
    cal-status  远程日历配置状态
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import remote_schedule as rs

_TAG = {"event": "活动", "task": "待办"}
_WEEKDAYS = "一二三四五六日"


def _fmt_when(item: dict) -> str:
    """日程时间的人类可读格式。非本年的日期带上年份（历史查询跨年，MM-DD 会有歧义）。"""
    s = item["start_at"]
    if not s:
        return ""
    sd, st = s[:10], s[11:16]
    try:
        wd = "(" + _WEEKDAYS[date.fromisoformat(sd).weekday()] + ")"
    except ValueError:
        wd = ""
    day = f"{sd if sd[:4] != str(date.today().year) else sd[5:]}{wd}"
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
    state = "[已完成] " if item["status"] == "done" else ""
    parts = [f"{item['id']} [{_TAG[item['kind']]}] {state}{item['title']}"]
    when = _fmt_when(item)
    if when:
        parts.append(when)
    if item["location"]:
        parts.append(f"@{item['location']}")
    return " ".join(parts)


def cmd_cal_add(args):
    if args.start and not args.date:
        raise ValueError("--start 必须配合 --date")
    start_at = end_at = ""
    if args.date:
        start_at = args.date
        if args.kind == "event" and args.start and not args.all_day:
            start_at = f"{args.date}T{args.start}"
            if args.end:
                end_at = f"{args.date}T{args.end}"
    item = rs.add(args.member, args.kind, args.title, start_at=start_at, end_at=end_at,
                  all_day=args.all_day, location=args.location or "", notes=args.notes or "")
    print(f"已添加{_TAG[args.kind]} {item['id']}「{item['title']}」")


def _shift(iso: str, days: int) -> str:
    """ISO 日期/日期时间整体平移 days 天（保留时间部分）。"""
    if not iso or not days:
        return iso
    return (date.fromisoformat(iso[:10]) + timedelta(days=days)).isoformat() + iso[10:]


def _edited_times(item: dict, args) -> dict:
    """由 --date/--start/--end/--all-day 算出新的 start_at/end_at/all_day（只返回有变的）。
    只给 --date = 整条平移到该日（保留时刻与跨天时长）；只给 --start = 保留原时长；待办的 --date = 截止日。"""
    if item["kind"] == "task":
        return {"start_at": args.date} if args.date else {}
    start, end = item["start_at"], item["end_at"]
    day = args.date or start[:10]
    if args.date and start:
        delta = (date.fromisoformat(args.date) - date.fromisoformat(start[:10])).days
        start, end = _shift(start, delta), _shift(end, delta)
    if args.all_day:
        return {"start_at": day, "end_at": "", "all_day": 1}
    if args.start:
        new_start = f"{day}T{args.start}"
        if args.end:
            end = f"{day}T{args.end}"
        elif len(start) > 10 and len(end) > 10:     # 只改开始：保留原时长
            dur = datetime.fromisoformat(end) - datetime.fromisoformat(start)
            end = (datetime.fromisoformat(new_start) + dur).isoformat(timespec="minutes")
        else:
            end = ""
        start = new_start
    elif args.end:
        if len(start) <= 10:
            raise ValueError("全天活动改 --end 前先给 --start")
        end = f"{day}T{args.end}"
    out = {"start_at": start, "end_at": end}
    if len(start) > 10:
        out["all_day"] = 0
    return out


def cmd_cal_update(args):
    item = rs.get(args.member, args.id)
    if item is None:
        raise rs.NotFound(args.id)
    fields = {k: v for k, v in (("title", args.title), ("location", args.location),
                                ("notes", args.notes)) if v is not None}
    fields.update(_edited_times(item, args))
    if not fields:
        raise ValueError("没给要改的字段")
    print(f"已更新 {_fmt_item(rs.update(args.member, item, fields))}")


def _parse_day(value: str, flag: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{flag} 需要 YYYY-MM-DD 格式，收到 {value!r}")


def cmd_cal_list(args):
    if args.date_from or args.date_to:
        start = _parse_day(args.date_from, "--from") if args.date_from else ""
        end = (_parse_day(args.date_to, "--to") if args.date_to
               else (date.today() + timedelta(days=args.days)).isoformat())
        if start and start > end:
            raise ValueError(f"--from({start}) 晚于 --to({end})")
        rows = rs.list_items(args.member, args.kind, start, end, closed=args.all)
        empty = f"（{start or '最早'}~{end} 无日程）"
    else:
        today = date.today()
        rows = rs.list_items(args.member, args.kind, today.isoformat(),
                             (today + timedelta(days=args.days)).isoformat(),
                             closed=args.all, undated=True)
        empty = "（无日程）"
    print("\n".join(_fmt_item(r) for r in rows) if rows else empty)


def cmd_cal_done(args):
    item = rs.complete(args.member, args.id)
    print(f"已完成待办 {item['id']}「{item['title']}」")


def cmd_cal_delete(args):
    item = rs.delete(args.member, args.id)
    print(f"已取消日程 {item['id']}「{item['title']}」")


def cmd_cal_status(args):
    switch = "已启用" if rs.CFG.get("enabled") else "未启用（config.json calendar.enabled）"
    print(f"日历（{args.member}）: 总开关{switch}")
    for domain, ok in rs.status(args.member).items():
        print(f"  {'活动' if domain == 'schedule' else '待办'}: {'已配置' if ok else '未配置'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Family Assistant — Calendar Keeper CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    id_help = "条目 id，照列表原样传（e:… 活动 / t:… 待办）"

    def member(p):
        p.add_argument("--member", required=True, help="成员（私有日历）")

    p = sub.add_parser("cal-add", help="新增活动/待办")
    member(p)
    p.add_argument("--kind", required=True, choices=["event", "task"],
                   help="event=活动/安排，task=待办")
    p.add_argument("--title", required=True, help="标题")
    p.add_argument("--date", help="日期 YYYY-MM-DD（活动必填；待办=截止日，可省）")
    p.add_argument("--start", help="开始时间 HH:MM（不填或 --all-day 即全天）")
    p.add_argument("--end", help="结束时间 HH:MM")
    p.add_argument("--all-day", action="store_true", help="全天活动")
    p.add_argument("--location", help="地点")
    p.add_argument("--notes", help="备注")

    p = sub.add_parser("cal-list", help="未来 N 天日程 + 开放待办（--from/--to 查任意历史窗口）")
    member(p)
    p.add_argument("--days", type=int, default=int(rs.CFG["lookahead_days"]),
                   help="窗口天数（默认 config calendar.lookahead_days）")
    p.add_argument("--from", dest="date_from",
                   help="窗口起始日 YYYY-MM-DD（可为过去；省略=不限过去）")
    p.add_argument("--to", dest="date_to", help="窗口结束日 YYYY-MM-DD（省略=今天 + --days）")
    p.add_argument("--kind", choices=["event", "task"], help="只看活动或待办")
    p.add_argument("--all", action="store_true", help="包含已完成待办")

    p = sub.add_parser("cal-update", help="编辑一条活动/待办（只改给了的字段）")
    member(p)
    p.add_argument("--id", required=True, help=id_help)
    p.add_argument("--title")
    p.add_argument("--date", help="新日期 YYYY-MM-DD（活动整条平移；待办=截止日）")
    p.add_argument("--start", help="新开始时间 HH:MM")
    p.add_argument("--end", help="新结束时间 HH:MM")
    p.add_argument("--all-day", action="store_true", help="改成全天")
    p.add_argument("--location")
    p.add_argument("--notes")

    p = sub.add_parser("cal-done", help="完成一条待办")
    member(p)
    p.add_argument("--id", required=True, help=id_help)

    p = sub.add_parser("cal-delete", help="删除一条日程/待办（远端同步删除）")
    member(p)
    p.add_argument("--id", required=True, help=id_help)

    p = sub.add_parser("cal-status", help="远程日历配置状态")
    member(p)

    args = parser.parse_args()
    dispatch = {
        "cal-add": cmd_cal_add,
        "cal-list": cmd_cal_list,
        "cal-update": cmd_cal_update,
        "cal-done": cmd_cal_done,
        "cal-delete": cmd_cal_delete,
        "cal-status": cmd_cal_status,
    }
    try:
        dispatch[args.command](args)
    except rs.NotFound:
        print("[错误] 无此日程", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"错误: 远程日历调用失败: {e}", file=sys.stderr)
        return 1
    except (rs.NotConfigured, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
