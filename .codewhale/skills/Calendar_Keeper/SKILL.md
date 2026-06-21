# Calendar Keeper

> 日程（活动/安排）与待办（任务），**按成员私有**，与各成员自己的远程日历静默双向同步。
> 活动与待办分库分 provider：每个成员的 schedule（活动）/ tasks（待办）各有独立存储、同步状态、
> 同步偏好，可指向不同远程平台。当前只有 Alex 配了 Google（Calendar+Tasks），其余成员本地模式。
> 远程是事实源（家人直接在手机上改日历）；本地是缓存 + 离线缓冲。未配置 provider 时本地照常工作。

## 代码位置

```
.codewhale/skills/Calendar_Keeper/
├── SKILL.md             ← 本文件
├── cal_db.py            ← 存储层（schedule_items 表；每成员每域各一个 .db）
├── calendar_sync.py     ← 同步引擎：按(成员,域)先推后拉、对账、状态；calendar_tick 遍历成员
├── calendar_provider.py ← Google Calendar + Tasks 实现（契约见文件头注释，可换）
├── providers.py         ← provider 注册表：(domain, name) → 实现（google_calendar/google_tasks）
├── image_gc.py          ← 陈旧来图清理：删 N 年前活动/待办的 source_image（节流，传输层调）
└── cli.py               ← cal-add / cal-list / cal-done / cal-delete / cal-sync / cal-status
```

活动/待办可带 `source_image`（原始来图，data 相对路径）：从图片建活动/待办时，传输层把来图
搬进 `data/<成员>/{schedule,tasks}/YYYY-MM/` 并记录。`image_gc.image_gc_tick()` 在成员消息到达时
节流（`calendar.image_prune_interval_days`，默认 30 天）扫一次，清掉 `calendar.image_retention_years`
（默认 2 年）前的来图文件并清空链接（保留行；图片非远端字段，不触发同步）。

存储分库：活动 `data/<成员>/schedule/schedule.db`，待办 `data/<成员>/tasks/tasks.db`
（路径经 `Agent_Runtime/paths.member_store`）。同步状态 `.sync_state.json` 与库同目录，不入备份。
单库迁移见 `Agent_Runtime/migrate_storage.py`（一次性，保留 uid/synced 避免远端重复）。

## 工作方式

- **静默刷新**：传输层在**已注册成员**的消息到达后调 `calendar_sync.calendar_tick()`：
  启用 + 距上次刷新 ≥ `refresh_minutes` + provider 就绪 → 拉取未来 `sync_horizon_days`
  天的活动 + 全部待办进本地缓存。未注册来源永远不会触发。不主动播报，
  Agent 仅在用户问到时按上下文/工具回答。
  注意：拉取窗口是 `sync_horizon_days`（默认 90，远期事件可见），**不是** `lookahead_days`
  （后者仅控制上下文注入与 cal-list 默认窗口）。两者分离，避免远期事件被漏拉。
- **查询前拉取**：用户问日程（cal-list）时，先调 `calendar_sync.sync_for_query(member)`
  按 `query_refresh_seconds` 短节流主动拉远端，再读本地。捕获 Google 端新加但后台
  tick 尚未拉到的事件（如 Gmail 自动建日程），避免回答陈旧。本地模式成员跳过。
- **先推后拉**：本地新增（cal-add）→ 立即尽力推送远端；失败/未配置则标记"待同步"，
  下一轮 tick 自动重试。完成/取消同理（同步完成或删除远端项）。
- **合并规则**：按远端 uid 合并，远端字段覆盖本地（remote wins）；本地有待推送
  改动的行跳过，推完再合并。窗口内活动在远端消失 → 本地标记取消；
  待办远端完成 → 本地完成。
- 日程存各成员 `data/<成员>/{schedule,tasks}/*.db` → 在 `data` 下随云备份镜像；`.sync_state.json` 不入备份。
- 成员私有：每个成员只见/改自己的日程与待办；`member` 字段记录归属（活动注入上下文也只取当前成员）。

## CLI

| 命令 | 行为 | Agent 可调 |
|------|------|-----------|
| `cal-add --member M --kind event\|task --title T [--date D] [--start HH:MM] [--end HH:MM] [--all-day] [--location L] [--notes N] [--source-image PATH]` | 新增（活动必须有日期；待办 --date=截止日可省；--source-image 关联原始来图） | ✅ |
| `cal-list [--days N] [--kind K] [--member M] [--all]` | 未来 N 天日程 + 开放待办 | ✅ |
| `cal-done --id N` | 完成待办 | ✅ |
| `cal-delete --id N` | 取消日程（同步删除远端） | ✅ |
| `cal-sync` | 立即强制刷新（忽略节流） | ✅ |
| `cal-status` | 同步状态（启用/配置/上次刷新/待同步/错误） | ✅ |

```bash
python .codewhale/skills/Calendar_Keeper/cli.py cal-add --member 爸爸 --kind event \
    --title "游泳课" --date 2026-06-20 --start 14:00 --end 15:00 --location 泳馆
python .codewhale/skills/Calendar_Keeper/cli.py cal-list
```

## 用户开启远程同步（当前 provider = Google Calendar + Google Tasks）

1. Google Cloud Console：启用 **Google Calendar API** 和 **Google Tasks API** →
   OAuth 客户端（**Desktop app**；可直接复用 Remote_Backup 的同一个客户端）。
2. `setx GCAL_CLIENT_ID "..."`、`setx GCAL_CLIENT_SECRET "..."`（新终端生效）。
3. 一次性授权：`python .codewhale/skills/Calendar_Keeper/calendar_provider.py --auth`
   → 浏览器批准 → 按提示 `setx GCAL_REFRESH_TOKEN "..."`。
   （与备份的 refresh token 互不影响，scope 不同需各自授权。）
4. （可选）非主日历：`setx GCAL_CALENDAR_ID "..."`（日历 id 形如邮箱，属隐私
   → 环境变量，不进 config.json）。
5. 在 `data/members.json` 给该成员加 `sync` 块（成员私有文件，凭据不入此处）：
   `"sync": {"schedule": {"provider":"google_calendar","enabled":true}, "tasks": {"provider":"google_tasks","enabled":true}}`。
   无 `sync` 块 = 本地模式（不推不拉）。
6. `config.json` 设 `calendar.enabled: true`（总开关），重启机器人。
   `cal-sync --member "<名>"` 首次全量刷新，`cal-status --member "<名>"` 确认。

> 多个成员都用 Google：`GCAL_*` 当前是单账号（Alex，复用 Remote_Backup 客户端）。第二个 Google
> 成员需扩展 provider 读命名空间环境变量（按成员区分凭据）—— 结构已就绪，凭据命名空间待实现。

权限范围（最小化）：`calendar.events`（只能读写日历上的活动，不能管理日历本身）
+ `tasks`（@default 待办清单）。

**换日历服务**：按 `calendar_provider.py` 文件头契约（8 个函数）重写该文件即可，
引擎零改动。**提交 provider 前自查**：代码里不得出现任何字面 token/key，
凭据只能 `os.environ` 读取。

## 配置（config.json `calendar` 段）

`enabled`（默认 false）/ `lookahead_days`（10，静默刷新与上下文注入的窗口）/
`refresh_minutes`（15，后台 tick 节流间隔）/
`query_refresh_seconds`（60，查询路径同步节流）/
`sync_horizon_days`（90，远端拉取窗口，独立于 lookahead_days）/ `image_retention_years`（2，来图保留年限）/
`image_prune_interval_days`（30，来图清理间隔）。改后重启进程生效。

## 边界

- ❌ 编辑日程（取消 + 重建即可）
- ❌ 邀请/参与人、单成员内多日历（按成员私有日历已支持）
- ❌ 主动提醒推送（Document_Keeper 负责主动提醒；日程只答不播）
- ❌ webhook 推送通道（只在成员消息到达时节流拉取）
