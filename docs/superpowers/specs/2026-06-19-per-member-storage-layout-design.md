# Per-Member / Family Storage Layout — Design Spec

Date: 2026-06-19
Status: approved (Jim, standing approval through implementation)

## Problem

All user data currently lands in two flat trees plus one shared SQLite file:

- `data/ledger.db` — single DB holding **every** domain (transactions, deposits, transfers,
  tax, fx, notes, schedule/tasks, documents) for **all** members, distinguished only by a
  `member` text column.
- `receipts/YYYY-MM/` — incoming-photo inbox **and** expense receipts.
- `documents/<doc_type>/` — long-term documents; `documents/notes/YYYY-MM/` — note images.

Consequences: member-private data (notes) is only logically separated; calendar is one
shared family calendar bound to a single Google account; there is no way for different
members to sync their own schedule/tasks to their own remote; files for all members and the
family are intermingled under `documents/`.

## Goals

1. All user data under `data/`, partitioned into one directory per family member plus a
   `Family` directory.
2. Each member owns their **notes**, **schedule** (events), and **tasks** — physically
   separate stores, each able to sync to that member's own remote platform.
3. `Family/` holds shared financials (expenses/incomes/savings/transfers/tax/fx), the ledger,
   receipts/invoices, and long-term documents (family **and** member documents).
4. Ledger rows point to their original receipt image by a path that resolves reliably.
5. Keep the code flexible: per-member sync is plumbed now; only Jim is wired live (→ Google);
   other members are local-only until they configure their own provider.

Non-goals (YAGNI): building a second remote provider (Todoist/Outlook/etc.) now; per-member
private documents directory (documents stay in `Family/` by explicit user decision); syncing
notes to any remote (notes are local + backed up only).

## Decisions (confirmed with user)

- **Calendar model: per-member private.** Each member owns schedule+tasks and their own
  remote. There is no longer an auto-shared family calendar. All 41 existing schedule rows
  live on Jim's single Google account, so they all migrate to Jim's stores; Wenliang and
  Euphie start empty/local-only.
- **Sync scope: plumb per-member now, only Jim live.** Per-member sync state + provider
  selection are wired; Jim→Google works exactly as today; others are local-only. No second
  platform is implemented.
- **Events vs tasks: physically separate.** Events live in a `schedule` store, tasks in a
  `tasks` store, per member — each with its own sync state and provider selection.

## Target layout

```
data/
  Jim/
    schedule/  schedule.db  .sync_state.json      # kind='event'
    tasks/     tasks.db      .sync_state.json      # kind='task'
    notes/     notes.db      YYYY-MM/*.jpg          # member-private notes + images
    inbox/     YYYY-MM/*.jpg                         # incoming-photo staging (sender-scoped)
  Wenliang/  (same shape; empty stores until used)
  Euphie/    (same shape; empty stores until used)
  Family/
    ledger.db                # transactions, deposits, transfers, tax_filings, exchange_rates, documents
    receipts/  YYYY-MM/*      # receipt / invoice images
    documents/ <doc_type>/*   # long-term documents (family AND members)
  members.json               # registry (now also holds per-member dir + sync prefs)
```

`schedule.db`, `tasks.db`, `notes.db` each carry the existing schema for their one table
(`schedule_items` filtered to one `kind`, or `notes`). One table per file; no schema change
to the columns themselves.

## Components

### `paths.py` (new, in Agent_Runtime; importable by every skill)

Single source of truth for on-disk locations. Reads `config.json` for `data_root` (default
`data`) and `family_dir_name` (default `Family`). Functions:

- `data_root() -> Path`
- `family_dir() -> Path` and `family_ledger() -> Path` (`Family/ledger.db`)
- `family_receipts_dir(dt=None) -> Path` (`Family/receipts/YYYY-MM/`, created)
- `family_documents_dir(doc_type) -> Path` (`Family/documents/<doc_type>/`, created)
- `member_dir(member) -> Path` (`data/<dir>` from members.json `dir`, fallback slug)
- `member_store(member, domain) -> Path` where `domain ∈ {schedule, tasks, notes}` →
  the `.db` file path; `member_store_dir(member, domain)` → the containing dir.
- `member_inbox_dir(member, dt=None) -> Path` (`data/<dir>/inbox/YYYY-MM/`, created)
- `member_notes_image_dir(member, dt=None) -> Path` (`data/<dir>/notes/YYYY-MM/`, created)
- `member_sync_state(member, domain) -> Path` (`.sync_state.json` next to the store)
- `to_rel(abs_path) -> str` / `resolve_rel(rel) -> Path` — store file links relative to
  `data_root` so they survive cwd changes. `rel` form e.g. `Family/receipts/2026-06/x.jpg`,
  `Jim/notes/2026-06/y.jpg`.

Slug fallback: lowercase first whitespace-delimited token, filesystem-sanitized.

### `members.py` (extend)

- Per-member `dir` and `sync` fields added to the registry schema (see members.json below).
- `member_dir_name(name) -> str` → entry `dir` or slug(name).
- `sync_pref(name, domain) -> dict | None` → `{"provider": str, "enabled": bool}` or None
  (None ⇒ local-only).
- `registered_members() -> list[str]` already available via `member_names()`.

### config.json

- Add `"data_root": "data"`, `"family_dir_name": "Family"`.
- Remove `db_path`, `receipts_dir`, `documents_dir` (replaced by computed Family paths).
- `calendar` keeps engine knobs `lookahead_days`, `refresh_minutes`; the global `enabled`
  becomes a master kill-switch only (per-member enable lives in members.json). `_comment`
  updated.
- `backup.include` → `["data", "config.json"]`.

### members.json (gitignored — keeps names out of git)

```json
{
  "Jim Zheng": {
    "aliases": ["Jichun Zheng", "郑佶淳"],
    "wechat": ["…"],
    "dir": "Jim",
    "sync": {
      "schedule": { "provider": "google_calendar", "enabled": true },
      "tasks":    { "provider": "google_tasks",    "enabled": true }
    }
  },
  "Wenliang Li": { "aliases": ["李雯靓"], "dir": "Wenliang" },
  "Euphie":      { "aliases": ["Ruiyi Li", "李芮仪", "Euphemia"], "dir": "Euphie" }
}
```

No `sync` block ⇒ local-only. Credentials never live here — they stay in `GCAL_*` env vars.

### Storage repointing

- **Expense_Tracker** (`models.py`) and **Document_Keeper** (`doc_models.py`): default
  `DB_PATH` → `paths.family_ledger()`. Receipt/document file roots → `paths.family_*`.
- **Note_Keeper** (`note_db.py` / `cli.py`): the DB is selected by **member** →
  `paths.member_store(member, "notes")`. CLI already takes `--member`; it now resolves the
  per-member DB. Member isolation becomes physical (one file per member) on top of the
  existing `WHERE member=?` guard.
- **Calendar_Keeper** (`cal_db.py` / `cli.py`): DB selected by **member + kind** →
  `paths.member_store(member, "schedule")` for events, `…"tasks"` for tasks. `cal-add`
  takes `--member` + `--kind`; list/done/delete operate within the requesting member's
  stores. `cal_db` schema unchanged; each file holds a single `kind`.

The existing `db_path=` parameter on every db-layer function and the CLI env-var test hooks
(`CAL_DB_PATH`, `DOC_KEEPER_DB`, …) are retained — they make per-member path injection and
test isolation mechanical.

### Per-member / per-domain calendar sync

- `calendar_sync` becomes parameterized by `(member, domain)`:
  - State file: `paths.member_sync_state(member, domain)` instead of the global
    `data/.calendar_state.json`.
  - Provider: selected from `members.sync_pref(member, domain)["provider"]`. A small
    registry maps provider name → module implementing the relevant half of the contract.
  - `refresh_schedule(member)` pushes/pulls/reconciles **events** against the member's
    schedule store + schedule provider; `refresh_tasks(member)` does **tasks** likewise.
  - `calendar_tick()` iterates `registered_members()`; for each member+domain that is
    `enabled` and whose provider `is_configured()`, runs that domain's refresh under its own
    throttle. Today only Jim's two domains fire.
  - `push_pending` runs after a member's `cal-add/done/delete`, scoped to that member+domain
    store + provider.
- **Provider contract split.** The contract divides into an **events provider**
  (`is_configured`, `list_events`, `create_event`, `delete_event`) and a **tasks provider**
  (`is_configured`, `list_tasks`, `create_task`, `complete_task`, `delete_task`). The
  existing `calendar_provider.py` (Google) implements both halves and is registered under
  `google_calendar` (events) and `google_tasks` (tasks). It keeps reading `GCAL_*` env, so
  Jim's working setup is untouched. A second Google-using member would require namespaced
  env vars — documented as a future step, not built.

### Receipt → ledger linkage

`transactions.receipt_path`, `deposits.receipt_path`, `tax_filings.receipt_path`,
`documents.file_path`, `notes.source_image` all store a path **relative to `data_root`**
(via `paths.to_rel`). Readers resolve with `paths.resolve_rel`. New receipts/docs/notes are
stored under the Family/member trees and recorded in this rel form. (Existing rows: none for
financials/documents; 7 note images are rewritten during migration.)

### agent_core / transport / backup

- `agent_core._schedule_context` becomes **member-scoped**: it injects only the current
  member's upcoming schedule+tasks (read from that member's two stores), not a family-wide
  list. `RECEIPTS_DIR`/`DOCUMENTS_DIR` constants and `receipt_month_dir` route through
  `paths`. `_relocate_note_image` moves a sender's inbox image →
  `paths.member_notes_image_dir(member, …)` and records the rel path.
- `telegram_bot.download_photo` / `wechat_ilink` save incoming photos to
  `paths.member_inbox_dir(member, …)` as `<ts>_<channel>.jpg` (sender-scoped staging).
- `backup_sync`: `include` walks `data` recursively (already supported). Add
  `.sync_state.json` to `_HARD_EXCLUDE_NAMES` (regenerable; avoids cross-device drift).
  Per-member `.db` files are snapshotted by the existing `.db`-extension path. `members.json`
  remains backed up. Creds remain excluded by the `"creds"` rule.

## Migration (one-time, reversible)

Preconditions: **bot stopped** (otherwise it writes to old paths mid-migration).

A `migrate_storage.py` script (in Agent_Runtime), runnable once, idempotent:

1. Snapshot: copy `data/ledger.db` → `data/ledger.db.premigration.bak`; copy
   `data/members.json` → `.bak`; copy `data/.calendar_state.json` if present.
2. Build `data/Family/` and `data/<dir>/{schedule,tasks,notes,inbox}` trees.
3. `data/Family/ledger.db`: create the financial + document + fx schema; copy
   transactions/deposits/transfers/tax_filings/exchange_rates/documents rows from the old
   ledger (all currently empty → no-op, but coded generally). Rewrite any `receipt_path` /
   `file_path` to rel form.
4. Notes (7, all Jim): write to `data/Jim/notes/notes.db`; move each `source_image` file
   into `data/Jim/notes/YYYY-MM/`; rewrite `source_image` to rel form.
5. Schedule: **all 41 rows → Jim's stores** (they are all on Jim's Google). Split by `kind`:
   events → `data/Jim/schedule/schedule.db`, tasks → `data/Jim/tasks/tasks.db`. Preserve
   `uid`, `synced`, `origin`, `status`, timestamps **exactly** → zero re-push, zero Google
   duplicates. Relabel the `爸爸` test rows' `member` → `Jim Zheng`. Euphie's 3 events stay
   in Jim's schedule store (they exist on Jim's Google; member label preserved).
6. Seed `data/Jim/schedule/.sync_state.json` and `…/tasks/.sync_state.json` from the old
   global `.calendar_state.json` (`last_refresh` preserved so no immediate churn).
7. Rename old `data/ledger.db` → `…premigration.bak` (leave in place for verification); the
   running code reads only the new paths afterward.

Verification: row counts pre/post per table; assert every migrated schedule row retains its
`uid` and `synced=1`; assert moved image files exist at their new rel paths.

Rollback: restore the `.bak` files and revert config/members.json.

## Error handling

- All path helpers create parent dirs on demand (`mkdir(parents=True, exist_ok=True)`), as
  the current code already does.
- Sync stays silent + throttled + never-raising at the `calendar_tick` boundary; per
  member+domain failures are isolated (one member's outage cannot block another's).
- Local-only members (no provider configured) behave exactly like today's
  provider-unconfigured path: everything works locally, nothing is pushed.
- Migration is transactional per store and guarded by the up-front snapshot.

## Testing

- New unit tests for `paths.py` (resolution, rel round-trip, slug fallback) and the
  `members.py` dir/sync resolvers.
- `conftest.py` fixtures gain per-member store fixtures; existing skill tests are repointed
  from the single `db`/`*_db_path` fixtures to the new helpers (mechanical — they already
  pass `db_path`).
- Calendar sync tests extended to cover per-member selection, per-domain state files, and a
  local-only member (no provider → no-op).
- A migration test: synthesize an old-shape `ledger.db` + `.calendar_state.json`, run the
  migration, assert the Family/member split, `uid`/`synced` preservation, `kind` routing,
  `爸爸`→Jim relabel, and moved image files.
- Backup tests updated for `include: ["data", …]` and the `.sync_state.json` exclusion.
- Full `pytest` suite green before completion.

## Implementation order (phased, TDD)

1. `paths.py` + `members.py` resolvers + config.json/members.json schema.
2. Repoint storage: Family ledger (Expense/Document); per-member note + schedule + task DBs;
   CLI member→path resolution.
3. Per-member/per-domain sync: state, provider registry/split, `calendar_tick` iteration.
4. Image/file paths: receipts/documents/notes/inbox roots, rel-path linkage, agent_core
   member-scoped schedule context + relocate, transport inbox, backup include + excludes.
5. Migration script + its test; run on real data; full suite green.
```
