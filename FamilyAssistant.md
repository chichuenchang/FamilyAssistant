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

## 项目关键文件

- `.codewhale/skills/Expense_Tracker/` — 记账 skill（cli.py 入口 + db.py 数据层 + models.py）
- `config.json` — 分类 & 白名单配置
- `.codewhale/skills/OCR/ocr.py` — OCR 文字识别模块（腾讯云，1000次/月免费）
- `.codewhale/skills/Agent_Runtime/` — 远程频道接入（Agent 核心 + 微信 + Telegram 传输层），详见其 SKILL.md
