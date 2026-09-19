# Documents DB Split + Family Profiles — Design

Date: 2026-07-10
Status: Approved (Section 1 approved in conversation; blanket approval through implementation)

## Goal

1. Split the `documents` table out of `data/Family/ledger.db` into its own
   family-shared `data/Family/documents.db` — storage now splits by feature
   for documents; ledger.db returns to pure finance.
2. Establish the privacy rule explicitly: **only notes, tasks, and schedule
   are member-private.** Documents (and the new profiles) are family-shared.
3. Add a **family profiles** store (names, DOB, phone, email, address, LAP
   card numbers, …) in `documents.db`, shared read/write, auto-injected into
   every member's conversation. Personal facts like these belong here, not in
   member-private notes/worksheets.

## Storage layout (after)

```
data/Family/ledger.db      收支/定期/划转/报税/汇率（纯财务）
data/Family/documents.db   documents 表 + profiles 表（家庭共享，本次新增）
data/<member>/notes|tasks|schedule/  唯一的成员私有域（不变）
data/<member>/forms/       填表会话（私有，不变）
data/<member>/inbox/       来图暂存（不变）
```

## Changes

### paths.py

- `family_documents_db() -> Path` — `data/Family/documents.db`.
- Module docstring layout list updated.

### doc_models.py

- `DB_PATH = _paths.family_documents_db()` (was `family_ledger()`).
- `SCHEMA` gains the `profiles` DDL:

```sql
CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member     TEXT NOT NULL,
    field      TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(member, field)
);
```

- `member` is a registered member display name (e.g. "Jim Zheng") or the
  literal `"Family"` for household-level facts (address, shared income info).

### doc_db.py — auto-migration (idempotent, runs in init path)

On `init_db`/first connect to documents.db:

1. If documents.db has no `documents` rows AND `ledger.db` exists AND has a
   `documents` table with rows → copy all rows verbatim (schema identical),
   preserving ids.
2. After a successful copy, in ledger.db:
   `ALTER TABLE documents RENAME TO documents_legacy` (safety net; manual
   drop later).
3. Copy happens before rename; any failure aborts with a clear error and
   leaves ledger.db untouched.
4. Fresh installs: no ledger `documents` table → clean create, no migration.
5. Explicit `db_path` overrides (tests) skip migration entirely.

### Profiles API (doc_db.py)

- `set_profile(member, field, value, db_path=None)` — upsert, stamps
  `updated_at` (ISO). Empty value → ValueError.
- `unset_profile(member, field, db_path=None) -> bool`
- `list_profiles(member=None, db_path=None) -> list[dict]` — all or one
  member, ordered by member, field.

### CLI (Document_Keeper/cli.py)

- `profile-set --member-name <n> --field <f> --value <v>` — validates
  member-name is a registered member or `Family`; prints `✅ <n>.<f> = <v>`.
- `profile-unset --member-name <n> --field <f>` — prints removal or
  `[错误] 不存在`.
- `profile-list [--member-name <n>]` — grouped text listing.

Flag is `--member-name` (target of the fact), NOT `--member`, so it can never
collide with agent_core's sender-identity injection.

### Agent runtime (agent_core.py)

- Commands: `profile-set`, `profile-unset`, `profile-list` added to
  `_DOC_AGENT_COMMANDS` routing (Document_Keeper) and ALLOWED_COMMANDS.
- Tools: `set_profile_field(member_name, field, value)`,
  `remove_profile_field(member_name, field)`. No read tool — reads come from
  injection. NOT added to any member-injection set (target member is explicit
  data, sender identity irrelevant; profiles are shared-write by design).
- `_profiles_context()` — builds `## 家庭成员资料（全家共享）` block from
  `profile-list`-equivalent direct doc_db read (in-process, like
  `_notes_context`), appended to the system prompt for **every** member's
  conversation. Empty store → empty string. Failure → empty string, never
  breaks handle().
- System prompt behavior rules added:
  - Lasting personal facts (法定名/生日/电话/邮箱/住址/证件号等) →
    `set_profile_field`（member_name 填这条事实属于谁，家庭层面用 Family），
    不要存成备忘。
  - PDF 填表建议值优先取自 家庭成员资料 block（仍然逐字段问用户确认）。

### One-off data migration (executed during implementation, NOT shipped)

- Run auto-migration by touching doc CLI once (moves live documents rows).
- Script: read Jim Zheng's 「家庭成员信息」 worksheet → `profile-set` each
  entry with mapping: `Jim *` → member "Jim Zheng"; `Wenliang *` →
  "Wenliang Li"; `Euphie *` → "Euphie"; 家庭住址/2025年收入 → "Family";
  邮箱/电话 → "Jim Zheng". Then delete the worksheet (single source of truth).

## Error handling

- Migration failure: documents.db partial copy is rolled back (single
  transaction); ledger untouched; error message tells user to retry.
- `profile-set` with unregistered member-name → `[错误] 成员未登记: <n>（家庭
  层面请用 Family）`.
- Injection failure → log + empty block (matches notes/worksheet injection).

## Testing

- `tests/test_document_keeper.py` keeps passing unchanged (explicit db_path).
- New `tests/test_doc_migration.py`: old-style ledger.db with documents rows +
  empty documents.db → init → rows present in documents.db, ledger table
  renamed `documents_legacy`; idempotent second run; fresh install no-op;
  db_path override skips migration.
- New `tests/test_profiles.py`: CRUD upsert/unset/list; CLI subprocess
  round-trip incl. member validation; agent wiring (commands allowed & routed,
  tools registered, NOT in member-injection sets); `_profiles_context`
  contains values; system prompt mentions profile rules.

## Docs

- `Document_Keeper/SKILL.md`: documents.db location, profiles table, CLI.
- `README.md` / `FamilyAssistant.md`: storage layout + profiles feature
  blurbs; privacy split sentence updated (documents 家庭共享 → documents.db).
- `paths.py` docstring (layout list).
