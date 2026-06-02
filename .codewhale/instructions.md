# Family Assistant

> 个人/家庭多功能助手。按需加载 skill，不在会话启动时全量加载。

## 可用技能

| Skill | 路径 | 触发条件 |
|-------|------|---------|
| **Expense Tracker** | `.codewhale/skills/Expense_Tracker/SKILL.md` | 记账、查账、汇总、存款、报税、汇率、票据 |
| **OCR** | `.codewhale/skills/OCR/SKILL.md` | 图片文字识别、票据结构化提取 |

## 加载策略

- 用户意图涉及记账/查账/财务 → 加载 Expense Tracker（如需票据识别，同时加载 OCR）
- 仅闲聊/问候 → 不加载

Agent 使用 `load_skill` 工具按需获取 SKILL.md 内容。

## 项目关键文件

- `scripts/cli.py` — CLI 入口
- `scripts/db.py` — 数据库层
- `config.json` — 分类 & 白名单配置
- `scripts/ocr.py` — OCR 文字识别模块（腾讯云，1000次/月免费）
- `scripts/wechat_ilink.py` — 微信传输层（基础设施，无需 Agent 介入）
- `scripts/wechat_skill.py` — 微信消息引擎（基础设施，无需 Agent 介入）
