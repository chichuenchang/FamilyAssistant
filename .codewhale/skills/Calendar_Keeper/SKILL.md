# Calendar Keeper

> 家庭日程（活动/安排）与待办（任务），与远程日历静默双向同步。
> 远程日历是日程数据的事实源（家人会直接在手机上改日历）；本地是缓存 + 离线缓冲。
> provider 未配置时一切照常本地工作，配好后自动补推。

## 代码位置

```
.codewhale/skills/Calendar_Keeper/
├── SKILL.md             ← 本文件
├── cal_db.py            ← 存储层（schedule_items 表，建在 data/ledger.db）
├── calendar_sync.py     ← 同步引擎：静默节流刷新、先推后拉、对账、状态
├── calendar_provider.py ← Google Calendar + Tasks 实现（契约见文件头注释，可换）
└── cli.py               ← cal-add / cal-list / cal-done / cal-delete / cal-sync / cal-status
```

## 工作方式

- **静默刷新**：传输层在**已注册成员**的消息到达后调 `calendar_sync.calendar_tick()`：
  启用 + 距上次刷新 ≥ `refresh_minutes` + provider 就绪 → 拉取未来 `lookahead_days`
  天的活动 + 全部待办进本地缓存。未注册来源永远不会触发。不主动播报，
  Agent 仅在用户问到时按上下文/工具回答。
- **先推后拉**：本地新增（cal-add）→ 立即尽力推送远端；失败/未配置则标记"待同步"，
  下一轮 tick 自动重试。完成/取消同理（同步完成或删除远端项）。
- **合并规则**：按远端 uid 合并，远端字段覆盖本地（remote wins）；本地有待推送
  改动的行跳过，推完再合并。窗口内活动在远端消失 → 本地标记取消；
  待办远端完成 → 本地完成。
- 日程数据存共享 `data/ledger.db` → 已在 `backup.include` 内，随云备份镜像。
- 家庭共享：所有成员可见可改；`member` 字段只记录创建者归属。

## CLI

| 命令 | 行为 | Agent 可调 |
|------|------|-----------|
| `cal-add --member M --kind event\|task --title T [--date D] [--start HH:MM] [--end HH:MM] [--all-day] [--location L] [--notes N]` | 新增（活动必须有日期；待办 --date=截止日可省） | ✅ |
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
5. `config.json` 设 `calendar.enabled: true`，重启机器人。
6. `cal-sync` 首次全量刷新，`cal-status` 确认。

权限范围（最小化）：`calendar.events`（只能读写日历上的活动，不能管理日历本身）
+ `tasks`（@default 待办清单）。

**换日历服务**：按 `calendar_provider.py` 文件头契约（8 个函数）重写该文件即可，
引擎零改动。**提交 provider 前自查**：代码里不得出现任何字面 token/key，
凭据只能 `os.environ` 读取。

## 配置（config.json `calendar` 段）

`enabled`（默认 false）/ `lookahead_days`（10，静默刷新与上下文注入的窗口）/
`refresh_minutes`（15，节流间隔）。改后重启进程生效。

## 边界

- ❌ 编辑日程（取消 + 重建即可）
- ❌ 邀请/参与人、多日历、按成员私有日历
- ❌ 主动提醒推送（Document_Keeper 负责主动提醒；日程只答不播）
- ❌ webhook 推送通道（只在成员消息到达时节流拉取）
