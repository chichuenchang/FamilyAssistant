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

COMMANDS = {"cal-add", "cal-list", "cal-done", "cal-delete", "cal-sync", "cal-status"}

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


TOOLS = {
    "add_event": tool_add_event,
    "add_task": tool_add_task,
    "list_schedule": "cal-list",
    "complete_task": "cal-done",
    "remove_schedule_item": "cal-delete",
    "sync_calendar": "cal-sync",
    "calendar_status": "cal-status",
}

MEMBER_LOCKED = set(TOOLS)

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
    fn("complete_task", "标记一条待办完成（同步到远程）", {
        "id": int_("日程 id"),
    }, ["id"]),
    fn("remove_schedule_item", "取消一条日程/待办（已上云的同步删除远端）", {
        "id": int_("日程 id"),
        "kind": s("活动(event)或待办(task)。活动与待办 id 会撞号，"
                  "务必按 list_schedule 里该条显示的 [活动]/[待办] 一并传入", enum=["event", "task"]),
    }, ["id"]),
    fn("sync_calendar", "立即与远程日历强制同步一轮（用户说\"刷新/同步日历\"）", {}),
    fn("calendar_status", "查看日历同步状态并实时核对本地↔远端一致性（启用/配置/上次刷新/校验结论）", {}),
]

PROMPT_SECTIONS = [
    f"""## 日程与待办（与远程日历静默同步，按成员私有）
- 未来{LOOKAHEAD}天**你（当前成员）的**日程/待办会自动注入上下文（每人只见自己的，活动与待办分库）
- **不要主动播报日程**：仅当用户问到（"接下来有什么安排""待办清单"）或与当前话题直接相关时才提及
- **加日程前先查重**：调 add_event/add_task 前，先比对已注入的未来日程（窗口外可先 list_schedule）。
  判定重复看"同一件事"——同一天 + 同一活动，标题措辞不同也算（如"游泳烧烤"vs"Hotdog Roast & Swim"）。
  对每条疑似重复按下面三种情况处理，**任何一种都必须明确告诉用户你做了什么，绝不静默**：
  1) 已有的更详细（有时间/地点/备注而新的没有）→ 丢弃新的，不 add；告诉用户"已存在更详细的同名日程，未重复添加"。
  2) 新的更详细（补了时间/地点/备注等有用信息）→ 先 remove_schedule_item 删旧（会同步删远端），再 add_event 加新的；
     告诉用户"发现重复，已用信息更全的版本替换旧的"。
  3) 新旧基本一致、新的没补任何有用信息 → 丢弃新的；告诉用户"已存在相同日程，未重复添加"。
  拿不准是不是同一件事，或替换会丢用户可能在意的东西时 → 先问用户，别擅自删。
  无重复 → 正常添加。报告里点明每条的处理（已加/已跳过重复/已替换），让用户能纠正或补特殊要求。
- 用户说"安排/约了/X号要做Y/加个日程/活动"→ add_event（活动必须有日期；有具体时间则给 start/end）
- 用户说"要做X/记个待办/任务"→ add_task（有截止日给 due）
- **必须先调工具再回复**：用户的话只要听起来是要加/记一件事——给了日期、时间、"几点去X""X号做Y""帮我加/记/设个提醒/约了/安排"等——你**这一轮就必须立即调用对应写工具**（活动→add_event，待办→add_task），拿到返回结果后再回复。绝不能只用自然语言说"已加上/搞定/记好了"而不调工具；没调工具=这件事根本没做。拿不准是活动还是待办、或缺日期时间，就先调最合适的工具用默认值，或一句话问清后再调——但不要假装已完成
- **calendar_status 现在核对真伪**：它会实时查询远端并与本地对账，可用于回答"同步了吗/本地和日历一致吗"；但某条新日程是否创建成功，仍以你 add_event 的返回为准
- **归属默认发送者**：活动/待办默认进**你（当前成员）**的日历/待办，即使内容关于别的家庭成员
  （如你发 Robin 的活动，默认进你的日历）；从图片建的，原始图也存你名下。仅当用户**明确**说
  "加到 X 的日历/记到 X 的待办"时，才用 for-member 路由到该成员。备忘不跨成员（按成员私有）。
- 用户问"接下来有什么安排/这周有什么事/我的待办"→ **必须调 list_schedule 再回答**（它会先与远端核对同步——家人可能刚在手机日历上加了事、或直接划掉了待办）；注入的上下文只是快照，可能过期，仅作话题参考
- **历史日程一样能查**：问"上个月/7月/去年 X 了几次""那次是哪天"→ list_schedule 传 `from`/`to`
  框住那段时间（`from` 可以是任意久远的过去，会实拉远端历史），数次数时再传 `all: true`
  把已取消/已完成的也带出来。**绝不能说"我只能看到未来日程/查不到历史"**——那是错的
- 用户说"做完了/办完了"→ complete_task；"取消/不去了/删掉"→ remove_schedule_item，
  **必须带 kind**（活动传 event、待办传 task，按该条在列表/上下文里显示的 [活动]/[待办] 判定）：
  活动与待办 id 会撞号，不带 kind 且两边都有这个号时工具会报错拒删，会删错对象
- 用户说"刷新日历/同步日历"→ sync_calendar；问同步状态 → calendar_status
- 新增/完成/取消会自动同步到远程日历；"待同步"= 暂未推送会自动重试，无需向用户解释技术细节
- 日程工具结果末尾的"校验"行 = 本地↔远端实时核对结论：一致/已自动修复时一笔带过即可；出现"本地≠远端/校验失败"时必须明确转告用户哪里不一致，绝不隐瞒""",
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
                f"不要主动播报，仅在用户问到或相关时使用）\n" + "\n".join(lines))
    except Exception:
        _log.exception("日程上下文注入失败（已跳过）")
        return ""


CONTEXT_FNS = [schedule_context]

from calendar_sync import calendar_tick  # noqa: E402
from image_gc import image_gc_tick  # noqa: E402

MESSAGE_TICKS = [calendar_tick, image_gc_tick]

IMAGE_ROUTES = [
    "邀请函/活动海报/预约/带日期时间的安排 → add_event（有日期；有具体时间给 start/end，"
    "location 给地点）；账单/发票/催款等需要跟进办理的 → add_task（截止日给 due）。"
    "两者都把上面的保存路径传给 source-image，留存原始材料（日后可定期清理）。"
    "默认进你（发送者）的日历/待办，即使活动关于别的成员；仅用户明确说加到某成员时才传 for-member。",
]
