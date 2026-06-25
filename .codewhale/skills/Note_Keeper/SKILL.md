# Note Keeper

> Family Assistant 的个人备忘管理 skill。记录零散信息（"帮我记住车位是B2-118"、路由器标签照片、名片等），支持关键词搜索和置顶，Agent 每次对话自动注入置顶+最近备忘。

## 代码位置

实现就在本 skill 目录 `.codewhale/skills/Note_Keeper/`，自包含、零外部依赖（仅标准库 + SQLite）：

```
.codewhale/skills/Note_Keeper/
├── SKILL.md    ← 本文件
├── note_db.py  ← SQLite CRUD（notes 表）
├── sheet_db.py ← SQLite CRUD（worksheets + worksheet_rows 表）
├── cli.py      ← 命令行入口（user / agent / 任意调用方）
```

数据模块名带 `note_` 前缀（不叫 db）：Expense_Tracker 已在共享进程占用该模块名，避免冲突。

备忘按成员私有分库：`data/<成员目录>/notes/notes.db`（路径经 `Agent_Runtime/paths.member_store(member,"notes")`，`cli.py` 据 `--member` 解析；`NOTE_DB_PATH` 测试时覆盖）。图片存同成员 `notes/YYYY-MM/`，库内 `source_image` 记 data 相对路径。

## 数据模型

### notes — 备忘表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| member | TEXT | 归属成员 |
| content | TEXT | 备忘内容 |
| source_image | TEXT | 来源图片路径（可选） |
| pinned | INTEGER | 是否置顶（0/1，默认 0） |
| created_at | TEXT | 创建时间 ISO 格式 |

索引：`idx_notes_member ON notes(member)`。

## 使用方式

Agent 通过 `_run_cli` 子进程调用 CLI；用户也可直接命令行操作。

### CLI 命令参考

```bash
# 添加备忘
python .codewhale/skills/Note_Keeper/cli.py note-add --member 爸爸 --content "车位 B2-118"
python .codewhale/skills/Note_Keeper/cli.py note-add --member 爸爸 --content "WiFi 密码贴在路由器背面" --source-image photos/router.jpg --pinned

# 列出备忘（最新在前）
python .codewhale/skills/Note_Keeper/cli.py note-list --member 爸爸
python .codewhale/skills/Note_Keeper/cli.py note-list --member 爸爸 --limit 10

# 搜索备忘
python .codewhale/skills/Note_Keeper/cli.py note-search --member 爸爸 --keyword 车位
python .codewhale/skills/Note_Keeper/cli.py note-search --member 爸爸 --keyword WiFi

# 删除备忘
python .codewhale/skills/Note_Keeper/cli.py note-delete --member 爸爸 --id 3

# 置顶 / 取消置顶
python .codewhale/skills/Note_Keeper/cli.py note-pin --member 爸爸 --id 3
python .codewhale/skills/Note_Keeper/cli.py note-pin --member 爸爸 --id 3 --unpin
```

## note_db API

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `add_note(member, content, source_image="", pinned=False)` | 添加备忘 | `int` — 新 id |
| `list_notes(member, limit=20)` | 列出备忘（最新在前） | `list[dict]` |
| `search_notes(member, keyword)` | 关键词模糊搜索 content | `list[dict]` |
| `delete_note(member, note_id)` | 删除备忘（仅本人） | `bool` — 是否成功删除 |
| `set_pinned(member, note_id, pinned)` | 置顶/取消置顶（仅本人） | `bool` — 是否成功更新 |
| `pinned_and_recent(member, recent_limit=5)` | 置顶备忘 + 最近 N 条非置顶 | `list[dict]` |

所有函数接受可选 `db_path` 关键字参数（不传时回退旧单库默认；运行时由 CLI 据 `--member` 经 `Agent_Runtime/paths.member_store(member,"notes")` 注入按成员分库路径）。

## 隐私

- 所有查询强制 `WHERE member = ?`，按成员隔离。
- 删除/置顶操作仅当备忘归属当前成员时才生效；对不存在的 ID 或他人备忘返回相同错误，不泄露存在性。
- Agent 上下文注入仅读取当前成员的数据。

## 存储

备忘按成员私有分库：`data/<成员目录>/notes/notes.db`（路径经 `Agent_Runtime/paths.member_store(member,"notes")`，CLI 据 `--member` 解析）。图片存同成员 `notes/YYYY-MM/`，库内 `source_image` 记 data 相对路径。`data/` 由各成员的 backup scope（`members.json` 各成员 backup 块的 `scopes`）随云备份镜像，无需额外配置。

## 工作表（Worksheet）

普通备忘是追加式、无结构的零散记录；**工作表**用于**长期、可原地更新的结构化记录**。
命名工作表，按成员私有（与备忘同库 `data/<成员>/notes/notes.db`，新增 `worksheets` /
`worksheet_rows` 两表），动态 schema 以 JSON 存储。两种 kind：

- **kv**（事实清单）：命名字段 → 值，可 set/覆盖/unset。如房贷利率、保单号、续约日。
- **table**（流水记录）：多行，每行动态列（列名随时可增），按行 id 编辑/删除。如血压/体重打卡。

**仅当用户明确要求**（"建个表 / 做个 worksheet / 长期跟踪这些字段 / 这些流水"）时才建工作表；
普通"记一下"仍用 `note-add`，不要把零散备忘升级成工作表。

### CLI 命令参考

```bash
# 创建（kv 事实清单 / table 流水）
python .../Note_Keeper/cli.py sheet-create --member 爸爸 --title 房贷 --kind kv
python .../Note_Keeper/cli.py sheet-create --member 爸爸 --title 血压 --kind table --pinned

# kv 字段 set / unset
python .../cli.py sheet-set   --member 爸爸 --title 房贷 --field 利率 --value 5.2%
python .../cli.py sheet-unset --member 爸爸 --title 房贷 --field 利率

# table 行 add / edit / delete（--data 是 JSON 对象）
python .../cli.py sheet-row-add    --member 爸爸 --title 血压 --data '{"date":"06-24","sys":120}'
python .../cli.py sheet-row-edit   --member 爸爸 --title 血压 --row-id 1 --data '{"date":"06-24","sys":125}'
python .../cli.py sheet-row-delete --member 爸爸 --title 血压 --row-id 1

# 列出 / 显示 / 重命名 / 置顶 / 删除
python .../cli.py sheet-list   --member 爸爸
python .../cli.py sheet-show   --member 爸爸 --title 血压
python .../cli.py sheet-rename --member 爸爸 --title 血压 --new-title 血压记录
python .../cli.py sheet-pin    --member 爸爸 --title 血压 [--unpin]
python .../cli.py sheet-delete --member 爸爸 --title 血压
```

### sheet_db API

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `create_sheet(member, title, kind, pinned=False)` | 建表（kind ∈ kv/table；标题成员内唯一） | `int` 新 id；空/坏 kind/重名 → `ValueError` |
| `get_sheet(member, title)` | 取整表（meta + `kv_data` 字典 + `rows` 列表） | `dict` 或 `None` |
| `list_sheets(member)` | 列出（id/title/kind/pinned/size/updated_at） | `list[dict]` |
| `set_field(member, title, field, value)` | kv 设置/覆盖字段 | `bool`（非 kv 表/缺表 → False） |
| `unset_field(member, title, field)` | kv 删字段 | `bool` |
| `add_row(member, title, row_data: dict)` | table 加行 | `int` 行 id 或 `None`（非 table 表） |
| `edit_row(member, title, row_id, row_data: dict)` | table 覆盖行 | `bool` |
| `delete_row(member, title, row_id)` | table 删行 | `bool` |
| `rename_sheet(member, title, new_title)` | 重命名 | `bool`（缺表/重名 → False） |
| `set_pinned(member, title, pinned)` | 置顶/取消 | `bool` |
| `delete_sheet(member, title)` | 删整表（级联删行） | `bool` |
| `pinned_sheets(member)` | 所有置顶表全量内容（供上下文注入） | `list[dict]` |

所有函数接受可选 `db_path`（CLI 据 `--member` 经 `paths.member_store(member,"notes")` 注入）。

### 上下文注入与隔离

- **置顶工作表全量注入**：每次对话把该成员置顶表整表渲染进 system prompt
  （`_worksheets_context`，进程内直调 `sheet_db.pinned_sheets`）。
- **软行上限**：table 超过 `worksheet_pin_row_cap`（config `notes.worksheet_pin_row_cap`，默认 80）
  只渲染前 N 行并附"…还有 M 行，用 show_worksheet 看全部"，控制每条消息的 token 成本。
- **成员隔离**：所有查询强制 `WHERE member = ?`；行表带冗余 member。改/删他人或不存在的表/行
  返回与"缺失"相同的 False/None，不泄露存在性。Agent 层对所有工作表工具强制注入 member，
  LLM 不得跨成员读写。

## 技能边界

覆盖：
- ✅ 添加/列出/搜索/删除备忘
- ✅ 置顶 + 最近备忘自动注入 Agent 上下文
- ✅ 工作表（kv 事实清单 / table 流水），动态 schema，原地更新，置顶全量注入
- ✅ 成员隔离（查询级强制过滤）
- ✅ 图片来源关联（不移动原始文件）

不覆盖：
- ❌ 备忘编辑（删除 + 重新添加即可）
- ❌ 工作表预定义/校验 schema（设计为全动态）、列排序、单元格历史
- ❌ 向量/语义搜索
- ❌ 备忘到期提醒（Document_Keeper 负责文档到期）
- ❌ 跨成员共享备忘/工作表
