# Form Filler

> Family Assistant 的 PDF 表格代填 skill。收到 PDF 表格 → 识别字段 → 一条消息问一个空（已知信息只作建议，绝不擅自填）→ 生成填好的 PDF 发回用户。

## 代码位置

实现就在本 skill 目录 `.codewhale/skills/Form_Filler/`，核心标准库，PDF 处理走可选依赖：

```
.codewhale/skills/Form_Filler/
├── SKILL.md          ← 本文件
├── form_session.py   ← 会话 JSON CRUD（data/<成员>/forms/<id>.json）
├── form_fill.py      ← AcroForm 可填写 PDF 读字段/填值（pypdf，tier 1，输出保持矢量）
├── form_overlay.py   ← 平面/扫描 PDF 渲染+盖字（pypdfium2 + Pillow，tier 2，输出栅格）
├── cli.py            ← 命令行入口（user / agent / 任意调用方）
```

## 两条路径（tier）

1. **acroform**（政府/移民/银行表单常见）：`form-scan` 直接读出字段名/类型/选项 → 逐字段问答 → pypdf 填值 + NeedAppearances 输出。
2. **flat**（扫描件/无字段 PDF）：`form-scan` 用 pypdfium2 逐页渲染成图（上限 `MAX_FLAT_PAGES=6` 页，scale 2.0）→ 腾讯 OCR 取每行文本+坐标 → **LLM 从布局推断字段和填写锚点** → `form-define` 提交 → 问答 → Pillow 把答案画在锚点处，重组多页 PDF。

## 会话 JSON

`data/<成员>/forms/<id>.json`，id 格式 `YYYYMMDD_HHMMSS_xxxx`。

| 字段 | 说明 |
|------|------|
| id / member / created | 会话标识、归属成员、创建时间 |
| source_pdf | 原始 PDF（data 相对路径） |
| kind | `acroform` / `flat` |
| status | `defining`（仅 flat，等字段定义）→ `collecting` → `done` / `cancelled` |
| pages | flat 专用：`[{page, image, width, height}]`，image 为 data 相对路径 |
| fields | `[{name, label, type: text/checkbox/choice, options, page, anchor{x,y,w,h}, value, asked}]`；`value=null` 未答，`""` 留空 |
| render_path | 填好的 PDF（data 相对路径） |

会话落盘 → 一字段一问跨消息、`/clear`、重启都不丢；`form-list` 可恢复。

## CLI 子命令

| 命令 | 说明 |
|------|------|
| `form-scan --file <pdf> --member <m>` | 建会话。acroform 列字段；flat 输出逐页 OCR 文本+坐标等 LLM 定义字段 |
| `form-define --session <id> --fields <json> --member <m>` | flat 专用：提交 LLM 推断的字段（锚点校验在页面内） |
| `form-next --session <id> --member <m>` | 下一个未回答字段 |
| `form-set --session <id> --field <n> --value <v> --member <m>` | 记答案（checkbox 收 是/否/on/off；空串=留空） |
| `form-render --session <id> --member <m>` | 生成 PDF。**stdout 第一行 = data 相对路径（哨兵契约，自动发给用户）**，后续行 `警告: …` |
| `form-list --member <m>` | 会话列表（恢复中断填表） |
| `form-cancel --session <id> --member <m>` | 取消 |

错误一律 stdout `[错误] …` + exit 1。

## 成员隔离

- 会话路径经 `paths.member_forms_dir(member)`；member 由 agent_core `_apply_member` 注入，LLM 无法跨成员。
- `form-scan --file` 路径闸门：须在 data_root 内且属本成员目录或 Family 共享目录（与 send_file 同规则）。

## 依赖与降级

| 依赖 | 用途 | 缺席行为 |
|------|------|---------|
| pypdf | tier 1 读/填 AcroForm | form-scan 提示 `pip install pypdf` |
| pypdfium2 + Pillow | tier 2 渲染+盖字 | 平面表不可用（acroform 不受影响），提示安装 |
| 腾讯云 OCR | tier 2 标签坐标 | 平面表提示配置 TENCENT_SECRET_ID/KEY |

加密 PDF → `[错误] PDF 已加密，无法读取`。纯 XFA 表单无 AcroForm 字段 → 自动走 flat 路径。

## Agent 工具映射

`fill_form_scan` / `fill_form_define_fields` / `fill_form_next` / `fill_form_set_answer` / `fill_form_render` / `fill_form_list` / `fill_form_cancel`（agent_core `_run_cli` 包装；`fill_form_render` 在 `_DOC_TOOLS`，产出路径自动作为文件发回）。

行为约定（system prompt 强制）：一条消息只问一个字段；已知值只作建议；绝不替用户编造/擅自填值；全答完（或用户明说留空）才 render。
