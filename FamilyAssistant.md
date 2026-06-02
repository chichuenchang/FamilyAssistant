# Family Assistant

> 个人/家庭多功能助手。技能已拆分为独立 skill，Agent 按需加载。

## Skill

| Skill | 说明 | 路径 |
|-------|------|------|
| Expense Tracker | 记账、查账、汇总、存款、报税、汇率 | [SKILL.md](.codewhale/skills/Expense_Tracker/SKILL.md) |

## 快速开始

```bash
# 记账
python scripts/cli.py add --type expense --amount 45.50 --currency CNY --date 2026-05-31 --category 餐饮 --desc "午餐"

# 查账
python scripts/cli.py list --start 2026-05-01 --end 2026-05-31

# 启动微信 Bot
python scripts/wechat_ilink.py --mode run
```
