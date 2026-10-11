"""Calendar_Keeper 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

日程/待办按成员私有：完成/取消/查询/同步/状态一律强制注入发送者 member
（否则 CLI member="" 命中空库或抛错，且可读他人私有日历）。
"""

from __future__ import annotations

import logging


import members as _members
import paths as _paths
import tool_runtime as rt
from tool_runtime import fn, s, int_, boolean

_log = logging.getLogger("familyassist.agent")

ORDER = 60

COMMANDS = {"cal-add", "cal-list", "cal-update", "cal-done", "cal-delete", "cal-sync", "cal-status"}

LOOKAHEAD = int((rt.CONFIG.get("calendar") or {}).get("lookahead_days") or 10)


def target_member(args: dict, sender: str) -> str:
    """日程/待办的目标成员：显式 for-member（须已登记）覆盖，否则归发送者。

    默认归发送者（即使内容关于别人）；未登记的 for-member 退回发送者（不凭空建目录）。
    """
    target = (args.pop("for-member", "") or "").strip() or sender
    if target != sender and target not in _members.member_names():
        return sender
    return target


def tool_add_event(args):
    args = dict(args)
    sender = args.get("member", "")
    target = target_member(args, sender)
    src = args.get("source-image", "")
    if src:                                   # 原始图始终归发送者名下
        args["source-image"] = rt.relocate_image(src, sender, "schedule")
    args["member"] = target                   # 活动入目标成员日历
    return rt.run_cli("cal-add", {**args, "kind": "event"})


def tool_add_task(args):
    # LLM 用 due 表达截止日，CLI 统一收 --date
    args = dict(args)
    due = args.pop("due", "")
    if due:
        args["date"] = due
    sender = args.get("member", "")
    target = target_member(args, sender)
    src = args.get("source-image", "")
    if src:
        args["source-image"] = rt.relocate_image(src, sender, "tasks")
    args["member"] = target
    return rt.run_cli("cal-add", {**args, "kind": "task"})


def tool_calendar_status(args):
    args = dict(args)
    return rt.run_cli("cal-sync" if args.pop("sync", False) else "cal-status", args)


TOOLS = {
    "add_event": tool_add_event,
    "add_task": tool_add_task,
    "list_schedule": "cal-list",
    "update_schedule_item": "cal-update",
    "complete_task": "cal-done",
    "remove_schedule_item": "cal-delete",
    "calendar_status": tool_calendar_status,
}

MEMBER_LOCKED = set(TOOLS)
UNTRUSTED_TOOLS = {"list_schedule"}   # 含远程日历拉下来的条目（外人发邀请即可写入）

SCHEMAS = [
    fn("add_event", "添加家庭日程/活动/安排（自动同步到远程日历）", {
        "title": s("活动标题，如 孩子游泳课"),
        "date": s("日期 YYYY-MM-DD"),
        "start": s("开始时间 HH:MM（不知道具体时间就不填=全天）"),
        "end": s("结束时间 HH:MM"),
        "all-day": boolean("全天活动"),
        "location": s("地点"),
        "notes": s("备注"),
        "source-image": s("从图片（邀请函/海报/截图）建活动时，传那张已保存图片的路径，"
                          "留存原始材料；纯文字建活动不填"),
        "for-member": s("默认归你（当前成员）自己的日历，即使活动关于别人也是。仅当用户"
                        "明确说\"加到 X 的日历\"时，传该家庭成员名；否则不填"),
    }, ["title", "date"]),
    fn("add_task", "添加待办/任务（自动同步到远程待办清单）", {
        "title": s("待办内容，如 买生日蛋糕"),
        "due": s("截止日期 YYYY-MM-DD（没有就不填）"),
        "notes": s("备注"),
        "source-image": s("从图片（账单/发票/截图）建待办时，传那张已保存图片的路径，"
                          "留存原始材料；纯文字建待办不填"),
        "for-member": s("默认归你自己的待办清单。仅当用户明确说\"记到 X 的待办\"时，"
                        "传该家庭成员名；否则不填"),
    }, ["title"]),
    fn("list_schedule", "查询日程与待办，**未来和历史都能查**（查询前自动与远端核对/拉取；"
       "用户问\"接下来有什么安排\"\"待办清单\"\"上个月去了几次X\"\"7月的日程\"）", {
        "days": int_(f"未来窗口天数（默认 {LOOKAHEAD}）；查历史请改用 from/to"),
        "from": s("窗口起始日 YYYY-MM-DD，**可以是过去任意日期**（查历史必填；"
                  "省略 from 与 to = 只看未来 days 天）"),
        "to": s("窗口结束日 YYYY-MM-DD（给了 from 而省略 to，则到今天+days）"),
        "kind": s("只看活动或待办", enum=["event", "task"]),
        "all": boolean("包含已完成/已取消。数历史次数时通常要传 true，否则取消掉的那几次看不到"),
    }),
    fn("update_schedule_item", "编辑一条活动/待办，只传要改的字段（同步到远程，远端参会人等不受影响）。"
       "改期、改时间、补地点备注、合并重复时用，别删了重加", {
        "id": int_("日程 id"),
        "kind": s("活动(event)或待办(task)，按列表里该条的 [活动]/[待办]", enum=["event", "task"]),
        "title": s("新标题"),
        "date": s("新日期 YYYY-MM-DD：活动整条平移到该日（保留时刻）；待办 = 新截止日"),
        "start": s("新开始时间 HH:MM（活动）"),
        "end": s("新结束时间 HH:MM（活动）"),
        "all-day": boolean("改成全天活动"),
        "location": s("新地点"),
        "notes": s("新备注（覆盖）"),
    }, ["id", "kind"]),
    fn("complete_task", "标记一条待办完成（同步到远程）", {
        "id": int_("日程 id"),
    }, ["id"]),
    fn("remove_schedule_item", "取消一条日程/待办（已上云的同步删除远端）", {
        "id": int_("日程 id"),
        "kind": s("活动(event)或待办(task)。活动与待办 id 会撞号，"
                  "务必按 list_schedule 里该条显示的 [活动]/[待办] 一并传入", enum=["event", "task"]),
    }, ["id"]),
    fn("calendar_status", "查看日历同步状态并实时核对本地↔远端一致性（启用/配置/上次刷新/校验结论）", {
        "sync": boolean("true = 先与远程日历强制同步一轮（忽略节流）再报告"),
    }),
]

PROMPT_SECTIONS = [
    f"""## 日程与待办（按成员私有，与远程日历同步）
- 上下文注入你未来{LOOKAHEAD}天的日程快照，可能过期；用户问安排/待办时**必须调 list_schedule 再回答**（它先与远端核对）。不要主动播报日程
- **历史日程一样能查**：list_schedule 的 from 可以是任意过去日期；数次数带 all。绝不能说"查不到历史"
- 要加/记一件事 → 这一轮就调写工具，拿到返回再回复；没调工具 = 没做，绝不假装已完成
- 避免重复：加之前对照已有日程（同一天同一件事，措辞不同也算）。跳过、更新已有条目或合并由你判断，但必须告诉用户每条怎么处理的；替换会丢用户可能在意的信息时先问
- 归属默认发送者，即使内容关于别人；用户明确说"加到 X 的日历"才用 for-member
- 结果末尾"校验"行是本地↔远端核对结论：不一致/校验失败必须告诉用户""",
]


def schedule_context(member: str | None = None, db_path: str | None = None,
                     clip: int = 60, max_lines: int = 15) -> str:
    """成员未来 N 天日程 + 待办，拼成 system prompt 附加块（按成员私有，分活动/待办两库）。

    db_path 给定 → 直读该单库（测试/兼容）；否则按 member 读其 schedule + tasks 两库。
    任何失败返回空串 —— 日程注入绝不能拖垮 handle()。
    """
    try:
        import cal_db
        if db_path:
            rows = cal_db.list_upcoming(days=LOOKAHEAD, db_path=db_path)
        elif member:
            rows = []
            for domain in ("schedule", "tasks"):
                rows.extend(cal_db.list_upcoming(
                    days=LOOKAHEAD,
                    db_path=str(_paths.member_store(member, domain))))
            rows.sort(key=lambda r: (r["start_at"] == "", r["start_at"], r["id"]))
        else:
            return ""
        if not rows:
            return ""
        lines = []
        for r in rows[:max_lines]:
            title = r["title"][:clip] + ("…" if len(r["title"]) > clip else "")
            if r["kind"] == "event":
                st = r["start_at"]
                when = (st[5:10] + (" " + st[11:16] if len(st) > 10 else " 全天")) if st else ""
                loc = f" @{r['location']}" if r["location"] else ""
                lines.append(f"- {when} {title}{loc}".strip())
            else:
                due = f"（截止 {r['start_at'][5:10]}）" if r["start_at"] else ""
                lines.append(f"- ☐ {title}{due}")
        return (f"\n\n## 你未来{LOOKAHEAD}天的日程与待办（按成员私有，已静默同步自你的远程日历；"
                f"不要主动播报，仅在用户问到或相关时使用）\n" + rt.fence("\n".join(lines), "calendar"))
    except Exception:
        _log.exception("日程上下文注入失败（已跳过）")
        return ""


CONTEXT_FNS = [schedule_context]

from calendar_sync import calendar_tick  # noqa: E402
from image_gc import image_gc_tick  # noqa: E402

MESSAGE_TICKS = [calendar_tick, image_gc_tick]

IMAGE_ROUTES = [
    "邀请函/海报/预约等带日期的安排 → 日程；账单/催款等需跟进办理的 → 待办。都带 source-image 留存原图",
]
