# Remote Drive Folder-Tree Mirror — Design Spec

Date: 2026-06-19
Status: approved (Jim, standing approval through implementation)

## Problem

The Google Drive backup stores every file **flat** inside the `remote_root` folder
(`FamilyAssistant`), with the file's path held only in `appProperties.rel` metadata. Browsing
the Drive shows one undifferentiated pile of files, not the local directory structure. The user
wants the remote to **mirror the local tree** — real nested Drive folders matching
`data/<Member>/…`, `data/Family/…`, `config.json`.

## Key insight

Under the `drive.file` OAuth scope the app sees **only its own files**, so the existing
`appProperties.rel` tag already locates any file regardless of how it is nested. Therefore the
engine's lookup operations need not change — only **where a new file is placed** changes, plus a
**one-time reorganizer** for the files already uploaded flat.

## Decisions (confirmed with user)

- **Layout: hybrid (rel-tag + real folders).** Keep `appProperties.rel` on every file as the
  engine's source of truth (robust, nesting-agnostic, single-query lookups); add nested-folder
  *placement* so the Drive browses as a mirror of local. Rejected: pure folder-tree (drop the
  tag, traverse folders for every op — more API calls, name-collision handling, no user-visible
  benefit).
- **Existing remote: reorganize in place (move).** Re-parent the current flat files into the new
  folder tree via Drive metadata moves (`addParents`/`removeParents`) — **zero byte re-upload**,
  preserving the existing data. A one-time, idempotent reorganizer does this.
- **Scope of change:** `backup_provider.py` (`GoogleDriveProvider`) + one CLI command. The
  `backup_sync` engine and the provider **contract** (`is_configured`, `upload`, `delete`,
  `list_remote`, `download`) are unchanged.

Non-goals (YAGNI): mirroring folders for a second (non-Google) provider; reconstructing `rel`
from the folder path in `list_remote` (the rel tag stays the source of truth); auto-running the
reorganizer inside `sync` (it is an explicit one-time command).

## Components

### `GoogleDriveProvider` — folder placement

The folder cache generalizes from "the single `remote_root` id" to a path→id map.

- `_ensure_folder_path(rel) -> str` (parent folder id):
  - Split `rel` into directory parts + basename. For `rel` with no `/` (e.g. `config.json`),
    the parent is `remote_root` (existing `_folder_id()`).
  - Starting from the `remote_root` id, for each directory part: query a child folder by
    `name == part and mimeType == folder and '<parent>' in parents and trashed=false`; if found
    use its id, else create it (`files.create` with folder mimeType + `parents=[parent]`).
  - Cache each cumulative path → folder id in the instance folder cache so repeat uploads in the
    same directory cost no extra lookups.
  - Return the leaf directory's folder id.
- `upload(local_path, remote_rel)`:
  - Resolve `existing = self._find(remote_rel)` (by `appProperties.rel`, unchanged).
  - On **create** (`existing is None`): `parents = [self._ensure_folder_path(remote_rel)]`
    (the leaf folder) instead of `[self._folder_id()]`; `name` = basename of `rel`;
    `appProperties.rel = rel` (unchanged).
  - On **PATCH** (existing found by rel): update content only — the file is already in the
    correct folder because its `rel` (hence target folder) has not changed. Do not re-parent.
- `_find(remote_rel)`: **drop the `'<remote_root>' in parents` clause** — a nested file is no
  longer a direct child of `remote_root`, so that clause would return nothing. Query by
  `appProperties has { key='rel' and value=<rel> } and trashed=false` alone; `rel` is unique
  within the app's files under `drive.file`. `delete` and `download` call `_find`, so they follow
  automatically with no further change.
- `list_remote()`: **drop the parent constraint** (same reason). Query
  `mimeType != '<folder>' and trashed=false` (under `drive.file` this is already limited to the
  app's own files) and keep the existing Python `appProperties.rel` extraction (folders carry no
  `rel` → skipped). Returns every backed-up file across the whole tree.
- **Isolation caveat:** dropping the parent scope means a single Google **account** hosting two
  members' `remote_root` folders would mix their files in these queries. That case is
  future-not-built — today each member uses their own `cred_prefix`/account, so the app's files
  are exactly one member's backup. If multi-member-same-account is ever built, re-add scoping via
  an `appProperties.root = remote_root` tag rather than `in parents`.

### `GoogleDriveProvider.reorganize() -> dict` — one-time re-parenter

- Page through all app files carrying `appProperties.rel` (reuse the `list_remote` query shape,
  but also fetch each file's `parents`).
- For each file: `target = _ensure_folder_path(rel)`. If the file's current parent ≠ `target`,
  call `files.update` with `addParents=target` and `removeParents=<current parent>` (metadata
  move, no bytes). Files already correctly nested are skipped.
- Idempotent: a second run finds everything already nested and moves nothing.
- Returns `{"moved": [rel, …], "skipped": int}`.
- Folders themselves are not tagged with `rel`; the query filters to files (`mimeType !=
  folder`) so the reorganizer never tries to move a folder.

### CLI — `backup-reorg --member NAME`

- New subcommand in `cli.py`, **local-only** (not added to `wechat.allowed_commands` /
  agent whitelist), mirroring `backup-restore`'s posture.
- Resolves the member's provider via `backup_sync._resolve` + `_make_provider`; requires the
  provider be configured; calls `provider.reorganize()` and prints `moved`/`skipped`.
- If the selected provider has no `reorganize` attribute (a future non-Google provider), print a
  graceful "provider 不支持重组" notice and exit 0.

## Data flow

1. Steady state: a write → `mark_dirty` → `backup_tick` → `sync(member)` → `upload(file, rel)`
   → `_ensure_folder_path` places the file in `remote_root/<dir parts>/basename` with the
   `rel` tag. Lookups/deletes/restore continue to use the tag.
2. One-time after deploy: `backup-reorg --member "Jim Zheng"` re-parents the existing flat files
   into the tree. Subsequent `backup-verify` is consistent; `backup-now` reports all-skips.

## Error handling

- Folder create/move failures raise `RuntimeError` (the engine already records the per-member
  `last_error` and retries the next round; the reorganizer surfaces the error to the CLI).
- The reorganizer is idempotent and safe to re-run after a partial failure.
- Manual user moves inside Drive do not break the engine: `appProperties.rel` remains the source
  of truth for lookups, so a hand-moved file is still found (its folder simply no longer matches
  — cosmetic). Documented as a caveat; users should not hand-reorganize.
- `drive.file` scope unchanged; creating folders and moving files stay within the app's own
  files. No new permissions.

## Testing

- `_ensure_folder_path`: builds a missing chain (query-miss → create each level), reuses cached
  ids on repeat, and maps a dir-less `rel` (`config.json`) to `remote_root`.
- `upload` nesting: a `rel` with directories creates the folder chain and parents the file to the
  **leaf** folder (assert the create call's `parents` is the leaf id, not `remote_root`); a
  dir-less `rel` parents to `remote_root`; the `appProperties.rel` tag is still set; a PATCH of an
  existing file does not re-parent.
- `reorganize`: a flat file (parent = `remote_root`, `rel` has dirs) is moved with
  `addParents=<leaf>` + `removeParents=<remote_root>`; an already-nested file is skipped; the
  result counts are correct.
- Query change: a `list_remote`/`_find` test proving they locate a file that is **not** a direct
  child of `remote_root` (nested), confirming the `in parents` clause was dropped. Existing
  `delete`/`download` cases stay green (they mock the HTTP layer and do not assert the dropped
  clause). The two existing `upload` tests are updated for the new placement (the create-path now
  resolves a folder first, so the mocked responses gain the folder query/create round-trips).
- CLI: `backup-reorg --member` resolves the member and invokes `reorganize`; unconfigured/missing
  member errors cleanly; a provider lacking `reorganize` is handled gracefully.
- Full `pytest` suite green before completion.

## Implementation order (phased, TDD)

1. `_ensure_folder_path` + instance folder-path cache (+ tests).
2. `upload` nested placement (+ updated upload tests).
3. `reorganize()` (+ tests).
4. `backup-reorg` CLI command (+ tests).
5. Full suite green; SKILL.md note on the folder-mirror layout + the one-time `backup-reorg` step.
