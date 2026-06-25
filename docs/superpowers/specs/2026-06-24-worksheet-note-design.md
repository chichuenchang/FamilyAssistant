# Worksheet Note — Design

> Date: 2026-06-24
> Skill: Note_Keeper (extension)
> Status: approved

## Problem

Note_Keeper stores freeform, append-only memos. It cannot maintain a single,
long-lived **structured** record that the user updates over time (a "worksheet"):
there is no in-place update, no fields/columns, and no keyed lookup. Use case:
the user has information they must track for a very long period (a living fact
sheet, or a running log table) and asks the assistant to keep it current.

## Goals

- Named worksheets, per-member private, two kinds:
  - **kv** — a living fact sheet: named fields you set/overwrite/unset.
  - **table** — an append log: rows with dynamic columns, each row editable/deletable by id.
- Fully dynamic schema (no upfront columns/fields; any name anytime).
- Full mutation surface (set/unset field; add/edit/delete row; rename; pin; delete sheet).
- Pinned worksheets inject **full content** into the agent context each turn,
  with a soft row cap to bound per-turn token cost.
- Agent creates a worksheet **only when the user explicitly asks** to track
  structured info long-term — never auto-promotes a casual `save_note`.

## Non-goals

- Predefined/validated schemas (kept dynamic by design).
- Vector/semantic search (lookup is by exact title).
- Cross-member shared worksheets (per-member isolation, like notes).
- Cell-level history / audit trail (edit overwrites).

## Placement

Lives inside the **Note_Keeper** skill — shares the per-member store, backup
scope, privacy model, and CLI entry. No new skill.

```
.codewhale/skills/Note_Keeper/
├── note_db.py     ← existing (notes table) — unchanged
├── sheet_db.py    ← NEW: worksheets + worksheet_rows CRUD
├── cli.py         ← existing, extended with sheet-* subcommands
```

Same per-member db file `data/<member>/notes/notes.db`
(path via `Agent_Runtime/paths.member_store(member, "notes")`). New tables added
to that same file → shares the existing backup dirty-mark and backup scope.
Test override env var: `NOTE_DB_PATH` (reused; both modules honor it via cli).

## Data model

Dynamic schema is stored as JSON blobs.

```sql
CREATE TABLE IF NOT EXISTS worksheets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member     TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    kind       TEXT    NOT NULL,          -- 'kv' | 'table'
    kv_data    TEXT    NOT NULL DEFAULT '{}',  -- JSON {field: value}; kind='kv'
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE(member, title)
);
CREATE INDEX IF NOT EXISTS idx_worksheets_member ON worksheets(member);

CREATE TABLE IF NOT EXISTS worksheet_rows (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id   INTEGER NOT NULL,          -- FK worksheets.id
    member     TEXT    NOT NULL,          -- denormalized for isolation enforcement
    row_data   TEXT    NOT NULL DEFAULT '{}',  -- JSON {col: value}; dynamic cols
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worksheet_rows_sheet ON worksheet_rows(sheet_id);
```

- **kv** mutates `worksheets.kv_data` (a JSON object). `set` adds/overwrites a
  field; `unset` removes one.
- **table** stores each row in `worksheet_rows` so rows get stable ids →
  edit/delete by id. Columns are whatever keys appear in `row_data`.
- `updated_at` bumps on any mutation (field set/unset, row add/edit/delete, rename, pin).
- Title is the handle, unique per member. Lookup is by `(member, title)`.

## sheet_db API (`sheet_db.py`)

All functions take an optional `db_path` kwarg (injected by CLI per member) and
enforce `WHERE member = ?`. No-leak semantics match notes: an unknown title /
row, or another member's data, returns the same failure (False / None) — never
discloses existence.

| Function | Returns |
|----------|---------|
| `create_sheet(member, title, kind, pinned=False, db_path=None)` | `int` new id; `ValueError` on empty title/bad kind; duplicate `(member,title)` → `ValueError` |
| `get_sheet(member, title, db_path=None)` | `dict` (sheet meta + parsed `kv_data` + rows for table) or `None` |
| `list_sheets(member, db_path=None)` | `list[dict]` — id, title, kind, pinned, size (field count / row count), updated_at |
| `set_field(member, title, field, value, db_path=None)` | `bool` (False if no such kv sheet) |
| `unset_field(member, title, field, db_path=None)` | `bool` (False if sheet/field absent) |
| `add_row(member, title, row_data: dict, db_path=None)` | `int` row id or `None` if no such table sheet |
| `edit_row(member, title, row_id, row_data: dict, db_path=None)` | `bool` (overwrite row_data) |
| `delete_row(member, title, row_id, db_path=None)` | `bool` |
| `rename_sheet(member, title, new_title, db_path=None)` | `bool` (False on missing / new-title clash) |
| `set_pinned(member, title, pinned, db_path=None)` | `bool` |
| `delete_sheet(member, title, db_path=None)` | `bool` (cascades its rows) |
| `pinned_sheets(member, db_path=None)` | `list[dict]` full content of pinned sheets, for context injection |

Kind mismatches are guarded: `set_field` on a table sheet → False; `add_row` on
a kv sheet → None. `row_data` / values are JSON-serialized; non-dict `row_data`
→ `ValueError`.

## CLI surface (`cli.py`, `sheet-*`)

Every command requires `--member` (forced by agent layer). Mirrors the note CLI:
plain-text stdout, `_mark_backup_dirty()` after writes, exit 1 + stderr on failure.

```
sheet-create   --member --title --kind kv|table [--pinned]
sheet-list     --member
sheet-show     --member --title
sheet-set      --member --title --field --value          # kv
sheet-unset    --member --title --field                  # kv
sheet-row-add  --member --title --data '{"col":"val",...}'   # table; JSON
sheet-row-edit --member --title --row-id --data '{...}'      # table; JSON overwrite
sheet-row-delete --member --title --row-id
sheet-rename   --member --title --new-title
sheet-pin      --member --title [--unpin]
sheet-delete   --member --title
```

`--data` is a JSON object string. `_run_cli` passes argv as a list (no shell),
so JSON is safe unquoted. CLI parses it with `json.loads`; bad JSON → exit 1.

`sheet-show` renders kv as `field: value` lines; table as a header + numbered
rows (`#<row_id>  col=val  col=val`). `sheet-list` shows `📊 <title> (<kind>, N) [📌]`.

## Agent wiring (`agent_core.py`)

1. **Whitelist + routing**
   - `_SHEET_COMMANDS = {"sheet-create","sheet-list","sheet-show","sheet-set",
     "sheet-unset","sheet-row-add","sheet-row-edit","sheet-row-delete",
     "sheet-rename","sheet-pin","sheet-delete"}`
   - `_cli_path`: route `_SHEET_COMMANDS` → `Note_Keeper`.
   - `ALLOWED_COMMANDS |= _SHEET_COMMANDS` (always-on, like notes/cal).

2. **Tool handlers + schemas** — one `_tool_*` per agent function calling `_run_cli`:
   `create_worksheet, show_worksheet, list_worksheets, set_worksheet_field,
   unset_worksheet_field, add_worksheet_row, edit_worksheet_row,
   delete_worksheet_row, rename_worksheet, pin_worksheet, delete_worksheet`.
   `add_worksheet_row` / `edit_worksheet_row` take a `data` object param; the
   handler `json.dumps` it into `--data`.

3. **Member forcing** — add all worksheet tools to the member-forced set (extend
   `_NOTE_TOOLS` or add `_SHEET_TOOLS` handled identically in `_apply_member`):
   strip any LLM-supplied member, inject the resolved sender. Guarantees
   per-member isolation for read and write alike.

4. **Context injection** — new `_worksheets_context(member)`:
   - In-process call to `sheet_db.pinned_sheets(member)` (subprocess too heavy per message).
   - Render each pinned sheet in full into a system-prompt block
     (`## 已存工作表（仅 <member> 可见）`).
   - **Soft cap** `WORKSHEET_PIN_ROW_CAP` (config `notes.worksheet_pin_row_cap`,
     default 80): a table beyond the cap renders the first N rows + a line
     `…还有 M 行，用 show_worksheet 看全部`. kv sheets render fully.
   - Any failure returns "" (never breaks `handle()`), mirroring `_notes_context`.
   - Appended alongside `_notes_context` wherever that is assembled.

5. **System-prompt guidance** — add a short rule:
   - 默认杂项信息用 `save_note`。
   - 仅当用户**明确**要求"建个表/做个 worksheet/长期跟踪这些字段"时才用
     `create_worksheet`；普通"记一下"不要升级成工作表。
   - kv 表（事实清单）vs table 表（流水/记录）依用户意图选 kind。

## Privacy & safety

- Every query filters `WHERE member = ?`; rows carry denormalized `member`.
- Mutations succeed only when the sheet/row belongs to the caller; otherwise the
  same False/None as a missing item (no existence disclosure).
- Agent layer strips and re-injects `member` for all worksheet tools — the LLM
  cannot read or write across members.
- No new file paths exposed to the LLM; worksheets are pure db rows.

## Testing (`tests/test_worksheet.py`)

Drive `sheet_db` directly with a temp `db_path`; a few CLI smoke tests via subprocess.

- create kv + table; duplicate title → ValueError.
- kv: set new field, overwrite field, unset field, unset-missing → False.
- table: add row (returns id), edit row overwrites, delete row, dynamic new column on later row.
- kind guards: `set_field` on table → False; `add_row` on kv → None.
- rename: success; clash with existing title → False; updates handle.
- pin/unpin; `pinned_sheets` returns full content.
- **isolation**: member B cannot get/set/delete member A's sheet (all return None/False).
- delete_sheet cascades rows.
- context-cap: table over `worksheet_pin_row_cap` truncates with the "还有 M 行" note.
- backup dirty-mark fires on a CLI write (mock/inspect like existing note tests).

## Out of scope / future

- Column reordering, typed columns, per-cell history.
- Export (CSV) — could add `sheet-export` later.
- Semantic search over worksheet contents.
