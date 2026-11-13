"""Calendar_Keeper 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

日程/待办按成员私有，直连远程日历：完成/取消/查询/状态一律强制注入发送者 member
（否则 CLI 报 "需要 --member"，且可读他人私有日历）。
"""

from __future__ import annotations

import members as _members
import remote_schedule as _rs
import tool_runtime as rt
from tool_runtime import fn, s, int_, boolean

ORDER = 60

COMMANDS = {"cal-add", "cal-list", "cal-update", "cal-done", "cal-delete", "cal-status"}

LOOKAHEAD = int(_rs.CFG["lookahead_days"])

_ID = "条目 id，照列表原样传（e:… 活动 / t:… 待办）"


def target_member(args: dict, sender: str) -> str:
    """日程/待办的目标成员：显式 for-member（须已登记）覆盖，否则归发送者。

    默认归发送者（即使内容关于别人）；未登记的 for-member 退回发送者。
    """
    target = (args.pop("for-member", "") or "").strip() or sender
    if target != sender and target not in _members.member_names():
        return sender
    return target


def _routed(args: dict) -> dict:
    """来图不入日程（照片留 inbox，凭 chat_history 来件找回）；member 换成目标成员。"""
    args = dict(args)
    args.pop("source-image", None)
    args["member"] = target_member(args, args.get("member", ""))
    return args


def tool_add_event(args):
    return rt.run_cli("cal-add", {**_routed(args), "kind": "event"})


def tool_add_task(args):
    # LLM 用 due 表达截止日，CLI 统一收 --date
    args = _routed(args)
    due = args.pop("due", "")
    if due:
        args["date"] = due
    return rt.run_cli("cal-add", {**args, "kind": "task"})


TOOLS = {
    "add_event": tool_add_event,
    "add_task": tool_add_task,
    "list_schedule": "cal-list",
    "update_schedule_item": "cal-update",
    "complete_task": "cal-done",
    "remove_schedule_item": "cal-delete",
    "calendar_status": "cal-status",
}

MEMBER_LOCKED = set(TOOLS)
UNTRUSTED_TOOLS = {"list_schedule"}   # 远程日历条目（外人发邀请即可写入）

SCHEMAS = [
    fn("add_event", "添加家庭日程/活动/安排（写入远程日历）", {
        "title": s("活动标题，如 孩子游泳课"),
        "date": s("日期 YYYY-MM-DD"),
        "start": s("开始时间 HH:MM（不知道具体时间就不填=全天）"),
        "end": s("结束时间 HH:MM"),
        "all-day": boolean("全天活动"),
        "location": s("地点"),
        "notes": s("备注"),
        "for-member": rt.member_param("默认归你（当前成员）自己的日历，即使活动关于别人也是。仅当用户"
                        "明确说\"加到 X 的日历\"时，传该家庭成员名；否则不填"),
    }, ["title", "date"]),
    fn("add_task", "添加待办/任务（写入远程待办清单）", {
        "title": s("待办内容，如 买生日蛋糕"),
        "due": s("截止日期 YYYY-MM-DD（没有就不填）"),
        "notes": s("备注"),
        "for-member": rt.member_param("默认归你自己的待办清单。仅当用户明确说\"记到 X 的待办\"时，"
                        "传该家庭成员名；否则不填"),
    }, ["title"]),
    fn("list_schedule", "查询远程日历的日程与待办，**未来和历史都能查**（用户问\"接下来有什么安排\""
       "\"待办清单\"\"上个月去了几次X\"\"7月的日程\"）", {
        "days": int_(f"未来窗口天数（默认 {LOOKAHEAD}）；查历史请改用 from/to"),
        "from": s("窗口起始日 YYYY-MM-DD，**可以是过去任意日期**（查历史必填；"
                  "省略 from 与 to = 只看未来 days 天）"),
        "to": s("窗口结束日 YYYY-MM-DD（给了 from 而省略 to，则到今天+days）"),
        "kind": s("只看活动或待办", enum=["event", "task"]),
        "all": boolean("包含已完成待办"),
    }),
    fn("update_schedule_item", "编辑一条活动/待办，只传要改的字段（远端参会人等不受影响）。"
       "改期、改时间、补地点备注、合并重复时用，别删了重加", {
        "id": s(_ID),
        "title": s("新标题"),
        "date": s("新日期 YYYY-MM-DD：活动整条平移到该日（保留时刻）；待办 = 新截止日"),
        "start": s("新开始时间 HH:MM（活动）"),
        "end": s("新结束时间 HH:MM（活动）"),
        "all-day": boolean("改成全天活动"),
        "location": s("新地点"),
        "notes": s("新备注（覆盖）"),
    }, ["id"]),
    fn("complete_task", "标记一条待办完成", {
        "id": s("待办 id，照列表原样传（t:…）"),
    }, ["id"]),
    fn("remove_schedule_item", "删除一条日程/待办（远端删除，不可恢复）", {
        "id": s(_ID),
    }, ["id"]),
    fn("calendar_status", "查看你的远程日历是否已配置（活动/待办各自）", {}),
]

PROMPT_SECTIONS = [
    """## 日程与待办（按成员私有，直连远程日历）
- 用户问安排/待办时**必须调 list_schedule 再回答**。不要主动播报日程
- **历史日程一样能查**：list_schedule 的 from 可以是任意过去日期。绝不能说"查不到历史"；已删除的活动远端不留痕，数次数时说明这一点
- 要加/记一件事 → 这一轮就调写工具，拿到返回再回复；没调工具 = 没做，绝不假装已完成
- 避免重复：加之前先 list_schedule 对照（同一天同一件事，措辞不同也算）。跳过、更新已有条目或合并由你判断，但必须告诉用户每条怎么处理的；替换会丢用户可能在意的信息时先问
- 归属默认发送者，即使内容关于别人；用户明确说"加到 X 的日历"才用 for_member
- 工具报"未配置远程日历"或"远程日历调用失败" → 如实告诉用户没记上，别假装成功
- 来图建的日程不挂图；用户要找原图 → chat_history 按标题/日期找那一轮的"来件"路径""",
]

from image_gc import image_gc_tick  # noqa: E402

MESSAGE_TICKS = [image_gc_tick]

IMAGE_ROUTES = [
    "邀请函/海报/预约等带日期的安排 → 日程；账单/催款等需跟进办理的 → 待办。原图留 inbox，日后凭 chat_history 来件找回",
]
