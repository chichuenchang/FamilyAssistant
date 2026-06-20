# Family Assistant

> 个人/家庭多功能 AI 助手。电脑上跑 Agent，手机上用微信远程操控。

## 功能特性

### 📒 记账（Expense Tracker）
- **多币种流水**：日常开销 / 收入 / 投资 / 活期，CNY · USD · CAD，每笔保留原币种，汇总按币种分组不混算
- **分类可自定义**：开支 / 收入（及投资 / 储蓄）分类都在 `config.json` 的 `categories` 段，**鼓励按自家需要增删改**（如把"每月孩子"换成自己的开销类目）；改后重启进程生效
- **定期存款追踪**：本金、利率、期限、到期日，`deposit-list --active` 查在存
- **资金划转 / 换汇溯源**：记录"源账户 → 换汇 → 目标账户"全链路，多年后可回查任意一笔存款的资金来源（`transfer-list --trace`）；转入定期时自动建定期存款记录并链接
- **报税记录存档**：按年度/国家（US/CA）存申报记录与税表文件
- **手工汇率表**：`fx-set` / `fx-get`，按基准币种折算
- **重复检测**：同日同金额同币种且描述相近自动拦截

### 🧾 票据 OCR
- 发票/小票照片或 PDF 账单 → 腾讯云 OCR 文字识别（1000 次/月免费，PDF 逐页各计一次，上限 20 页）→ DeepSeek 结构化提取（金额/日期/分类/描述）→ 自动记账
- 原始票据按月存档 `data/Family/receipts/YYYY-MM/`，与账目记录关联（行内 `receipt_path` 记 data 相对路径）

### 📁 家庭文档管理（Document Keeper）
- 重要文档归档：租约、保险单、证件、健康卡等，原件存 `data/Family/documents/<类型>/`
- **在微信/Telegram 发一份 PDF（合同/保单/证件/移民表格等），Bot 自动 OCR 全文提取并归档**，OCR 识别不了的 PDF 会提示配置腾讯云密钥
- OCR 全文索引，关键词检索（"我们有哪些保险"）
- **到期跟踪 + 每日主动提醒**：到期前 Bot 每天推送（如 "租约 20 天后到期 — 提前60天通知房东"），`doc-ack` 确认后不再重复
- 重复检测（证件编号 / 文件哈希）

### 🗒️ 个人备忘（Note Keeper）
- "帮我记住车位是B2-118"、发一张路由器标签/课表/名片照片 → OCR 提取后存为备忘
- **按成员私有**：只能看到自己的备忘（系统强制，LLM 无法跨成员读写）
- 置顶备忘每次对话自动带上（如 wifi 密码）；"我记过什么""XX是什么来着"随口即查

### 👨‍👩‍👧 家庭成员
- 成员注册表（仅本机管理，Agent 无权增删）；每笔账自动归到发消息的成员名下
- 注册表存 **git 忽略的 `data/members.json`**（姓名/法定名/频道 id 属隐私，永不入仓库），随云备份镜像，新设备自动恢复
- `--alias` 登记法定名/别名 → Bot 能认出文档里"这是谁的"（如保单上的法定中文名）
- `summary --by-member` 按成员汇总；`--member` 过滤查询
- **默认锁定**：未登记的频道来源一律静默忽略；账目归属由代码注入，LLM 无法冒名

### 💬 远程频道（微信 / Telegram）
- 手机发自然语言即可操作："花了45块 午餐"、"这个月花了多少"、发照片/PDF 说"存一下这份租约"
- 一个频道无关的 Agent 大脑（DeepSeek 函数调用），多个轻量传输层；新增频道只需实现一个薄适配文件
- 每用户独立对话上下文，`/clear` 远程清空

### 📅 家庭日程与待办（Calendar Keeper）
- 自然语言加日程/待办："周六下午2点孩子游泳课"、"提醒我15号前买生日蛋糕"
- **静默同步远程日历**：已注册成员消息到达时自动拉取未来 10 天日程（节流，默认 15 分钟一次），
  不主动播报；问"接下来有什么安排""待办清单"时即答
- Agent 新建/完成/取消的日程待办自动推送远端；离线/未配置时本地照常用，配好自动补推
- 当前内置 Google Calendar + Google Tasks 实现（最小权限 `calendar.events` + `tasks`）；
  按 provider 契约可换任意日历服务
- 家人在手机日历上的改动会同步回来：远端是日程的事实源（改名/删除/完成都对账）

### 🌐 联网资讯（Web Reach）
- "最新 AI 新闻是什么""外面在发生什么" → 联网搜索；发链接说"帮我看看这篇" → 抓取正文总结
- 发 YouTube 链接说"总结下这视频" → 取字幕转写后用中文总结
- **只读公开信息，无需 API key**（搜索走 DuckDuckGo + Jina 阅读器；YouTube 需 `yt-dlp`，缺失时优雅降级）

### ☁️ 云盘备份（Remote Backup，可选）
- 用户数据（账本/票据/文档/配置）单向镜像到云盘，写入后防抖增量同步，本地永远是事实源
- 当前内置 Google Drive 实现（最小 `drive.file` 权限，只能看到自己上传的文件）；按 provider 契约可换任意云端存储
- 换电脑 `backup-restore` 一键恢复全部数据

### 🔒 安全设计
- Agent 只能调 `config.json` 白名单内的 CLI 命令；成员/文档删除等敏感命令仅限本机
- OCR 路径限制在数据目录 `data/` 内，防文件外泄；所有凭据走环境变量或本地加密存储，永不入库
- **隐私分层**：git 跟踪的文件（代码 + `config.json`）不含任何个人数据；隐私数据（成员注册表/账本/票据/文档/备忘/日程）
  全部在 git 忽略路径 `data/`（家庭共享 `data/Family/` + 成员私有 `data/<成员>/`），只存本机 + 你自己的云盘镜像

## 快速开始

### 电脑端

```bash
# 1. 安装依赖
pip install "weixin-ilink[qr]"

# 2. 设 LLM API key（必须）
setx DEEPSEEK_API_KEY "sk-xxx"

# 3. 登记家庭成员（必须 — 未登记的来源一律静默忽略；--alias 登记文档里的法定名，Agent 据此识别"这是谁的"）
python .codewhale/skills/Expense_Tracker/cli.py member-add 爸爸 --telegram 123456789 --wechat wxid_xxx --alias 法定名
python .codewhale/skills/Expense_Tracker/cli.py member-list

# 4. 启动 Agent（终端出二维码；默认写调试日志 data/bot_debug.log，--no-debug 关闭）
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run

# （可选）设 OCR：
#   setx TENCENT_SECRET_ID "xxx"
#   setx TENCENT_SECRET_KEY "xxx"

# （可选）云盘备份（当前为 Google Drive 实现，可换其他云盘；步骤详见 Remote_Backup/SKILL.md）：
#   setx GDRIVE_CLIENT_ID "xxx"
#   setx GDRIVE_CLIENT_SECRET "xxx"
#   python .codewhale/skills/Remote_Backup/backup_provider.py --auth  # 一次性授权，按提示设 GDRIVE_REFRESH_TOKEN
#   config.json 设 backup.enabled: true

# （可选）远程日历同步（当前为 Google Calendar + Tasks 实现；步骤详见 Calendar_Keeper/SKILL.md）：
#   setx GCAL_CLIENT_ID "xxx"        # 可复用上面的同一个 OAuth 客户端
#   setx GCAL_CLIENT_SECRET "xxx"
#   python .codewhale/skills/Calendar_Keeper/calendar_provider.py --auth  # 一次性授权，按提示设 GCAL_REFRESH_TOKEN
#   config.json 设 calendar.enabled: true

# ── 或用 Telegram（多人，推荐） ──
#   setx TELEGRAM_BOT_TOKEN "xxx"
#   python .codewhale/skills/Agent_Runtime/telegram_bot.py   # 同样默认写调试日志，--no-debug 关闭
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

发什么都可以，比如 `花了45块 午餐`、`这个月花了多少`、`周六下午2点游泳课`、
`接下来有什么安排`，或发一张租约照片说 `存一下这份租约`。
每笔账自动归到发消息的成员名下；`summary --by-member` 可看谁花了多少。
归档的文档到期前 Bot 会每天主动提醒（如 "租约 20 天后到期 — 提前60天通知房东"）。
（可选）配置云盘备份后，所有数据自动镜像到你自己的网盘；换电脑 `backup-restore` 一键恢复。

## 换新机 / 灾难恢复

代码在 git，**家庭数据在你自己的云盘备份里**，凭据走环境变量（按设计不进备份、不进 git）。
换机就三步：克隆代码 → 恢复数据 → 重设凭据。

**恢复地图**

| 东西 | 在哪 | 怎么回来 |
|------|------|---------|
| 代码 | git 仓库 | `git clone` |
| 家庭数据（账本/票据/文档/备忘/日程/成员注册表） | 你的 Google Drive 备份 | `backup-restore` |
| `config.json` | 随 git 克隆（也在备份里） | 自带 |
| 凭据（GDRIVE_* / GCAL_* / 微信 / Telegram / OCR / DeepSeek） | **只在环境变量，不在备份** | 手动重设 / 重新授权 |

**步骤**

```bash
# 1. 装 Python 3.10+ 与依赖，克隆代码
pip install "weixin-ilink[qr]"
git clone <你的仓库地址> && cd FamilyAssistant

# 2. 设 Google Drive 凭据（恢复用）。CLIENT_ID/SECRET 来自 Google Cloud Console 的 OAuth 客户端
setx GDRIVE_CLIENT_ID "xxx"
setx GDRIVE_CLIENT_SECRET "xxx"
#    refresh token 若已保存：setx GDRIVE_REFRESH_TOKEN "xxx"
#    丢了也没关系——数据还在云端，重新授权一次即可（开新终端让 setx 生效后）：
python .codewhale/skills/Remote_Backup/backup_provider.py --auth   # 浏览器批准 → 按提示 setx GDRIVE_REFRESH_TOKEN

# 3. 引导恢复：先恢复"持有注册表的主成员"——即备份 scope 含 members.json/config.json 的那个成员
#    （members.json 在该成员的备份里，故首次用显式参数、无需本地注册表）。三处占位按实际替换：
#      <主成员> = 成员名   <前缀> = 该成员 cred_prefix（默认 GDRIVE）   <根目录> = 该成员 remote_root
python .codewhale/skills/Remote_Backup/cli.py backup-restore --member "<主成员>" --prefix <前缀> --remote-root <根目录>
#    → 拉回 config.json + data/members.json + 该成员的全部数据
#    其他成员若各有备份：再 backup-restore --member "成员名"（此时注册表已恢复，正常模式）

# 4. 重设其余凭据（都不在备份里）
setx DEEPSEEK_API_KEY "sk-xxx"        # 必须
setx GCAL_CLIENT_ID "xxx"             # 日历同步（可复用 Drive 的同一 OAuth 客户端）
setx GCAL_CLIENT_SECRET "xxx"
setx GCAL_CALENDAR_ID "xxx"
#    GCAL refresh token 同样可 calendar_provider.py --auth 重授
#    可选：setx TENCENT_SECRET_ID / TENCENT_SECRET_KEY（OCR）、setx TELEGRAM_BOT_TOKEN（Telegram）

# 5. 启动 Bot；微信通道重新扫码登录（wechat_creds.json 自动重建，不从备份恢复）
python .codewhale/skills/Agent_Runtime/wechat_ilink.py --mode run     # 或 telegram_bot.py

# 6. 确认备份续上（应报告全部一致、零重传）
python .codewhale/skills/Remote_Backup/cli.py backup-verify --member "<主成员>"
```

**唯一必须自己保管好的：Google 账号 + 那个 OAuth 客户端的 `CLIENT_ID` / `CLIENT_SECRET`。**
数据躺在该 Google 账号的云盘里；只要有 CLIENT_ID/SECRET，随时能 `--auth` 换一个新的 refresh token 再恢复。
把这两个值（连同各 refresh token）存进**密码管理器**——它们按设计不进备份、不进 git、不进日志，丢了没有第二份。
凭据不入备份是有意为之（见「安全设计」）：备份云盘一旦被盗，泄露的也只是数据、不含登录密钥。

> 提示：Google OAuth 同意屏幕发布状态设为 **In production**，否则 refresh token 约 7 天过期（过期了 `--auth` 重授即可）。

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
│       ├── Note_Keeper/      ← 个人备忘（按成员私有）
│       │   ├── SKILL.md
│       │   ├── note_db.py        ← SQLite 数据层
│       │   └── cli.py            ← 备忘 CLI 入口
│       ├── Remote_Backup/    ← 用户数据云盘镜像（可选）
│       │   ├── SKILL.md
│       │   ├── backup_sync.py    ← 同步引擎
│       │   ├── backup_provider.py← Google Drive 实现（可按契约换成其他云盘）
│       │   └── cli.py            ← 备份 CLI 入口
│       ├── Calendar_Keeper/  ← 家庭日程/待办 + 远程日历同步
│       │   ├── SKILL.md
│       │   ├── cal_db.py         ← 数据层（日程缓存）
│       │   ├── calendar_sync.py  ← 同步引擎（静默节流刷新/先推后拉/对账）
│       │   ├── calendar_provider.py ← Google Calendar+Tasks 实现（可按契约换）
│       │   └── cli.py            ← 日程 CLI 入口
│       ├── Web_Reach/        ← 只读联网：搜索 / 网页摘要 / YouTube 转写
│       │   ├── SKILL.md
│       │   ├── reach.py           ← 搜索/抓取/YouTube 纯逻辑（可注入 fetcher）
│       │   └── cli.py             ← CLI 入口 + 真实 HTTP 适配器
│       └── Agent_Runtime/    ← Agent 大脑 + 远程频道传输层
│           ├── SKILL.md
│           ├── agent_core.py     ← 频道无关 Agent 核心（全量上下文）
│           ├── members.py        ← 成员注册表（存 git 忽略的 data/members.json）
│           ├── wechat_ilink.py   ← 微信传输层
│           └── telegram_bot.py   ← Telegram 传输层
├── config.json           ← 分类 & 命令白名单（git 跟踪，不含隐私）
├── data/                 ← 全部用户数据（git 不跟踪）
│   ├── Family/           ← 家庭共享：ledger.db、receipts/、documents/
│   ├── <成员>/           ← 成员私有：notes/、schedule/、tasks/、inbox/
│   └── members.json      ← 成员注册表（dir + 每成员同步偏好）
├── tests/                ← pytest 套件（python -m pytest）
├── docs/                 ← 设计 spec 与实现 plan 存档
└── requirements-dev.txt  ← 开发依赖（pytest）
```

## 技术栈

Python 3.10+ · SQLite · DeepSeek V4 Pro · [weixin-ilink](https://pypi.org/project/weixin-ilink/)
