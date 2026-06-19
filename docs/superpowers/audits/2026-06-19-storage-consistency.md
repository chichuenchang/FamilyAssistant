# Storage-Layout Consistency Audit — 2026-06-19

> **Scope:** `.codewhale/skills/**/*.py`, `.codewhale/skills/**/SKILL.md`, `config.json`, `FamilyAssistant.md`, `README.md`, `docs/**`, `tests/**/*.py`
> **Baseline:** `docs/superpowers/specs/2026-06-19-per-member-storage-layout-design.md` + `paths.py` (single source of truth).
> **Excluded:** `tests/test_migrate_storage.py` (intentionally synthesizes old-schema DBs).

---

## HIGH — Wrong runtime behavior or user-facing doc that lies

- **[HIGH] README.md:18** — `"原始票据按月存档 receipts/YYYY-MM/，与账目记录关联"` — stale: receipts now live under `data/Family/receipts/YYYY-MM/`. Fix: `data/Family/receipts/YYYY-MM/`.
- **[HIGH] README.md:22** — `"原件存 documents/<类型>/"` — stale: documents now live under `data/Family/documents/<type>/`. Fix: `data/Family/documents/<类型>/`.
- **[HIGH] README.md:60** — `"隐私数据（成员注册表/账本/票据/文档）全部在 git 忽略路径（data/ receipts/ documents/）"` — stale: `receipts/` and `documents/` are no longer root-level; everything is under `data/`. Fix: `全部在 git 忽略路径（data/）`.
- **[HIGH] README.md:166–168** — directory tree shows root-level `├── receipts/` and `├── documents/` — stale: these dirs no longer exist at root; they are under `data/Family/`. Fix: remove both lines; add `data/Family/` subtree note or simply keep `data/` as the sole user-data root.
- **[HIGH] FamilyAssistant.md:64–66** — config key table lists three removed keys:
  - `"| db_path | models.DB_PATH |"` — stale: `db_path` key removed from config.json; DB_PATH now `paths.family_ledger()`.
  - `"| receipts_dir | agent_core.RECEIPTS_DIR … |"` — stale: key removed; computed via `paths.family_receipts_dir()`.
  - `"| documents_dir | Document_Keeper/doc_models.py … |"` — stale: key removed; computed via `paths.family_documents_dir()`.
  Fix: replace with `data_root`, `family_dir_name` rows, referencing `paths.py`.
- **[HIGH] .codewhale/skills/Expense_Tracker/SKILL.md:19** — `"数据与配置仍在项目根：data/ledger.db（SQLite）、config.json（分类 & 路径）、receipts/YYYY-MM/（票据按月存档）"` — directly contradicts line 17 (which correctly says `Family/ledger.db` / `Family/receipts/`). Fix: delete line 19 entirely; line 17 is correct.
- **[HIGH] .codewhale/skills/Note_Keeper/SKILL.md:83** — `"数据存储在 data/ledger.db 的 notes 表中，与交易/文档共库。该文件已被现有备份配置覆盖（config.json backup.include），无需额外备份配置。"` — stale: notes are now per-member `data/<member>/notes/notes.db`, NOT in the shared ledger. Fix: describe per-member store via `paths.member_store(member,"notes")`; backup covers `data/` recursively.
- **[HIGH] .codewhale/skills/Document_Keeper/SKILL.md:13** — file tree line: `"├── doc_db.py ← SQLite CRUD & 到期查询（documents 表，建在共享 data/ledger.db）"` — stale: documents table is in `data/Family/ledger.db`. Fix: `"建在 data/Family/ledger.db（家庭共享账本）"`.

---

## MEDIUM — Misleading doc or comment (no runtime impact but confuses readers)

- **[MEDIUM] .codewhale/skills/Calendar_Keeper/cal_db.py:5** — docstring: `"表建在共享账本库 data/ledger.db（DB_PATH 来自 config.json db_path）。"` — stale: table now lives per-(member,domain) DB via `paths.member_store()`, not in a single `data/ledger.db`. Fix: describe per-member/domain store layout.
- **[MEDIUM] .codewhale/skills/Calendar_Keeper/cal_db.py:8** — docstring: `"与备忘不同：日程是家庭共享的（member 只记录创建者归属，不做可见性隔离）。"` — stale: calendar is now **per-member private**, not family-shared. Fix: `"日程按成员私有（每成员独立 schedule/tasks 库）"`.
- **[MEDIUM] .codewhale/skills/Note_Keeper/note_db.py:5** — docstring: `"表建在共享账本库 data/ledger.db（DB_PATH 来自 config.json db_path）。"` — stale: notes are now per-member `data/<member>/notes/notes.db`. Fix: describe per-member path via `paths.member_store`.
- **[MEDIUM] .codewhale/skills/Document_Keeper/doc_db.py:5** — docstring: `"表建在共享账本库 data/ledger.db（DB_PATH 来自 config.json db_path，经 doc_models）。"` — stale: table is in `data/Family/ledger.db` via `paths.family_ledger()`. Fix: `"表建在家庭账本 data/Family/ledger.db（经 paths.family_ledger()）"`.
- **[MEDIUM] .codewhale/skills/Agent_Runtime/SKILL.md:95** — `"图片消息：先存到按月子目录 receipts/YYYY-MM/（用 agent_core.receipt_month_dir()）"` — stale: incoming photos now go to sender's `data/<member>/inbox/YYYY-MM/` first. Fix: describe inbox staging via `member_inbox_dir()`.
- **[MEDIUM] .codewhale/skills/OCR/ocr.py:13,17** — docstring examples use `"receipts/2026-06/photo.jpg"` — stale path. Fix: `"Family/receipts/2026-06/photo.jpg"` or `"data/Family/receipts/2026-06/photo.jpg"`.
- **[MEDIUM] FamilyAssistant.md:14** — Calendar Keeper description: `"家庭日程与待办"` — stale: calendar is now per-member, not family-shared. Fix: `"按成员私有的日程与待办"`.
- **[MEDIUM] FamilyAssistant.md:78** — key files description: `"家庭日程/待办 + 远程日历同步 skill"` — stale for same reason. Fix: `"按成员私有的日程/待办 + 远程日历同步 skill"`.
- **[MEDIUM] data/consistency_inventory.md:25** — lists `"data/ledger.db", "data/members.json", "receipts", "documents"` as backup.include — stale. Fix: `"data", "config.json"`.
- **[MEDIUM] data/consistency_inventory.md:30–32** — lists `db_path`, `receipts_dir`, `documents_dir` as config top-level keys — stale (removed). Fix: list `data_root`, `family_dir_name`.
- **[MEDIUM] data/consistency_inventory.md:165–167** — config table repeats the three stale keys. Fix same as above.
- **[MEDIUM] data/consistency_inventory.md:249–251** — fixture names `doc_db_path`, `note_db_path`, `cal_db_path` listed without noting they now route through `paths`. Not strictly wrong but misleading. Fix: add note about paths resolution.
- **[MEDIUM] data/consistency_inventory.md:282–284** — .gitignore still shows `receipts/*` and `documents/*` entries that have been removed. Fix: update to current .gitignore.
- **[MEDIUM] docs/superpowers/plans/2026-06-11-document-keeper.md** — multiple lines (7, 26, 29, 182–183, 316, 1382–1383, 1656, 1663, 1782) describe the old layout (`data/ledger.db`, config keys `db_path`/`documents_dir`, root-level `documents/`). These are historical design/plan docs — not runtime, but misleading to future readers. Fix: add a banner at top noting these predate the 2026-06-19 per-member refactor, or update inline.
- **[MEDIUM] docs/superpowers/plans/2026-06-11-remote-backup.md** — multiple lines (33, 217, 272, 284, 338, 680, 684, 806) reference old `backup.include` `["data/ledger.db", "receipts", "documents", ...]`. Fix: add banner or update.
- **[MEDIUM] docs/superpowers/specs/2026-06-11-document-keeper-design.md** — lines 39, 41, 52, 77, 138, 155, 164 describe old layout (`data/ledger.db`, `documents_dir`, root-level `documents/`). Fix: add banner.
- **[MEDIUM] docs/superpowers/specs/2026-06-12-calendar-keeper-design.md** — lines 31, 38, 78 reference `data/ledger.db` and `.calendar_state.json` as primary state. Fix: add banner.
- **[MEDIUM] docs/superpowers/specs/2026-06-11-note-keeper-design.md** — lines 26, 48 reference `data/ledger.db` as storage. Fix: add banner.

---

## LOW — Cosmetic / inert (fallback defaults never reached at runtime, or test scaffolding)

- **[LOW] .codewhale/skills/Remote_Backup/backup_sync.py:38** — `_FALLBACK_CFG["include"]`: `["data/ledger.db", "receipts", "documents", "config.json"]` — inert: real config.json has `"include": ["data", "config.json"]`, which overrides. Fix: update fallback to `["data", "config.json"]`.
- **[LOW] .codewhale/skills/Calendar_Keeper/cal_db.py:34** — `DB_PATH = _ROOT / (_cfg.get("db_path") or "data/ledger.db")` — inert: CLI always resolves through `paths.member_store()`; this module-level default is never used at runtime. Fix: update comment or fallback to match new layout for clarity.
- **[LOW] .codewhale/skills/Note_Keeper/note_db.py:28** — `DB_PATH = _ROOT / (_cfg.get("db_path") or "data/ledger.db")` — same inert fallback as cal_db.py. Fix: same approach.
- **[LOW] tests/conftest.py:51** — `"the real data/ledger.db is never touched"` — still true (tests use temp DBs), but references the old single-DB name. Fix: `"the real data stores are never touched"`.
- **[LOW] tests/test_expense_tracker.py:4** — `"the real data/ledger.db is never touched"` — same as above.
- **[LOW] tests/test_remote_backup.py:145** — test fixture injects `"include": ["data/ledger.db", "receipts", "documents", "config.json"]` — functional for test isolation, but stale path values. Fix: update to `["data/ledger.db", "data/Family/receipts", "data/Family/documents", "config.json"]` or a minimal `data` tree.
- **[LOW] tests/test_remote_backup.py:200,214,267,271,345** — test assertions and setup reference `"data/ledger.db"`, `"receipts/"`, `"documents/"` as paths under fake ROOT — functional but stale. Fix: update to match new layout.
- **[LOW] tests/test_migrate_storage.py:75** — `state = tmp_path / ".calendar_state.json"` — tests the migration from old state; acceptable per audit scope exclusion.

---

## Resolution (2026-06-19, applied)

- **HIGH (8/8 fixed):** README.md (receipts/documents paths, privacy-paths line, dir tree),
  FamilyAssistant.md config-key table (`db_path`/`receipts_dir`/`documents_dir` → `data_root`/
  `family_dir_name` → `paths.py`) + Calendar row, Expense/Note/Document SKILL.md storage lines.
- **MEDIUM (fixed inline):** cal_db / note_db / doc_db module docstrings; Agent_Runtime SKILL.md
  inbox-staging line; OCR/ocr.py docstring path examples; FamilyAssistant.md Calendar entries;
  Calendar cli `--member` help. **Historical** plan/spec docs (5 files, pre-refactor) got a
  one-line stale banner at top rather than inline rewrites — they are point-in-time design records.
- **LOW (fixed/triaged):** backup_sync `_FALLBACK_CFG.include` → `["data","config.json"]`;
  cal_db/note_db inert `DB_PATH` defaults made honest (dropped the removed `db_path` config read,
  labelled legacy-fallback — only used if no `db_path` injected, which never happens at runtime).
  Test comments + `test_remote_backup` fixtures left as-is (functional under fake ROOT).
- **Not fixed (flagged):** `data/consistency_inventory.md` is gitignored (under `data/`) and
  entirely stale — should be regenerated, not hand-patched. Surfaced to the user.

Verification: full `pytest` suite green (272) after all edits.

## Summary

| Severity | Count |
|----------|-------|
| HIGH     | 8     |
| MEDIUM   | 17    |
| LOW      | 9     |
| **Total** | **34** |

### Confirmed clean (no stale references found):

- `config.json` — correctly has `data_root`, `family_dir_name`; old keys fully removed.
- `.codewhale/skills/Agent_Runtime/paths.py` — single source of truth, no stale references.
- `.codewhale/skills/Agent_Runtime/members.py` — correctly reads `members.json` `dir` + `sync` blocks.
- `.codewhale/skills/Calendar_Keeper/cli.py` — correctly resolves stores via `paths.member_store()`.
- `.codewhale/skills/Note_Keeper/cli.py` — correctly resolves DB via `paths.member_store(member, "notes")`.
- `.codewhale/skills/Expense_Tracker/models.py` — correctly uses `paths.family_ledger()` + `paths.family_dir() / "receipts"`.
- `.codewhale/skills/Document_Keeper/doc_models.py` — correctly uses `paths.family_ledger()` + `paths.family_dir() / "documents"`.
- `.codewhale/skills/Calendar_Keeper/calendar_sync.py` — correctly uses `paths.member_store()` + per-domain sync state.
- `.gitignore` — correctly updated; root-level `receipts/*` / `documents/*` entries removed.
- `.codewhale/skills/Calendar_Keeper/SKILL.md` — fully correct (describes per-member layout).
- `.codewhale/skills/Agent_Runtime/SKILL.md:102` — correctly describes new layout.

### Key patterns spotted:

1. **SKILL.md vs .py docstring asymmetry:** Several SKILL.md files were partially updated (e.g. Note_Keeper line 18 correctly describes `data/<成员目录>/notes/notes.db`) but the "存储" / "## 存储" section below still has the old description. A single old paragraph can poison an otherwise-correct doc.

2. **Module-level inert fallbacks:** `cal_db.py`, `note_db.py`, `backup_sync.py` all have old-path fallback defaults that are never reached because the CLI/env injection overrides them. These are low-risk but invite future mistakes if someone bypasses the CLI path.

3. **Historical plan/spec docs:** The `docs/superpowers/plans/` and `docs/superpowers/specs/` directories for pre-refactor skills (document-keeper, note-keeper, calendar-keeper, remote-backup) all describe the old layout. These are design artifacts, not runtime docs, but they mislead anyone reading them for current architecture. The 2026-06-19 design + plan files are correct and authoritative.

4. **`data/consistency_inventory.md` is entirely stale:** This auto-generated inventory was created before the refactor and reflects the old layout throughout. It should be regenerated or updated.
