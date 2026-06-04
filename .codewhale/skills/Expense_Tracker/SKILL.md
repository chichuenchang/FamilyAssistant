# Expense Tracker

> Family Assistant 的记账 skill。支持多币种日常开销、收入、投资、定期存款和报税记录管理。

## 项目结构

```
FamilyAssistant/
├── SKILL.md              ← 根索引
├── .codewhale/skills/    ← Agent 技能目录
├── README.md
├── config.json           ← 分类 & 路径配置
├── data/
│   └── ledger.db         ← SQLite 数据库
├── receipts/             ← 原始票据/截图存档
│   └── YYYY/MM/
└── scripts/
    ├── models.py
    ├── db.py
    └── cli.py
```

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
python scripts/cli.py init

# 添加交易
python scripts/cli.py add --type expense --amount 45.50 --currency CNY --date 2026-05-31 --category 餐饮 --desc "午餐"

# 查询
python scripts/cli.py list --type expense --start 2026-05-01 --end 2026-05-31
python scripts/cli.py list --category 餐饮 --currency USD

# 删除
python scripts/cli.py delete --id 3

# 汇总
python scripts/cli.py summary --type expense --year 2026 --month 5
python scripts/cli.py monthly --type expense --year 2026

# 定期存款
python scripts/cli.py deposit-add --amount 50000 --currency USD --bank "HSBC" --term 12 --rate 4.5 --start-date 2026-01-15 --maturity 2027-01-15
python scripts/cli.py deposit-list --currency USD --active

# 报税
python scripts/cli.py tax-add --year 2025 --country US --data '{"total_income":85000,"tax_paid":12000}' --filing-date 2026-04-10
python scripts/cli.py tax-list --year 2025

# 汇率
python scripts/cli.py fx-set --from USD --to CNY --rate 7.25
python scripts/cli.py fx-get --from USD --to CNY
```

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
- 汇总时从 `exchange_rates` 取最新汇率折算到 CNY
- 首次使用某币种提醒设置汇率
- 汇率建议每季度更新

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
