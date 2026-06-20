# Note Keeper

> Family Assistant 的个人备忘管理 skill。记录零散信息（"帮我记住车位是B2-118"、路由器标签照片、名片等），支持关键词搜索和置顶，Agent 每次对话自动注入置顶+最近备忘。

## 代码位置

实现就在本 skill 目录 `.codewhale/skills/Note_Keeper/`，自包含、零外部依赖（仅标准库 + SQLite）：

```
.codewhale/skills/Note_Keeper/
├── SKILL.md    ← 本文件
├── note_db.py  ← SQLite CRUD（notes 表）
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

所有函数接受可选 `db_path` 关键字参数（默认使用 config.json 中的 `db_path`）。

## 隐私

- 所有查询强制 `WHERE member = ?`，按成员隔离。
- 删除/置顶操作仅当备忘归属当前成员时才生效；对不存在的 ID 或他人备忘返回相同错误，不泄露存在性。
- Agent 上下文注入仅读取当前成员的数据。

## 存储

备忘按成员私有分库：`data/<成员目录>/notes/notes.db`（路径经 `Agent_Runtime/paths.member_store(member,"notes")`，CLI 据 `--member` 解析）。图片存同成员 `notes/YYYY-MM/`，库内 `source_image` 记 data 相对路径。`data/` 由各成员的 backup scope（`members.json` 各成员 backup 块的 `scopes`）随云备份镜像，无需额外配置。

## 技能边界

覆盖：
- ✅ 添加/列出/搜索/删除备忘
- ✅ 置顶 + 最近备忘自动注入 Agent 上下文
- ✅ 成员隔离（查询级强制过滤）
- ✅ 图片来源关联（不移动原始文件）

不覆盖：
- ❌ 备忘编辑（删除 + 重新添加即可）
- ❌ 向量/语义搜索
- ❌ 备忘到期提醒（Document_Keeper 负责文档到期）
- ❌ 跨成员共享备忘
