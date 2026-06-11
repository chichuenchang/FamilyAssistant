# Family Assistant

> 个人/家庭多功能助手。技能拆分为独立 skill，Agent 按需加载，不在会话启动时全量加载。

## 可用技能

| Skill | 说明 | 路径 | 触发条件 |
|-------|------|------|---------|
| **Expense Tracker** | 记账、查账、汇总、存款、报税、汇率 | [SKILL.md](.codewhale/skills/Expense_Tracker/SKILL.md) | 记账、查账、汇总、存款、报税、汇率、票据 |
| **OCR** | 图片文字识别、票据结构化提取 | [SKILL.md](.codewhale/skills/OCR/SKILL.md) | 图片文字识别、票据结构化提取 |
| **Agent Runtime** | 频道无关 Agent 大脑 + 远程频道传输层（微信、Telegram） | [SKILL.md](.codewhale/skills/Agent_Runtime/SKILL.md) | 远程频道、微信、Telegram、Bot 接入、Agent 核心、新增频道 |

## 加载策略

- 用户意图涉及记账/查账/财务 → 加载 Expense Tracker（如需票据识别，同时加载 OCR）
- 仅闲聊/问候 → 不加载

Agent 使用 `load_skill` 工具按需获取 SKILL.md 内容。

## 快速开始

```bash
# 记账
python .codewhale/skills/Expense_Tracker/cli.py add --type expense --amount 45.50 --currency CNY --date 2026-05-31 --category 餐饮 --desc "午餐"

# 查账
python .codewhale/skills/Expense_Tracker/cli.py list --start 2026-05-01 --end 2026-05-31

# 启动微信 Bot
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run
```

## 配置原则（单一事实来源）

凡是 `config.json` 里有的值，代码与 SKILL.md **一律读取它，不重复硬编码**。当前 config.json 驱动：

| config 键 | 谁读取 |
|-----------|--------|
| `base_currency` / `supported_currencies` / `categories` | `Expense_Tracker/models.py`（读一次→常量），`db`/`cli` 取用并校验 |
| `db_path` | `models.DB_PATH` |
| `receipts_dir` | `agent_core.RECEIPTS_DIR` |
| `members` | `Agent_Runtime/members.py`（只读：resolve）、`cli.py member-*`（本机写入） |
| `wechat.allowed_commands` | `agent_core.ALLOWED_COMMANDS` |

改这些值只改 `config.json`（改后重启进程生效）。config 缺失/损坏时各处有应急回退默认值。

## 项目关键文件

- `.codewhale/skills/Expense_Tracker/` — 记账 skill（cli.py 入口 + db.py 数据层 + models.py 读 config）
- `config.json` — 全局配置（分类/币种/路径/命令白名单的单一事实来源）
- `.codewhale/skills/OCR/ocr.py` — OCR 文字识别模块（腾讯云，1000次/月免费）
- `.codewhale/skills/Agent_Runtime/` — 远程频道接入（Agent 核心 + 微信 + Telegram 传输层），详见其 SKILL.md
