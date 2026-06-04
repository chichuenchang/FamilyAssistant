# Expense Tracker

> Family Assistant 的记账 skill。支持多币种日常开销、收入、投资、定期存款和报税记录管理。

## 代码位置

实现就在本 skill 目录 `.codewhale/skills/Expense_Tracker/`，自包含、零外部依赖（仅标准库 + SQLite）：

```
.codewhale/skills/Expense_Tracker/
├── SKILL.md     ← 本文件
├── models.py    ← 数据模型 / SCHEMA / 默认分类
├── db.py        ← SQLite CRUD & 查询层（DB_PATH 来自 config.json db_path）
└── cli.py       ← 命令行入口（user / agent / 任意调用方）
```

数据与配置仍在项目根：`data/ledger.db`（SQLite）、`config.json`（分类 & 路径）、`receipts/YYYY/MM/`（票据存档）。`cli.py` 把同目录加入 `sys.path` 后 `from db import ...`，无需从项目根 import。

## 数据模型

### transactions — 流水表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| type | TEXT | `expense` / `income` / `investment` / `savings` |
| amount | REAL | 金额（正数） |
| currency | TEXT | `CNY` / `USD` / `CAD` |
| category | TEXT | 分类名（见 config.json） |
| description | TEXT | 描述 |
| date | TEXT | ISO 日期 `YYYY-MM-DD` |
| receipt_path | TEXT | 票据文件相对路径 |
| notes | TEXT | 备注 |
| created_at | TEXT | 创建时间 |

### deposits — 定期存款表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| amount | REAL | 本金 |
| currency | TEXT | 币种 |
| bank | TEXT | 银行名 |
| term_months | INTEGER | 期限（月） |
| rate | REAL | 年利率（%） |
| start_date | TEXT | 起存日期 |
| maturity_date | TEXT | 到期日期 |
| receipt_path | TEXT | 单据路径 |
| notes | TEXT | 备注 |

### tax_filings — 报税记录表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| year | INTEGER | 纳税年度 |
| country | TEXT | 国家：`US` / `CA` |
| filing_date | TEXT | 申报日期 |
| data | TEXT(JSON) | 灵活字段 |
| receipt_path | TEXT | 税表文件路径 |
| notes | TEXT | 备注 |

### exchange_rates — 汇率表

| 字段 | 类型 | 说明 |
|------|------|------|
| from_currency | TEXT | 源币种 |
| to_currency | TEXT | 目标币种 |
| rate | REAL | 汇率 |
| date | TEXT | 日期 |
| source | TEXT | 来源 |

## 飞书远程收票

用户可从手机拍照发到飞书群，Agent 定时拉取处理。

### 配置

1. [飞书开放平台](https://open.feishu.cn) 创建自建应用
2. 添加机器人能力，权限 `im:message`、`im:message:read_as_bot`
3. 设环境变量：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`
4. `python scripts/feishu_inbox.py --list-chats` 获取 chat_id

### Agent 定时流程

1. `python scripts/feishu_inbox.py` → 下载新图片到 `receipts/inbox/`
2. 遍历 OCR 提取信息
3. `cli.py add` 写入数据库（自动去重）
4. 图片移到 `receipts/YYYY/MM/` 归档

## 接收票据（截图 / 发票照片）

1. **OCR 提取** — 金额、日期、商家/类别
2. **自动归类** — 匹配 `config.json` 分类
3. **保存原始票据** — `receipts/YYYY/MM/`
4. **写入记录** — `cli.py add --receipt <路径>`
5. **告知结果**

### 重复检测

`add` 内置：同日 + 同金额 + 同币种 + 描述相近 → 拦截。`--force` 强制写入。

### 票据存储约定

- 文件名：`YYYY-MM-DD_type_description.ext`
- 路径相对项目根目录
- 重要票据 `notes` 标记 `[重要票据]`

## CLI 命令参考

```bash
# 初始化
python .codewhale/skills/Expense_Tracker/cli.py init

# 添加交易
python .codewhale/skills/Expense_Tracker/cli.py add --type expense --amount 45.50 --currency CNY --date 2026-05-31 --category 餐饮 --desc "午餐"

# 查询
python .codewhale/skills/Expense_Tracker/cli.py list --type expense --start 2026-05-01 --end 2026-05-31
python .codewhale/skills/Expense_Tracker/cli.py list --category 餐饮 --currency USD

# 删除
python .codewhale/skills/Expense_Tracker/cli.py delete --id 3

# 汇总
python .codewhale/skills/Expense_Tracker/cli.py summary --type expense --year 2026 --month 5
python .codewhale/skills/Expense_Tracker/cli.py monthly --type expense --year 2026

# 定期存款
python .codewhale/skills/Expense_Tracker/cli.py deposit-add --amount 50000 --currency USD --bank "HSBC" --term 12 --rate 4.5 --start-date 2026-01-15 --maturity 2027-01-15
python .codewhale/skills/Expense_Tracker/cli.py deposit-list --currency USD --active

# 报税
python .codewhale/skills/Expense_Tracker/cli.py tax-add --year 2025 --country US --data '{"total_income":85000,"tax_paid":12000}' --filing-date 2026-04-10
python .codewhale/skills/Expense_Tracker/cli.py tax-list --year 2025

# 汇率
python .codewhale/skills/Expense_Tracker/cli.py fx-set --from USD --to CNY --rate 7.25
python .codewhale/skills/Expense_Tracker/cli.py fx-get --from USD --to CNY

# 合法分类（来自 config.json）
python .codewhale/skills/Expense_Tracker/cli.py categories
python .codewhale/skills/Expense_Tracker/cli.py categories --type expense
```

## 分类 & 币种校验（单一事实来源）

`config.json` 的 `categories` / `supported_currencies` / `base_currency` 是合法值的**唯一来源**。`models.py` 在导入时读取一次 config.json，暴露为 `CATEGORIES` / `SUPPORTED_CURRENCIES` / `BASE_CURRENCY` 常量；`db.py` 只从 `models` 取值（薄封装 `get_categories` / `get_supported_currencies` / `get_base_currency`），不再各自读配置。config.json 缺失/损坏时用 `models.py` 内的应急回退值（每类型仅 `其他` + USD）。

- 数据流：`config.json` → `models`（读一次）→ `db` 取值 → `cli` 校验。改值只改 config.json，**改后重启进程生效**（导入期读取，非每次调用）。
- `add` / `deposit-add` 写入前校验币种；`add` 还校验分类（按交易类型）。非法值报错并退出码 `1`，不写库。
- 三方调用（user CLI / Agent subprocess / 进程内 import）走同一份校验，行为一致。
- `categories` 命令可随时查当前合法分类。

## 查询模式

| 用户问法 | 操作 |
|---------|------|
| "这个月花了多少" | `monthly --type expense --year <year>` |
| "5 月餐饮花了多少" | `summary --type expense --year <year> --month 5` |
| "那一笔 ¥320 是什么" | `list --start YYYY-MM --end YYYY-MM` 匹配 |
| "我有哪些定期存款" | `deposit-list` |
| "2025 美国报了多少税" | `tax-list --year 2025 --country US` |
| "现在美元汇率多少" | `fx-get --from USD --to CNY` |

## 多币种策略

- 每笔保留原币种，不自动转换
- `summary` / `monthly` **按币种分组汇总，不跨币种相加**（输出每币种一块）
- 基准币种 = `config.json` base_currency（当前 USD），用于 `fx-get` / `convert_to_base` 折算
- `exchange_rates` 存手工汇率；首次使用某币种提醒设置，建议每季度更新

## 远程通道

- **微信**：用户可通过微信发送文字或图片与 Agent 交互（基础设施，后台常驻运行，Agent 无需管理）
- **飞书**：用户从飞书群发票据图片，Agent 定时拉取处理
- **OCR**：票据图片自动识别 → 结构化提取（通过 `.codewhale/skills/OCR/ocr.py` 调用腾讯云 OCR，详见 [OCR Skill](../OCR/SKILL.md)）

## 技能边界

覆盖：
- ✅ 日常开销 / 收入记账
- ✅ 多币种（CNY USD CAD）
- ✅ 定期存款追踪
- ✅ 报税记录存档
- ✅ 票据 OCR + 原始文件存档
- ✅ 自然语言查询

不覆盖：
- ❌ 银行自动同步
- ❌ 预算告警
- ❌ 股票/基金实时行情
- ❌ 图表仪表盘
- ❌ 多人协作
