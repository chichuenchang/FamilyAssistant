# Calendar Keeper

> 日程（活动）+ 待办，按成员私有，**直连远程日历，无本地库**。每次操作即读写远端；远端失败 = 操作失败，不缓冲不重试。
> 设计：`docs/superpowers/specs/2026-10-10-remote-only-calendar-design.md`。行为细节（id、条目、窗口、全天 end）见 `remote_schedule.py` 文件头与 docstring。

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

## 开启（Google）

1. Cloud Console 启用 Calendar API + Tasks API，OAuth 客户端 Desktop app（可复用 Remote_Backup 的）。
2. `setx GCAL_CLIENT_ID "..."`、`setx GCAL_CLIENT_SECRET "..."`。
3. `python .codewhale/skills/Calendar_Keeper/calendar_provider.py --auth` → `setx GCAL_REFRESH_TOKEN "..."`。
4. 可选非主日历：`setx GCAL_CALENDAR_ID "..."`（形如邮箱，隐私，不进 config）。
5. members.json 该成员：`"sync": {"schedule": {"provider":"google_calendar","enabled":true}, "tasks": {"provider":"google_tasks","enabled":true}}`。
6. `cal-status --member "<名>"` 确认。

`GCAL_*` 单账号；第二个 Google 成员需 provider 读按成员命名空间的凭据（未实现）。
权限：`calendar.events` + `tasks`。provider 代码不得含字面 token/key。

## 配置

`config.json` `calendar`，键见 `FamilyAssistant.md` 配置表。测试钩子：`CALENDAR_CONFIG`、`IMAGE_GC_STATE_DIR`、`DATA_ROOT`。
