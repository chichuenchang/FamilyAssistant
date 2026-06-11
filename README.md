# Family Assistant

> 个人/家庭多功能 AI 助手。电脑上跑 Agent，手机上用微信远程操控。

## 快速开始

### 电脑端

```bash
# 1. 安装依赖
pip install "weixin-ilink[qr]"

# 2. 设 LLM API key（必须）
setx DEEPSEEK_API_KEY "sk-xxx"

# 3. 登记家庭成员（必须 — 未登记的来源一律静默忽略）
python .codewhale/skills/Expense_Tracker/cli.py member-add 爸爸 --telegram 123456789 --wechat wxid_xxx
python .codewhale/skills/Expense_Tracker/cli.py member-list

# 4. 启动 Agent（终端出二维码）
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
4. 把 Bot 链接发给家人，并在电脑上用 `member-add` 登记每个人的 chat id（未登记的人 Bot 不会回应）

发什么都可以，比如 `花了45块 午餐`、`这个月花了多少`，或发一张租约照片说 `存一下这份租约`。
每笔账自动归到发消息的成员名下；`summary --by-member` 可看谁花了多少。
归档的文档到期前 Bot 会每天主动提醒（如 "租约 20 天后到期 — 提前60天通知房东"）。
（可选）配置云盘备份后，所有数据自动镜像到你自己的网盘；换电脑 `backup-restore` 一键恢复。

## 目录结构

```
FamilyAssistant/
├── .codewhale/
│   └── skills/
│       ├── Expense_Tracker/  ← 记账技能
│       │   ├── SKILL.md
│       │   ├── models.py        ← 数据模型
│       │   ├── db.py            ← SQLite 数据层
│       │   └── cli.py           ← 记账 CLI 入口
│       ├── OCR/              ← OCR 技能
│       │   ├── SKILL.md
│       │   └── ocr.py            ← OCR（腾讯云）
│       ├── Document_Keeper/  ← 家庭文档管理技能
│       │   ├── SKILL.md
│       │   ├── doc_models.py     ← 数据模型
│       │   ├── doc_db.py         ← SQLite 数据层
│       │   ├── cli.py            ← 文档 CLI 入口
│       │   └── reminder.py       ← 每日到期提醒
│       ├── Remote_Backup/    ← 用户数据云盘镜像（可选）
│       │   ├── SKILL.md
│       │   ├── backup_sync.py    ← 同步引擎
│       │   ├── backup_provider.py← 云盘占位（用户自己实现）
│       │   └── cli.py            ← 备份 CLI 入口
│       └── Agent_Runtime/    ← Agent 大脑 + 远程频道传输层
│           ├── SKILL.md
│           ├── agent_core.py     ← 频道无关 Agent 核心（全量上下文）
│           ├── members.py        ← 成员注册表（频道 id → 成员名）
│           ├── wechat_ilink.py   ← 微信传输层
│           └── telegram_bot.py   ← Telegram 传输层
├── config.json           ← 分类 & 白名单 & 成员注册表
├── data/                 ← SQLite + 凭据
├── receipts/             ← 票据存档
└── documents/            ← 家庭文档存档（按类型子目录）
```

## 技术栈

Python 3.10+ · SQLite · DeepSeek V4 Flash · [weixin-ilink](https://pypi.org/project/weixin-ilink/)
