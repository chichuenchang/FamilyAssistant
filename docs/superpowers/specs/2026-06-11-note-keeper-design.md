# Note Keeper — Design

Date: 2026-06-11
Status: approved

## Purpose

Let the Family Assistant agent ingest miscellaneous information as private per-member notes
("帮我记住车位是B2-118", a photo of a wifi router label, a business card). Notes are recalled
two ways: an on-demand search tool, and automatic injection of pinned + recent notes into the
agent's per-message context.

## Decisions (user-confirmed)

- **Recall model**: both — `search_notes` tool AND pinned + 5 most recent notes injected per message.
- **Image notes**: 4th case in `handle_image` routing — non-receipt, non-document informational
  images get OCR'd; note stores OCR text + image path. Image file stays where the transport
  layer saved it (no moving).
- **Privacy**: per-member. Member filter enforced at DB/CLI layer via code-injected `--member`;
  the LLM can never read or write another member's notes.

## Architecture

New skill `.codewhale/skills/Note_Keeper/` mirroring Document_Keeper:

- `note_db.py` — storage layer on existing `data/ledger.db`. Table auto-created on connect:

```sql
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  member TEXT NOT NULL,
  content TEXT NOT NULL,
  source_image TEXT DEFAULT '',
  pinned INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
)
```

- `cli.py` — subprocess interface used by the agent (`_run_cli` pattern):
  - `note-add --member M --content TEXT [--source-image PATH] [--pinned]`
  - `note-list --member M [--limit N]` (newest first)
  - `note-search --member M --keyword KW` (LIKE match on content)
  - `note-delete --member M --id N` (refuses if note belongs to another member, exit 1)
  - `note-pin --member M --id N [--unpin]`
  - All commands require `--member`; every query has `WHERE member = ?`.
- `SKILL.md` — usage doc.

Storage rides in `data/ledger.db` → covered by existing backup include; no backup config change.

## Agent integration (`agent_core.py`)

- Five tools registered in `TOOL_SCHEMAS` / `_TOOL_MAP`: `save_note`, `search_notes`,
  `list_notes`, `delete_note`, `pin_note` → `_run_cli("note-add", ...)` etc.
- `save_note` added to `_MEMBER_WRITE_TOOLS`. Read/delete/pin tools also get member injected
  by `_apply_member` (extend it or add a parallel set `_MEMBER_SCOPED_TOOLS` so member is
  forced on every note tool, not only writes).
- **Context injection**: in `handle()`, before calling the LLM, fetch member's pinned notes +
  5 most recent (direct `note_db` import — in-process, no subprocess per message), truncate each content to 100 chars, append one
  block to the system message: `已存备忘（仅 {member} 可见）: ...`. Empty → no block.
- System prompt routing hint: 用户说"记一下/帮我记住/备忘" → save_note；问"我记过什么/
  XX是什么来着" → search_notes/list_notes.
- `handle_image` prompt: add case 4 — 其他有信息价值的图片（路由器标签/课表/名片等）→
  save_note，content 传 OCR 全文摘要，source-image 传保存路径。OCR 为空 → 问用户要记什么。

## Error handling

- DB/CLI failure → error string back to LLM (existing pattern), agent relays.
- `note-delete`/`note-pin` on another member's note id → exit 1, "[错误] 无此备忘".
  (Same message as nonexistent id — don't leak that the id exists.)
- Context-injection failure must never break `handle()`: wrap in try/except, log, continue.

## Testing (`tests/test_note_keeper.py`)

- add/list/search/delete/pin roundtrip on tmp db.
- Member isolation: A's notes invisible to B in list/search; B cannot delete/pin A's note.
- note-add without --member exits nonzero.
- Truncation behavior of context injection (unit-test the formatting helper).
- Tool registration: 5 names present in agent TOOL_SCHEMAS and _TOOL_MAP.

## Out of scope

- Vector/semantic search, note editing (delete + re-add covers it), reminders on notes
  (Document_Keeper owns reminders), cross-member shared notes.
