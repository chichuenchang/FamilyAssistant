# Per-Member / Family Storage Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Partition all user data under `data/` into one directory per family member (private notes/schedule/tasks, each its own store + per-member sync) plus a `Family` directory (shared ledger, receipts, documents), migrating existing data without duplicating Alex's Google-synced calendar.

**Architecture:** A new `paths.py` is the single source of truth for on-disk locations; `members.json` gains per-member `dir` + `sync` prefs. Storage repoints: Expense/Document → `Family/ledger.db`; Note_Keeper → `<Member>/notes/notes.db`; Calendar_Keeper → `<Member>/schedule/schedule.db` (events) and `<Member>/tasks/tasks.db` (tasks). Calendar sync becomes per-(member,domain) with provider selection from members.json; only Alex is wired to Google. A one-time reversible migration moves existing rows + image files and preserves Google `uid`/`synced`.

**Tech Stack:** Python 3 (stdlib only — sqlite3, urllib, pathlib), pytest. Windows/PowerShell host.

## Global Constraints

- Stdlib only; no new dependencies.
- Credentials (`GCAL_*`) read from `os.environ` only — never in config.json, members.json, code, or logs.
- Member names are PII → stay in gitignored `data/members.json`, never in git-tracked `config.json`.
- Every db-layer function keeps its `db_path=` parameter; CLI env-var test hooks retained (`CAL_DB_PATH`, `DOC_KEEPER_DB`, `CALENDAR_STATE_DIR`, `CALENDAR_CONFIG`, `BACKUP_STATE_DIR`, `BACKUP_CONFIG`).
- Sync stays silent, throttled, never-raising at the `calendar_tick` boundary; per member+domain failures isolated.
- Migration is reversible (up-front `.bak` snapshots) and must preserve `uid`, `synced`, `origin` on every schedule row exactly.
- Run tests from repo root with the `familyassis` conda env Python. Full `pytest` green before completion.

---

## File Structure

- Create `.codewhale/skills/Agent_Runtime/paths.py` — location resolver (sole source of truth).
- Create `.codewhale/skills/Agent_Runtime/migrate_storage.py` — one-time migration.
- Modify `.codewhale/skills/Agent_Runtime/members.py` — `member_dir_name`, `sync_pref`.
- Modify `config.json` — `data_root`, `family_dir_name`; drop `db_path`/`receipts_dir`/`documents_dir`; `backup.include`.
- Modify `data/members.json` — add `dir` + `sync` blocks.
- Modify `.codewhale/skills/Expense_Tracker/models.py` — default DB + receipts root via `paths`.
- Modify `.codewhale/skills/Expense_Tracker/cli.py` — receipt store dir via `paths`, rel-path linkage.
- Modify `.codewhale/skills/Document_Keeper/doc_models.py` + `cli.py` — Family ledger + documents root.
- Modify `.codewhale/skills/Note_Keeper/note_db.py` + `cli.py` — per-member DB via `paths`.
- Modify `.codewhale/skills/Calendar_Keeper/cal_db.py` — per-member+kind DB default.
- Modify `.codewhale/skills/Calendar_Keeper/cli.py` — resolve member+kind → store; member-scoped list.
- Modify `.codewhale/skills/Calendar_Keeper/calendar_sync.py` — per-(member,domain) state + provider registry + `calendar_tick` iteration.
- Modify `.codewhale/skills/Calendar_Keeper/calendar_provider.py` — register as events + tasks provider halves.
- Modify `.codewhale/skills/Agent_Runtime/agent_core.py` — member-scoped schedule context, relocate target, receipt/doc roots via `paths`.
- Modify `.codewhale/skills/Agent_Runtime/telegram_bot.py` + `wechat_ilink.py` — inbox path via `paths`.
- Modify `.codewhale/skills/Remote_Backup/backup_sync.py` — `.sync_state.json` hard-exclude.
- Modify `tests/conftest.py` + skill tests — per-member fixtures, repoint.
- Create `tests/test_paths.py`, `tests/test_migrate_storage.py`.

---

## Phase 1 — paths + resolvers + schema

### Task 1: `paths.py` resolver

**Files:** Create `.codewhale/skills/Agent_Runtime/paths.py`; Test `tests/test_paths.py`.

**Interfaces — Produces:**
- `data_root() -> Path`, `family_dir() -> Path`, `family_ledger() -> Path`
- `family_receipts_dir(dt: date|None=None) -> Path`, `family_documents_dir(doc_type: str) -> Path`
- `member_dir(member: str) -> Path`
- `member_store(member: str, domain: str) -> Path` (domain ∈ `schedule|tasks|notes`; returns the `.db` file)
- `member_store_dir(member, domain) -> Path`
- `member_inbox_dir(member, dt=None) -> Path`, `member_notes_image_dir(member, dt=None) -> Path`
- `member_sync_state(member, domain) -> Path`
- `to_rel(p: str|Path) -> str` (posix, relative to data_root), `resolve_rel(rel: str) -> Path`
- Reads `config.json` `data_root` (default `data`), `family_dir_name` (default `Family`). Honors `DATA_ROOT` env override for tests. Member dir name from `members.member_dir_name`.

**Steps:**
- [ ] Write failing tests: `family_ledger()` ends with `Family/ledger.db`; `member_store("Alex Lee","schedule")` ends with `Alex/schedule/schedule.db` (Alex via members `dir`); `member_store("Alex Lee","tasks")` → `Alex/tasks/tasks.db`; `member_store("Alex Lee","notes")` → `Alex/notes/notes.db`; `to_rel(resolve_rel("Family/receipts/2026-06/x.jpg"))=="Family/receipts/2026-06/x.jpg"`; unknown-member slug fallback `member_dir("New Person")` ends with `new`. Use `DATA_ROOT` env → tmp; monkeypatch members registry.
- [ ] Run, verify fail (module missing).
- [ ] Implement `paths.py`.
- [ ] Run, verify pass.
- [ ] Commit.

### Task 2: `members.py` dir + sync resolvers

**Files:** Modify `.codewhale/skills/Agent_Runtime/members.py`; Test `tests/test_members.py`.

**Interfaces — Produces:**
- `member_dir_name(name: str, members_path=None) -> str` → entry `dir` or `_slug(name)` (lowercase first token, sanitized).
- `sync_pref(name: str, domain: str, members_path=None) -> dict|None` → `{"provider":str,"enabled":bool}` or None.

**Steps:**
- [ ] Write failing tests against a temp members.json: `member_dir_name("Alex Lee")=="Alex"` (explicit dir); `member_dir_name("Sam Lee")=="Sam"`; missing dir → slug; `sync_pref("Alex Lee","schedule")=={"provider":"google_calendar","enabled":True}`; `sync_pref("Robin","schedule") is None`.
- [ ] Run, verify fail.
- [ ] Implement resolvers (tolerate non-dict entries → slug / None).
- [ ] Run, verify pass.
- [ ] Commit.

### Task 3: config.json + members.json schema

**Files:** Modify `config.json`, `data/members.json`.

**Steps:**
- [ ] Add `"data_root": "data"`, `"family_dir_name": "Family"` to config.json; remove `db_path`, `receipts_dir`, `documents_dir`; set `backup.include` to `["data","config.json"]`; update `calendar._comment` (enabled now master switch). 
- [ ] Add `"dir"` to all three members.json entries (`Alex`/`Sam`/`Robin`) and the `sync` block to Alex only (schedule→google_calendar, tasks→google_tasks, enabled true).
- [ ] Run `pytest tests/test_paths.py tests/test_members.py -q`; expected PASS (now reading real config/members defaults too).
- [ ] Commit.

---

## Phase 2 — repoint storage

### Task 4: Expense_Tracker → Family ledger + receipts root

**Files:** Modify `Expense_Tracker/models.py` (DB_PATH default, RECEIPTS_DIR), `Expense_Tracker/cli.py` (`_store_receipt` dest, rel-path return); Test `tests/test_expense_tracker.py`.

**Interfaces — Consumes:** `paths.family_ledger`, `paths.family_receipts_dir`, `paths.to_rel`.

**Steps:**
- [ ] Update test (or add): default `models.DB_PATH` resolves to `…/Family/ledger.db`; a stored receipt path round-trips through `paths.to_rel` (starts with `Family/receipts/`). Existing fixture tests keep passing `db_path=` (unchanged).
- [ ] Run, verify the new assertion fails.
- [ ] Implement: `DB_PATH = paths.family_ledger()`; `_store_receipt` writes under `paths.family_receipts_dir(when)` and returns `paths.to_rel(dest)`.
- [ ] Run `pytest tests/test_expense_tracker.py -q`; PASS.
- [ ] Commit.

### Task 5: Document_Keeper → Family ledger + documents root

**Files:** Modify `Document_Keeper/doc_models.py` (DB_PATH, DOCUMENTS_DIR), `Document_Keeper/cli.py` (`_store_file` dest, rel path); Test `tests/test_document_keeper.py`.

**Steps:**
- [ ] Add assertion: default `doc_models.DB_PATH` → `…/Family/ledger.db`; stored file rel path starts with `Family/documents/`.
- [ ] Run, verify fail.
- [ ] Implement via `paths.family_ledger()` / `paths.family_documents_dir(doc_type)` / `paths.to_rel`.
- [ ] Run `pytest tests/test_document_keeper.py -q`; PASS.
- [ ] Commit.

### Task 6: Note_Keeper → per-member DB

**Files:** Modify `Note_Keeper/note_db.py` (DB_PATH usage stays param-driven; add helper to resolve by member), `Note_Keeper/cli.py` (resolve `--member` → `paths.member_store(member,"notes")`); Test `tests/test_note_keeper.py`.

**Interfaces — Consumes:** `paths.member_store`, `paths.member_notes_image_dir`, `paths.to_rel`.

**Steps:**
- [ ] Add CLI-level test (subprocess or direct): `note-add --member "Alex Lee" …` writes to `Alex/notes/notes.db`, not the global ledger; `note-list --member "Alex Lee"` reads it back. Use `DATA_ROOT` env → tmp.
- [ ] Run, verify fail.
- [ ] Implement: CLI computes db_path from member via `paths`; pass through existing `db_path` plumbing. `note_db` unchanged except default fallback.
- [ ] Run `pytest tests/test_note_keeper.py -q`; PASS.
- [ ] Commit.

### Task 7: Calendar_Keeper → per-member + kind store

**Files:** Modify `Calendar_Keeper/cli.py` (resolve `--member` + `--kind` → store; list/done/delete scoped to member's two stores), `cal_db.py` (default DB fallback only); Test `tests/test_calendar_keeper.py`.

**Interfaces — Consumes:** `paths.member_store(member, "schedule"|"tasks")`.

**Steps:**
- [ ] Add test: `cal-add --member "Alex Lee" --kind event …` → row in `Alex/schedule/schedule.db`; `--kind task` → `Alex/tasks/tasks.db`; `cal-list --member "Alex Lee"` merges both stores. `DATA_ROOT`→tmp.
- [ ] Run, verify fail.
- [ ] Implement CLI store resolution by (member, kind); `cal-list` reads both member stores and merges by start time; `cal-done`/`cal-delete` look up the id across the member's two stores.
- [ ] Run `pytest tests/test_calendar_keeper.py -q`; PASS (sync tests may need Task 8 — keep those xfail/skipped until then if needed, noting it).
- [ ] Commit.

---

## Phase 3 — per-member / per-domain sync

### Task 8: provider registry + split contract

**Files:** Modify `Calendar_Keeper/calendar_provider.py` (expose `PROVIDER_NAME` halves / registry entry), add `Calendar_Keeper/providers.py` registry: `events_provider(name)` / `tasks_provider(name)` → module or None; Test `tests/test_calendar_keeper.py`.

**Interfaces — Produces:** `providers.get(domain, provider_name)` → object with the domain's functions (`is_configured` + events or tasks set), or None for unknown/local.

**Steps:**
- [ ] Write test: `providers.get("schedule","google_calendar")` exposes `list_events/create_event/delete_event/is_configured`; `providers.get("tasks","google_tasks")` exposes `list_tasks/create_task/complete_task/delete_task`; `providers.get("schedule","local") is None`.
- [ ] Run, verify fail.
- [ ] Implement registry mapping the two google names to `calendar_provider` (same module, different function subsets).
- [ ] Run, verify pass.
- [ ] Commit.

### Task 9: per-(member,domain) sync engine

**Files:** Modify `Calendar_Keeper/calendar_sync.py`; Test `tests/test_calendar_keeper.py`.

**Interfaces — Produces:**
- `refresh_domain(member, domain, db_path=None, provider=None, …) -> dict`
- `push_pending(member, domain, …)` scoped to one store+provider
- `calendar_tick(now=None)` iterates `members.registered_members()` × `{schedule,tasks}`; runs enabled+configured domains under per-domain throttle (state via `paths.member_sync_state`).
- `force_sync(member, domain)`, `status(member, domain)`.

**Steps:**
- [ ] Write tests with a fake provider injected: pushing a pending event in Alex's schedule store calls `create_event` and marks synced; a local-only member (Robin, no sync block) → `calendar_tick` skips (no provider) and never raises; per-domain state file written under `Robin/.../`-free path (i.e. not touched). Reuse existing sync test scaffolding/stubs.
- [ ] Run, verify fail.
- [ ] Implement: state file per (member,domain); provider from `members.sync_pref` via `providers.get`; reconciliation logic unchanged but scoped to the one store/kind. `calendar_tick` loops members×domains, isolates failures.
- [ ] Run `pytest tests/test_calendar_keeper.py -q`; PASS.
- [ ] Commit.

---

## Phase 4 — image/file paths, agent, transport, backup

### Task 10: agent_core member-scoped context + roots

**Files:** Modify `Agent_Runtime/agent_core.py`; Test `tests/test_agent_member.py`.

**Steps:**
- [ ] Update/add test: `_schedule_context(member="Alex Lee")` reads Alex's two stores (events+tasks) and is empty for a member with empty stores; `_relocate_note_image` for a member moves an inbox file under that member's `notes/YYYY-MM/` and returns a rel path. `RECEIPTS_DIR`/`DOCUMENTS_DIR`/`receipt_month_dir` route through `paths`.
- [ ] Run, verify fail.
- [ ] Implement: `_schedule_context(member)` (now takes member; called with current member in `handle`), reads `paths.member_store(member,"schedule"|"tasks")`; relocate target via `paths.member_notes_image_dir(member)`; receipts/doc roots via `paths`.
- [ ] Run `pytest tests/test_agent_member.py -q`; PASS.
- [ ] Commit.

### Task 11: transport inbox + backup excludes

**Files:** Modify `Agent_Runtime/telegram_bot.py`, `Agent_Runtime/wechat_ilink.py` (inbox via `paths.member_inbox_dir(member)`), `Remote_Backup/backup_sync.py` (add `.sync_state.json` to `_HARD_EXCLUDE_NAMES`); Test `tests/test_remote_backup.py`.

**Steps:**
- [ ] Update backup test: a `.sync_state.json` under a member dir is excluded from `_iter_local_files`; `data` dir walk includes `Family/ledger.db` and `Alex/schedule/schedule.db`.
- [ ] Run, verify fail.
- [ ] Implement transport inbox path (sender member known at download time) + backup hard-exclude.
- [ ] Run `pytest tests/test_remote_backup.py -q`; PASS.
- [ ] Commit.

---

## Phase 5 — migration + green suite

### Task 12: migration script

**Files:** Create `Agent_Runtime/migrate_storage.py`; Test `tests/test_migrate_storage.py`.

**Interfaces — Produces:** `migrate(old_ledger: Path, old_state: Path|None, dry_run=False) -> dict` (counts + moved-files report). CLI entry `python migrate_storage.py [--dry-run]`.

**Steps:**
- [ ] Write test: synthesize an old-shape `ledger.db` (notes for "Alex Lee"; schedule_items mix: remote member='' events+tasks with uid/synced=1, "Alex Lee" rows, "Robin" events, "爸爸" rows) + a `.calendar_state.json`. Run `migrate`. Assert: financial tables created empty in `Family/ledger.db`; notes in `Alex/notes/notes.db` with rewritten `source_image`; events in `Alex/schedule/schedule.db`, tasks in `Alex/tasks/tasks.db`; every migrated schedule row keeps its `uid` and `synced=1`; `爸爸` rows relabeled member `Alex Lee`; Robin events present in Alex's schedule store; Alex's two `.sync_state.json` seeded with `last_refresh`; `.bak` snapshot exists.
- [ ] Run, verify fail.
- [ ] Implement `migrate_storage.py` per spec §Migration. Idempotent (skip if target rows already present); transactional per store.
- [ ] Run `pytest tests/test_migrate_storage.py -q`; PASS.
- [ ] Commit.

### Task 13: conftest repoint + full suite

**Files:** Modify `tests/conftest.py` and any remaining skill tests still asserting old paths.

**Steps:**
- [ ] Repoint fixtures: keep per-`db_path` fixtures (they still work); add `data_root` tmp fixture setting `DATA_ROOT` env for CLI/path tests. Fix any test asserting `receipts/`/`documents/` literal roots.
- [ ] Run full `pytest -q`; expected PASS (0 failures).
- [ ] Commit.

### Task 14: run migration on real data

**Files:** runtime data only.

**Steps:**
- [ ] Confirm bot not running. Back up `data/ledger.db` (the script does this too).
- [ ] Run `python .codewhale/skills/Agent_Runtime/migrate_storage.py --dry-run`; review report.
- [ ] Run for real; verify with a read-only sqlite check: `Family/ledger.db` financial tables empty; `Alex/schedule/schedule.db` event count + all `synced=1`; `Alex/tasks/tasks.db` task count; `Alex/notes/notes.db` 7 notes; image files exist at new rel paths.
- [ ] Run `python .codewhale/skills/Calendar_Keeper/cli.py cal-list --member "Alex Lee"` → events+tasks render from new stores.
- [ ] Commit (data dir is gitignored; commit only code/docs already done — this step is verification, no code commit).

---

## Self-Review

- **Spec coverage:** layout (Tasks 1,4–7,10,11), per-member sync plumbed Alex-live (Tasks 8,9), events/tasks separate stores (Tasks 7–9,12), receipt→ledger rel linkage (Tasks 4,5,10), members.json dir+sync (Tasks 2,3), backup (Task 11), migration preserving uid/synced (Task 12,14), member-scoped agent context (Task 10). Covered.
- **Placeholder scan:** none — each task names exact files, interfaces, and concrete assertions.
- **Type consistency:** `paths.*`, `members.member_dir_name`/`sync_pref`, `providers.get`, `calendar_sync.refresh_domain`/`calendar_tick` names used consistently across tasks.
```
