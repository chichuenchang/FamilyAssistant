# Document Keeper

> Family Assistant 的家庭文档管理 skill。归档重要/临时文档（租约、保险单、SIN、健康卡等），OCR 索引，跟踪到期日并提醒。

## 代码位置

实现就在本 skill 目录 `.codewhale/skills/Document_Keeper/`，自包含、零外部依赖（仅标准库 + SQLite）：

```
.codewhale/skills/Document_Keeper/
├── SKILL.md       ← 本文件
├── doc_models.py  ← 数据模型 / SCHEMA / 文档类型（读 config.json）
├── doc_db.py      ← SQLite CRUD & 到期查询（documents 表，建在 data/Family/ledger.db）
├── cli.py         ← 命令行入口（user / agent / 任意调用方）
└── reminder.py    ← 每日到期提醒（传输层轮询时调用，按频道按日去重）
```

数据模块名带 `doc_` 前缀（不叫 models/db）：Expense_Tracker 已在共享进程占用这两个模块名。

文件存档在 `data/Family/documents/<类型>/`，数据库共用家庭账本 `data/Family/ledger.db`（路径经 `Agent_Runtime/paths`）。行内 `file_path` 记 data 相对路径（`Family/documents/...`）。文档为家庭共享（含成员个人证件，统一归家庭目录）。

## 数据模型

### documents — 文档表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| doc_type | TEXT | 类型（config.json `doc_types`：lease/insurance/health/id_document/other） |
| title | TEXT | 名称，如 "2026公寓租约" |
| member | TEXT | 归属成员（空 = 家庭级） |
| issuer | TEXT | 签发方（房东/保险公司/政府机构） |
| doc_number | TEXT | 编号（保单号/证件号） |
| issue_date / expiry_date | TEXT | 签发/到期 ISO 日期；长期有效则 expiry 为空 |
| action_note | TEXT | 到期要做什么（如 提前60天通知房东） |
| remind_days | INTEGER | 该文档提醒提前量；空用 config `reminder_lead_days` |
| acknowledged | INTEGER | 提醒已确认（到期日变更自动清零） |
| file_path | TEXT | 原始文件相对路径 `documents/<类型>/...` |
| ocr_text | TEXT | OCR 全文（关键词检索用） |
| data | TEXT(JSON) | 灵活字段（含 file_sha256 重复检测哈希） |
| status | TEXT | active / expired / archived / superseded |
| notes / created_at | TEXT | 备注 / 创建时间 |

## 接收文档（拍照 / 截图）

1. **存档原始文件** — `doc-add --file` 自动复制到 `documents/<类型>/<成员>_标题.ext`（长期文档，无成员则省略前缀）
2. **OCR 提取** — 全文进 `ocr_text` 索引；DeepSeek 结构化提取 类型/标题/签发方/编号/日期
3. **写入记录** — `doc-add`，归属成员由代码注入（防冒名，与记账同规则）
4. **告知结果** — 回复提取出的到期日等关键信息，用户可用 `doc-update` 纠正

### 重复检测

同类型 + 同编号（无编号时同文件 SHA-256）→ 拦截。`--force` 强制写入。superseded 的旧文档不算重复。

## 到期提醒（双通道）

- **随问随查**：`doc-due [--days N]` — active 且 `到期日 − 提前量 ≤ 今天`（含已过期），未确认在前。提前量：`--days` > 文档 `remind_days` > config `reminder_lead_days`。
- **每日推送**：`reminder.check_and_push(send_fn, 频道)` 由传输层轮询调用，每频道每日最多一次，推给该频道全部已登记成员。状态存 `data/.doc_reminder_state`；推送失败不记状态、下轮重试。`doc-ack` 后该文档不再重复提醒，直到到期日更新。

## CLI 命令参考

```bash
# 归档（--file 自动复制进文档目录；--member 可选）
python .codewhale/skills/Document_Keeper/cli.py doc-add --type lease --title "2026公寓租约" \
  --issuer "房东张三" --number L-001 --issue-date 2026-03-01 --expiry 2027-02-28 \
  --action-note "提前60天通知房东" --file receipts/2026-06/xxx.jpg --ocr-text "..."

# 查询 / 详情
python .codewhale/skills/Document_Keeper/cli.py doc-list --type insurance --keyword 车险
python .codewhale/skills/Document_Keeper/cli.py doc-show --id 3

# 到期
python .codewhale/skills/Document_Keeper/cli.py doc-due
python .codewhale/skills/Document_Keeper/cli.py doc-due --days 90

# 更新（续约改到期日会重新进入提醒）/ 确认提醒
python .codewhale/skills/Document_Keeper/cli.py doc-update --id 3 --expiry 2028-02-28
python .codewhale/skills/Document_Keeper/cli.py doc-ack --id 3

# 删除（仅本机；Agent 白名单外）
python .codewhale/skills/Document_Keeper/cli.py doc-remove --id 3 --delete-file
```

## 查询模式

| 用户问法 | 操作 |
|---------|------|
| "存一下这份租约"（带图） | OCR → `doc-add --file <图> --ocr-text ...` |
| "租约什么时候到期" | `doc-list --type lease` |
| "我们有哪些保险" | `doc-list --type insurance` |
| "最近有什么要到期的" | `doc-due` |
| "续约了，新到期日X" | `doc-update --id N --expiry X` |
| "知道了别再提醒" | `doc-ack --id N` |

## 隐私

所有文档图片走腾讯云 OCR、提取文本走 DeepSeek（用户已知情选择）。原始文件与数据库默认只存本机；
启用 Remote Backup（可选）后会镜像到用户自己的云盘（见 [Remote Backup](../Remote_Backup/SKILL.md)）。

## 技能边界

覆盖：
- ✅ 文档归档 + OCR 全文索引
- ✅ 到期跟踪、按需查询 + 每日推送提醒
- ✅ 成员归属（与记账同防冒名机制）
- ✅ 重复检测（编号 / 文件哈希）

不覆盖：
- ❌ 文档版本对比（新版本另存一条，旧的标 superseded）
- ❌ 静态加密
- ❌ PDF 文字层解析（PDF 只存档，元数据手动填）
- ❌ 与文档无关的通用提醒
