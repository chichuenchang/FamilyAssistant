# Family Assistant

> 个人/家庭多功能助手。技能拆分为独立 skill；本文档与各 SKILL.md 面向开发者，
> 运行时 Agent 不读取它们（见下"运行时提示词"）。

## 可用技能

| Skill | 说明 | 路径 | 触发条件 |
|-------|------|------|---------|
| **Expense Tracker** | 记账、查账、汇总、存款、报税、汇率 | [SKILL.md](.codewhale/skills/Expense_Tracker/SKILL.md) | 记账、查账、汇总、存款、报税、汇率、票据 |
| **OCR** | 图片文字识别、票据结构化提取 | [SKILL.md](.codewhale/skills/OCR/SKILL.md) | 图片文字识别、票据结构化提取 |
| **Document Keeper** | 家庭文档归档、OCR 索引、到期跟踪与每日提醒 | [SKILL.md](.codewhale/skills/Document_Keeper/SKILL.md) | 文档、合同、租约、保险单、证件、到期、提醒 |
| **Note Keeper** | 个人备忘（杂项信息长期记忆，按成员私有，支持图片 OCR 入忘、置顶常驻上下文） | [SKILL.md](.codewhale/skills/Note_Keeper/SKILL.md) | 记一下、帮我记住、备忘、我记过什么 |
| **Calendar Keeper** | 按成员私有的日程与待办（活动/待办分库），与各成员自己的远程日历静默同步（作者已实现 Google Calendar + Tasks provider，按成员/域选择，用户可按契约换其他日历服务） | [SKILL.md](.codewhale/skills/Calendar_Keeper/SKILL.md) | 日程、安排、活动、待办、任务、日历 |
| **Remote Backup** | 用户数据云盘镜像（可选；作者已实现 Google Drive provider，用户可按契约换成自己想要的云端存储） | [SKILL.md](.codewhale/skills/Remote_Backup/SKILL.md) | 备份、同步、云盘、恢复数据 |
| **Web Reach** | 只读联网：搜最新资讯、抓取/总结网页、转写 YouTube 字幕（无需 key；YouTube 需 yt-dlp，缺失优雅降级） | [SKILL.md](.codewhale/skills/Web_Reach/SKILL.md) | 最新新闻、查一下、外面在发生什么、总结链接、YouTube、视频 |
| **Agent Runtime** | 频道无关 Agent 大脑 + 远程频道传输层（微信、Telegram） | [SKILL.md](.codewhale/skills/Agent_Runtime/SKILL.md) | 远程频道、微信、Telegram、Bot 接入、Agent 核心、新增频道 |

## 运行时提示词

运行时 Agent（`Agent_Runtime/agent_core.py`）在启动时把所有技能领域的行为准则
一次性组装进 system prompt（`_build_system_prompt()`），分类/币种/文档类型等
合法值从 `config.json` 提取为紧凑列表。SKILL.md 与本文档是开发文档，
不进 prompt，对运行时对话无影响。工具定义走 API 的 tools 参数（function calling）。

## 快速开始

```bash
# 登记家庭成员（必须先做 — 未登记的频道来源一律静默忽略）
python .codewhale/skills/Expense_Tracker/cli.py member-add 爸爸 --telegram 123456789 --wechat wxid_xxx
python .codewhale/skills/Expense_Tracker/cli.py member-list

# 记账（--member 可选，远程频道会自动归属发消息的成员）
python .codewhale/skills/Expense_Tracker/cli.py add --type expense --amount 45.50 --currency CNY --date 2026-05-31 --category 餐饮 --desc "午餐" --member 爸爸

# 查账
python .codewhale/skills/Expense_Tracker/cli.py list --start 2026-05-01 --end 2026-05-31

# 归档文档 / 查到期
python .codewhale/skills/Document_Keeper/cli.py doc-add --type lease --title "2026公寓租约" --expiry 2027-02-28
python .codewhale/skills/Document_Keeper/cli.py doc-due

# 启动微信 Bot（默认写调试日志 data/bot_debug.log，--no-debug 关闭）
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run
```

## 成员注册（仅本机）

- `member-add <名> --telegram <chat_id> --wechat <wxid>`（可多次传同一 flag 绑多个 id）、`member-list`、`member-remove <名>`。
- `--alias <别名/法定名>`（可多次）：登记文档票据里出现的名字（如法定中文名）。Agent 的 system prompt
  会带上成员+别名清单，识别"这份保单是谁的"；别名不参与频道闸门，仅别名登记也可（如还没手机的孩子）。
- 注册表存 `data/members.json`（**git 不跟踪** — 姓名/法定名/频道 id 属隐私，不入仓库；由持有它的
  成员 backup scope（`members.json` 各成员 backup 块的 `scopes`）随云备份镜像，新设备 `backup-restore` 一并恢复）；改后重启机器人生效。
  文件缺失/空注册表 = 锁定，所有远程消息静默丢弃。
- 这三个命令**不在** `wechat.allowed_commands` 白名单内 —— Agent 只读注册表（`members.resolve`），无法增删成员。
- 账目归属由代码注入解析出的成员名（LLM 给的 member 一律剥离，防冒名）。

## 配置原则（单一事实来源）

凡是 `config.json` 里有的值，代码与 SKILL.md **一律读取它，不重复硬编码**。当前 config.json 驱动：

| config 键 | 谁读取 |
|-----------|--------|
| `base_currency` / `supported_currencies` / `categories` | `Expense_Tracker/models.py`（读一次→常量），`db`/`cli` 取用并校验；`agent_core` 独立读取同一来源（工具 enum） |
| `data_root` / `family_dir_name` | `Agent_Runtime/paths.py` — 磁盘布局的单一事实来源（family_ledger / family_receipts_dir / family_documents_dir / member_store / member_domain_image_dir / to_rel）。各 skill 的 DB/票据/文档/备忘路径全经此解析 |
| `doc_types` | `Document_Keeper/doc_models.py`（读一次→常量）、`agent_core`（工具 enum） |
| `reminder_lead_days` | `Document_Keeper/doc_models.py`（读一次→常量） |
| `backup`（enabled/debounce_seconds） | `Remote_Backup/backup_sync.py`（CFG，读一次）。每成员 provider/cred_prefix/remote_root/scopes 在 `data/members.json` 的 backup 块 |
| `calendar`（enabled/lookahead_days/refresh_minutes/image_retention_years/image_prune_interval_days） | `Calendar_Keeper/calendar_sync.py`（CFG）；`image_gc.py`（来图清理参数）；`agent_core`（_CAL_LOOKAHEAD，上下文注入窗口）；`cli.py`（默认窗口）。按成员/域的远程同步偏好在 `data/members.json` 的 sync 块（不在 config.json） |
| ~~`members`~~（已迁出 → `data/members.json`，git 不跟踪） | `Agent_Runtime/members.py`（resolve / member-* 读写均走该文件） |
| `wechat.allowed_commands` | `agent_core.ALLOWED_COMMANDS` |

改这些值只改 `config.json`（改后重启进程生效）。config 缺失/损坏时各处有应急回退默认值。

## 项目关键文件

- `.codewhale/skills/Expense_Tracker/` — 记账 skill（cli.py 入口 + db.py 数据层 + models.py 读 config）
- `config.json` — 全局配置（分类/币种/路径/命令白名单的单一事实来源）
- `.codewhale/skills/OCR/ocr.py` — OCR 文字识别模块（腾讯云，1000次/月免费）
- `.codewhale/skills/Document_Keeper/` — 文档管理 skill（cli.py 入口 + doc_db.py 数据层 + reminder.py 每日提醒）
- `.codewhale/skills/Note_Keeper/` — 个人备忘 skill（cli.py 入口 + note_db.py 数据层；按成员私有）
- `.codewhale/skills/Remote_Backup/` — 用户数据云盘镜像 skill（backup_provider.py 当前为 Google Drive 实现；按其文件头契约重写即可换成其他云盘）
- `.codewhale/skills/Calendar_Keeper/` — 按成员私有的日程/待办 + 远程日历同步 skill（活动/待办分库；按成员/域选 provider，providers.py 注册表，calendar_provider.py 为 Google Calendar + Tasks 实现；image_gc.py 清理陈旧来图）
- `.codewhale/skills/Agent_Runtime/` — 远程频道接入（Agent 核心 + 微信 + Telegram 传输层），详见其 SKILL.md
