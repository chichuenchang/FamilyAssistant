# Family Assistant

> 个人/家庭多功能 AI 助手。电脑上跑 Agent，手机上用微信远程操控。

## 快速开始

### 电脑端

```bash
# 1. 安装依赖
pip install "weixin-ilink[qr]"

# 2. 设 LLM API key（必须）
setx DEEPSEEK_API_KEY "sk-xxx"

# 3. 启动 Agent（终端出二维码）
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run

# （可选）设 OCR：
#   setx TENCENT_SECRET_ID "xxx"
#   setx TENCENT_SECRET_KEY "xxx"

# ── 或用 Telegram（多人，推荐） ──
#   setx TELEGRAM_BOT_TOKEN "xxx"
#   python .codewhale/skills/Agent_Runtime/telegram_bot.py
```

### 手机端

**方式一：微信**（个人使用）
1. 微信 → 搜 **ClawBot** → 开通插件（官方灰度中）
2. 扫电脑终端上的二维码 → 授权
3. 在微信里给 Bot 发消息

**方式二：Telegram**（多人使用，推荐）
1. Telegram 搜 **@BotFather** → `/newbot` → 获取 Token
2. `setx TELEGRAM_BOT_TOKEN "xxx"`
3. `python .codewhale/skills/Agent_Runtime/telegram_bot.py`
4. 把 Bot 链接发给家人，拉进群一起用

发什么都可以，比如 `花了45块 午餐` 或 `这个月花了多少`

## 目录结构

```
FamilyAssistant/
├── .codewhale/
│   └── skills/
│       ├── Expense_Tracker/  ← 记账技能
│       ├── OCR/              ← OCR 技能
│       │   ├── SKILL.md
│       │   └── ocr.py            ← OCR（腾讯云）
│       └── Agent_Runtime/    ← Agent 大脑 + 远程频道传输层
│           ├── SKILL.md
│           ├── agent_core.py     ← 频道无关 Agent 核心（全量上下文）
│           ├── wechat_ilink.py   ← 微信传输层
│           └── telegram_bot.py   ← Telegram 传输层
├── scripts/
│   ├── cli.py            ← 记账 CLI
│   ├── db.py             ← 数据库层
│   └── feishu_inbox.py   ← 飞书收件箱
├── config.json           ← 分类 & 白名单
├── data/                 ← SQLite + 凭据
└── receipts/             ← 票据存档
```

## 技术栈

Python 3.10+ · SQLite · DeepSeek V4 Flash · [weixin-ilink](https://pypi.org/project/weixin-ilink/)
