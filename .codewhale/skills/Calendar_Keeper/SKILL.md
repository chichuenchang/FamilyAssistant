# Calendar Keeper

> 日程（活动）+ 待办，按成员私有，**直连远程日历，无本地库**。每次操作即读写远端；远端失败 = 操作失败，不缓冲不重试。
> 设计：`docs/superpowers/specs/2026-10-10-remote-only-calendar-design.md`。

## 代码

```
.codewhale/skills/Calendar_Keeper/
├── remote_schedule.py   ← 按成员+域取 provider，list/get/add/update/complete/delete/status
├── calendar_provider.py ← Google Calendar + Tasks 实现（契约见文件头，可换）
├── providers.py         ← (domain, name) → provider 注册表
├── image_gc.py          ← inbox/schedule/tasks 月目录按年龄清理
├── cli.py               ← cal-add / cal-list / cal-update / cal-done / cal-delete / cal-status
└── agent_tools.py       ← manifest
```

## 要点

- id = `e:<uid>`（活动）/ `t:<uid>`（待办），uid 为远端原值。旧 `e12` 形式被拒。
- 成员接入：`data/members.json` 该成员 `sync` 块 + `config.json calendar.enabled` + GCAL_* 凭据。缺任一 → `错误: <成员> 未配置远程日历`（其他成员当前即此占位）。
- 部分更新：先 `get_*` 远端当前值合并再 PATCH（`_event_body` 整体覆盖标题/地点/备注）。
- 删除活动 = 远端删除，历史不留痕；`--all` 只多出已完成待办。
- Google 全天活动 end 独占，`remote_schedule._item` 转成含当日（单日全天 end 置空），否则漏进次日窗口。
- `_window_iso` 用固定时区偏移：naive `astimezone()` 在 Windows 上对 1970 前本地时刻抛 `OSError [Errno 22]`，`--from` 省略（=1970）会整段失败。
- 来图不挂日程：留 `data/<成员>/inbox/YYYY-MM/`，agent 凭 `chat_history` 的「来件」路径找回。`image_gc` 每 `image_prune_interval_days` 删早于 `image_retention_years`（1）所在月的月目录；`notes/` 不动。

## 开启（Google）

1. Cloud Console 启用 Calendar API + Tasks API，OAuth 客户端 Desktop app（可复用 Remote_Backup 的）。
2. `setx GCAL_CLIENT_ID "..."`、`setx GCAL_CLIENT_SECRET "..."`。
3. `python .codewhale/skills/Calendar_Keeper/calendar_provider.py --auth` → `setx GCAL_REFRESH_TOKEN "..."`。
4. 可选非主日历：`setx GCAL_CALENDAR_ID "..."`（形如邮箱，隐私，不进 config）。
5. members.json 该成员：`"sync": {"schedule": {"provider":"google_calendar","enabled":true}, "tasks": {"provider":"google_tasks","enabled":true}}`。
6. `cal-status --member "<名>"` 确认。

`GCAL_*` 单账号；第二个 Google 成员需 provider 读按成员命名空间的凭据（未实现）。
权限：`calendar.events` + `tasks`。provider 代码不得含字面 token/key。

## 配置（config.json `calendar`）

`enabled` / `lookahead_days`（10，cal-list 默认窗口）/ `image_retention_years`（1）/ `image_prune_interval_days`（30）。
测试钩子：`CALENDAR_CONFIG`、`IMAGE_GC_STATE_DIR`、`DATA_ROOT`。
