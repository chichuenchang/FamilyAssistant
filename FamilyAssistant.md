# Family Assistant

> 个人/家庭多功能助手。技能拆分为独立 skill，Agent 按需加载，不在会话启动时全量加载。

## 可用技能

| Skill | 说明 | 路径 | 触发条件 |
|-------|------|------|---------|
| **Expense Tracker** | 记账、查账、汇总、存款、报税、汇率 | [SKILL.md](.codewhale/skills/Expense_Tracker/SKILL.md) | 记账、查账、汇总、存款、报税、汇率、票据 |
| **OCR** | 图片文字识别、票据结构化提取 | [SKILL.md](.codewhale/skills/OCR/SKILL.md) | 图片文字识别、票据结构化提取 |
| **Document Keeper** | 家庭文档归档、OCR 索引、到期跟踪与每日提醒 | [SKILL.md](.codewhale/skills/Document_Keeper/SKILL.md) | 文档、合同、租约、保险单、证件、到期、提醒 |
| **Remote Backup** | 用户数据云盘镜像（可选；作者已实现 Google Drive provider，用户可按契约换成自己想要的云端存储） | [SKILL.md](.codewhale/skills/Remote_Backup/SKILL.md) | 备份、同步、云盘、恢复数据 |
| **Agent Runtime** | 频道无关 Agent 大脑 + 远程频道传输层（微信、Telegram） | [SKILL.md](.codewhale/skills/Agent_Runtime/SKILL.md) | 远程频道、微信、Telegram、Bot 接入、Agent 核心、新增频道 |

## 加载策略

- 用户意图涉及记账/查账/财务 → 加载 Expense Tracker（如需票据识别，同时加载 OCR）
- 用户意图涉及文档归档/合同/保险/证件/到期提醒 → 加载 Document Keeper（如需图片识别，同时加载 OCR）
- 用户意图涉及备份/恢复/云盘同步 → 加载 Remote Backup
- 仅闲聊/问候 → 不加载

Agent 使用 `load_skill` 工具按需获取 SKILL.md 内容。

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

# 启动微信 Bot
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run
```

## 成员注册（仅本机）

- `member-add <名> --telegram <chat_id> --wechat <wxid>`（可多次传同一 flag 绑多个 id）、`member-list`、`member-remove <名>`。
- 注册表存 `config.json` `members` 段；改后重启机器人生效。空注册表 = 锁定，所有远程消息静默丢弃。
- 这三个命令**不在** `wechat.allowed_commands` 白名单内 —— Agent 只读注册表（`members.resolve`），无法增删成员。
- 账目归属由代码注入解析出的成员名（LLM 给的 member 一律剥离，防冒名）。

## 配置原则（单一事实来源）

凡是 `config.json` 里有的值，代码与 SKILL.md **一律读取它，不重复硬编码**。当前 config.json 驱动：

| config 键 | 谁读取 |
|-----------|--------|
| `base_currency` / `supported_currencies` / `categories` | `Expense_Tracker/models.py`（读一次→常量），`db`/`cli` 取用并校验；`agent_core` 独立读取同一来源（工具 enum） |
| `db_path` | `models.DB_PATH` |
| `receipts_dir` | `agent_core.RECEIPTS_DIR`；`Expense_Tracker/models.py`（RECEIPTS_DIR，cli `--receipt` 归档用） |
| `documents_dir` | `Document_Keeper/doc_models.py`（DOCUMENTS_DIR）、`agent_core.DOCUMENTS_DIR` |
| `doc_types` | `Document_Keeper/doc_models.py`（读一次→常量）、`agent_core`（工具 enum） |
| `reminder_lead_days` | `Document_Keeper/doc_models.py`（读一次→常量） |
| `backup`（enabled/debounce/include/remote_root） | `Remote_Backup/backup_sync.py`（CFG，读一次） |
| `members` | `Agent_Runtime/members.py`（只读：resolve）、`cli.py member-*`（本机写入） |
| `wechat.allowed_commands` | `agent_core.ALLOWED_COMMANDS` |

改这些值只改 `config.json`（改后重启进程生效）。config 缺失/损坏时各处有应急回退默认值。

## 项目关键文件

- `.codewhale/skills/Expense_Tracker/` — 记账 skill（cli.py 入口 + db.py 数据层 + models.py 读 config）
- `config.json` — 全局配置（分类/币种/路径/命令白名单的单一事实来源）
- `.codewhale/skills/OCR/ocr.py` — OCR 文字识别模块（腾讯云，1000次/月免费）
- `.codewhale/skills/Document_Keeper/` — 文档管理 skill（cli.py 入口 + doc_db.py 数据层 + reminder.py 每日提醒）
- `.codewhale/skills/Remote_Backup/` — 用户数据云盘镜像 skill（backup_provider.py 当前为 Google Drive 实现；按其文件头契约重写即可换成其他云盘）
- `.codewhale/skills/Agent_Runtime/` — 远程频道接入（Agent 核心 + 微信 + Telegram 传输层），详见其 SKILL.md
