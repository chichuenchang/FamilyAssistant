# Per-Member Remote Backup — Design Spec

Date: 2026-06-19
Status: approved (Alex, standing approval through implementation)

## Problem

Remote backup is monolithic. One `backup_provider.py` reads a single fixed `GDRIVE_*`
credential set, mirrors the entire `data/` tree plus `config.json` to one `remote_root`
folder, and tracks everything in one global `data/.backup_manifest.json` +
`data/.backup_state.json`. Backup is all-or-nothing for the whole family.

The 2026-06-19 per-member storage refactor partitioned user data into `data/<Member>/`
(schedule, tasks, notes, inbox) and `data/Family/` (ledger, receipts, documents), and gave
**calendar sync** a per-member / per-domain provider model (members.json `sync` block,
provider registry, per-domain `.sync_state.json`). Backup did not get the same treatment.

## Goal

Each family member can back up to their **own** remote provider/account, selecting **what**
they back up. Provider, credentials, destination, and the file set are all per-member.

Concrete first case: Alex backs up to the existing Google Drive, covering the same data the
monolithic engine covers today (no coverage regression).

Non-goals (YAGNI): a second provider implementation (Dropbox/S3/etc.) — registry is built,
only `google_drive` ships; path-routed dirty tracking (mark-all-members is correct + cheap);
backup-side encryption (unchanged — relies on the cloud's at-rest encryption).

## Decisions (confirmed with user)

1. **Composition: explicit scope list per member.** Each member's `backup` block in
   members.json lists rel-path scope tokens. Selective on both axes (own data + family data);
   expresses "notes but not my schedule" and "documents but not ledger".
2. **Credentials: generic env-prefix, built now.** The backup block names `provider` +
   `cred_prefix` + `remote_root`. The provider resolves creds from `{cred_prefix}_*` env vars.
   Default prefix `GDRIVE` ⇒ Alex's existing setup works unchanged. The full multi-member
   mechanism is built now (not just plumbed).
3. **Scope tokens: rel-path prefixes.** Data-root-relative path roots (e.g. `Alex/notes`,
   `Family/documents`), reusing the rel-path convention the codebase already speaks. Filter =
   prefix match. `config.json` is a recognized infra alias (the one backup-worthy file outside
   `data/`).
4. **Engine: per-member mirror, one global debounce clock.** `backup_tick` loops registered
   members; each runs an isolated mirror `(provider, manifest, scopes)` with its own
   `last_sync`/`last_error`. A single global `data/.backup_state.json` holds the
   `dirty_since`/`last_write` debounce clock, so `mark_dirty()` stays arg-less, member-agnostic,
   and cheap (one write, no member enumeration). One member's provider failure is isolated to
   its own `last_error`; the global clock simply stays dirty and the next tick retries (cheap,
   sha-gated). Mirrors the calendar per-member precedent for the per-member parts.
5. **Alex's scope = full current coverage.** `["Alex", "Family", "members.json", "config.json"]`
   reproduces today's exact backup set (same rels), so Alex's existing Drive contents + manifest
   carry over with **zero re-upload**. No data-safety regression.

## Components

### `members.json` (gitignored — keeps names/creds-adjacent config out of git)

Optional `backup` block per member:

```json
"Alex Lee": {
  "aliases": ["Alex Lee", "安宁"],
  "wechat": ["…"],
  "dir": "Alex",
  "sync": { "schedule": {...}, "tasks": {...} },
  "backup": {
    "provider": "google_drive",
    "cred_prefix": "GDRIVE",
    "remote_root": "FamilyAssistant",
    "enabled": true,
    "scopes": ["Alex", "Family", "members.json", "config.json"]
  }
}
```

No `backup` block ⇒ member is local-only (not backed up). Credentials never live here — they
stay in `{cred_prefix}_CLIENT_ID/SECRET/REFRESH_TOKEN` env vars. `cred_prefix` defaults to
`GDRIVE` if omitted; `remote_root` defaults to the member's `dir` name if omitted.

### `config.json` `backup` block (git-tracked — subsystem knobs only)

```json
"backup": { "enabled": true, "debounce_seconds": 60 }
```

`enabled` = subsystem master kill-switch (per-member `enabled` gates each member on top).
`debounce_seconds` = shared settle window. `include` and `remote_root` are **removed** (now
per-member). Mirrors the calendar global-`enabled` vs per-member-enable split.

### `members.py` (extend)

- `backup_pref(name, members_path=None) -> dict | None` → the member's backup block normalized
  to `{"provider", "cred_prefix", "remote_root", "enabled": bool, "scopes": list[str]}`, or
  `None` when there is no block (local-only). Applies defaults (`cred_prefix="GDRIVE"`,
  `remote_root=member_dir_name(name)`, `enabled=False`, `scopes=[]`).

### Scope resolution (in `backup_sync`)

Token → ROOT-relative prefix (preserves the existing rel scheme, which is ROOT-relative posix,
e.g. `data/Alex/notes/x.jpg`, `data/members.json`, `config.json`):

- token `config.json` → `config.json` (recognized infra alias; the one file outside `data/`)
- any other token → `data/<token>` (`Alex` → `data/Alex`, `Family/documents` →
  `data/Family/documents`, `members.json` → `data/members.json`)

A walked file's ROOT-rel `r` is in a member's set iff some resolved prefix `p` satisfies
`r == p or r.startswith(p + "/")`. Hard-excludes (`creds`, `.backup_*.json`,
`.sync_state.json`, `.telegram_offset`, …) still apply on top. Load-time validation: each
token must resolve under ROOT; a token whose target is missing logs a warning (typo guard) and
contributes an empty set.

### Engine restructure — `backup_sync.py`

Generalize the single global engine into a per-member mirror over a shared debounce clock:

- **Global debounce clock stays global**: `data/.backup_state.json` keeps `{dirty_since,
  last_write}` exactly as today. `mark_dirty()` is **unchanged** — arg-less, member-agnostic,
  one write, never raises. All existing call sites (the two CLIs + transport store-image) and
  the dirty-hook tests keep working verbatim.
- **Manifest + sync-result move under the member**: `data/<Member>/.backup_manifest.json`
  (mirror tracking) + `data/<Member>/.backup_state.json` (`{last_sync, last_error}` only). Both
  names are already in `_HARD_EXCLUDE_NAMES`, so they are never self-backed-up even when
  `Family`/`<Member>` is in scope.
- **`backup_tick()` loops members**: if the subsystem is enabled, the global clock is dirty,
  and debounce has elapsed → for each member whose block is `enabled` and whose provider
  `is_configured()`, run that member's `sync()`. Per-member failures are caught and recorded in
  that member's `last_error`; the loop continues. After the loop, the global `dirty_since` is
  cleared only if every attempted member succeeded and no new write landed during the run;
  otherwise it stays dirty and the next tick retries (sha-gated → no redundant upload). Never
  raises.
- **`sync(member)` / `restore(member)` / `verify(member)` / `status(member=None)`** become
  member-scoped. Each resolves the member's provider instance, scope fileset, and per-member
  manifest/state paths. `sync` keeps today's semantics (sha-gated incremental upload + mirror
  delete of out-of-scope/removed remote files), scoped to that member's set. `status(None)`
  reports the global clock plus a per-member row list.

### Provider refactor — `backup_provider.py`

Module-level Google globals become a factory parameterized by `(cred_prefix, remote_root)`:

- `GoogleDriveProvider(cred_prefix, remote_root)` exposing the existing contract as methods:
  `is_configured`, `upload`, `delete`, `list_remote`, `download`.
- `is_configured()` checks `{cred_prefix}_CLIENT_ID/SECRET/REFRESH_TOKEN` (default `GDRIVE` ⇒
  Alex's existing env unchanged). Token/folder caches become per-instance.
- `remote_root` comes from the constructor (no longer reads `config.json`).
- `--auth` gains `--prefix` (default `GDRIVE`): a future member authorizes with
  `python backup_provider.py --auth --prefix WLI_GDRIVE` and the printed `setx` line uses the
  same prefix.

**Registry** in `backup_sync`: `{"google_drive": GoogleDriveProvider}`. The member block's
`provider` selects the class; the engine instantiates `cls(cred_prefix, remote_root)`. A new
backend later = new class + one registry entry; the engine is untouched. **Provider self-check
before commit**: no literal token/key in code; creds only via `os.environ`.

### CLI — `cli.py`

| Command | Behavior | Agent-callable |
|---|---|---|
| `backup-now [--member NAME]` | Sync now (ignore debounce). Default: all enabled members. | ✅ |
| `backup-status [--member NAME]` | Per-member rows: enabled/configured/dirty/last_sync/last_error/files. Default: all members with a block. | ✅ |
| `backup-verify [--member NAME]` | Manifest vs that member's remote. Default: all enabled members. | ✅ |
| `backup-restore --member NAME [--force] [--prefix P] [--remote-root R]` | Pull that member's remote → rebuild files + manifest. | ❌ local-only |

Restore provider config resolves from members.json if the member has a block; otherwise from
`--prefix`/`--remote-root` flags (fresh-device bootstrap). `wechat.allowed_commands` is
unchanged (the three read/now commands already present; restore stays local-only).

## Bootstrap / restore on a fresh device

members.json lives inside Alex's backup set, creating a chicken-and-egg: you need members.json
to know Alex's scopes, but members.json comes from Alex's backup. Resolution:

1. Clone repo, set Alex's three `GDRIVE_*` env vars.
2. `backup-restore --member "Alex Lee" --prefix GDRIVE --remote-root FamilyAssistant`
   (provider config from flags, no members.json needed) → recovers all of Alex's files,
   including `data/members.json` and `config.json`.
3. With members.json present, restore any other member normally.

## Migration (one-time, reversible) — `migrate_backup.py`

Preconditions: **bot stopped** (otherwise it writes state to old global paths mid-migration).

Idempotent script in Agent_Runtime:

1. Snapshot: copy `data/members.json` → `.bak`, `config.json` → `.bak`, and the global
   `data/.backup_manifest.json` / `.backup_state.json` if present → `.bak`.
2. Add Alex's `backup` block to members.json (provider `google_drive`, `cred_prefix` `GDRIVE`,
   `remote_root` `FamilyAssistant`, `enabled` true, scopes
   `["Alex", "Family", "members.json", "config.json"]`).
3. Rewrite `config.json` `backup` → `{enabled, debounce_seconds}` (drop `include`,
   `remote_root`).
4. Move the global manifest → Alex: `data/.backup_manifest.json` →
   `data/Alex/.backup_manifest.json`. The rel scheme is identical (ROOT-relative), so the next
   `sync` is a no-op diff → **zero re-upload, zero remote churn**. The global
   `data/.backup_state.json` (debounce clock) **stays in place** — it remains the shared clock.
   If the global manifest is absent (backup never ran), Alex simply starts empty → first sync is
   a full upload (expected).
5. Verify: assert `data/Sam/` and `data/Robin/` contain no files (they are empty after
   the storage refactor), so Alex's mirror will not delete anything unexpected from the remote;
   assert every relocated manifest rel still resolves to an existing in-scope local file.
6. Idempotent: re-running detects Alex's block + relocated files and no-ops.

Rollback: move the state files back, restore the `.bak` copies of config.json + members.json.

## Error handling

- Per-member isolation: a member's provider error is caught, recorded in that member's
  `last_error`, and the `backup_tick` loop proceeds to the next member.
- `mark_dirty()` never raises (unchanged contract) — backup problems never affect the write.
- No backup block, or subsystem/member `enabled=false`, or provider not configured ⇒ that
  member is skipped silently (exactly like today's unconfigured-provider path).
- Scope validation warns on a missing target and yields an empty set rather than crashing.
- Migration is guarded by the up-front snapshot and is idempotent.

## Testing

- `members.backup_pref`: block normalization, defaults, `None` when absent.
- Scope resolution: token→prefix mapping, `config.json` infra alias, prefix-boundary match
  (`Alex` must not match `data/Jimbo/…`), hard-excludes layered on top, missing-token warning.
- Provider factory: `{cred_prefix}_*` env read, `is_configured` true/false, default `GDRIVE`,
  per-instance caches, `--auth --prefix`.
- Engine: `mark_dirty` still writes the global clock (unchanged); `backup_tick` loops and
  isolates a failing member (member A provider raises → member B still syncs, A's `last_error`
  set, global clock stays dirty); per-member manifest/state file locations; `sync` mirror
  semantics scoped to one member.
- CLI: `--member` selection and default-all on now/status/verify; restore per-member +
  bootstrap via `--prefix`/`--remote-root`.
- Migration test: synthesize a global manifest/state + old config/members.json, run the
  migration, assert Alex's block added, config shrunk, state relocated with rels preserved,
  Sam/Robin empty assertion, and idempotent re-run.
- Existing backup tests repointed from the global state/manifest to Alex's per-member files.
- Full `pytest` suite green before completion.

## Implementation order (phased, TDD)

1. `members.backup_pref` + members.json/config.json schema + scope resolver (`backup_sync`).
2. Provider factory refactor `(cred_prefix, remote_root)` + registry + `--auth --prefix`.
3. Engine per-member: state/manifest relocation, `mark_dirty`-all, `backup_tick` loop,
   `sync/verify/restore/status` member arg, per-member isolation.
4. CLI `--member` on all four commands + restore bootstrap flags.
5. `migrate_backup.py` + its test; run on real data; full suite green.
6. SKILL.md update (per-member model, env-prefix setup, bootstrap/restore, migration).
