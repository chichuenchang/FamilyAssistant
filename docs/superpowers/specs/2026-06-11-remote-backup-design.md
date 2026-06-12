# Remote Backup — Design

Date: 2026-06-11
Status: approved

## Goal

Mirror all user data (ledger database, receipts, documents, config) to a remote drive so
the remote is a disaster-recovery copy and a bootstrap source for new devices. Git
handles the codebase; this skill handles user data. The skill is a tool invoked by
agents — it does nothing on its own.

User decisions made during design:

- **Engine real, provider stub** — sync engine (manifest, change detection, CLI, hooks)
  ships working and tested; only the remote-drive calls are a documented stub the
  user's own agent implements privately for their chosen drive.
- **Dirty-flag + debounced push** — writes mark dirty instantly; bot loops sync after
  ~60s of quiet; failures auto-retry; `backup-now` for manual.
- **True mirror with delete propagation** — file gone locally → deleted remotely.
  Drive-side trash/version history is the safety net.
- **Restore command** — `backup-restore` pulls everything from remote onto a new device.
- **Optional** — disabled by default; FamilyAssistant guides setup when the user asks.
- **Lean SKILL.md** — the skill is a callee; agent behavior is documented separately
  (FamilyAssistant.md + agent system prompt), and only after the skill itself is built.

## 1. Skill layout

```
.codewhale/skills/Remote_Backup/
├── SKILL.md            ← lean: CLI reference + provider contract + setup pointer
├── backup_sync.py      ← REAL engine: config, manifest, dirty flag, debounced tick,
│                          mirror sync, restore, verify
├── backup_provider.py  ← STUB: documented contract; all functions raise
│                          NotImplementedError (user's agent fills in)
└── cli.py              ← backup-now / backup-status / backup-verify / backup-restore
```

Self-contained, standard library only. Module names carry the `backup_` prefix for the
same reason Document_Keeper uses `doc_` — `cli.py` runs only as a subprocess, but
`backup_sync` is imported into shared processes (transports, pytest) where generic
names collide.

## 2. Config (config.json, single source of truth)

```json
"backup": {
  "enabled": false,
  "debounce_seconds": 60,
  "include": ["data/ledger.db", "receipts", "documents", "config.json"],
  "remote_root": "FamilyAssistant"
}
```

- `enabled: false` by default — the skill is optional. Hooks and ticks no-op when
  disabled.
- `include` lists files and directories relative to project root. Directories are
  walked recursively.
- Credentials and runtime state are **never** backed up: `data/wechat_creds.json*`,
  `data/.telegram_offset`, `data/.doc_reminder_state`, `data/.backup_*` are not in the
  include set, and the engine additionally hard-excludes `data/.backup_manifest.json` /
  `data/.backup_state.json` and any `*.creds*` path even if a user adds `data` wholesale.
- `remote_root` is the folder prefix on the remote drive.
- Whitelist additions: `backup-now`, `backup-status`, `backup-verify`.
  `backup-restore` stays local-only (overwrites local data — a compromised remote
  channel must not be able to trigger it).

## 3. Engine (backup_sync.py)

State files (both in `data/`, both excluded from backup):

- `.backup_manifest.json` — `{rel_path: {"sha256": str, "size": int, "uploaded_at": str}}`;
  what the engine believes is on the remote.
- `.backup_state.json` — `{"dirty_since": iso-ts|null, "last_write": iso-ts|null,
  "last_sync": iso-ts|null, "last_error": str|null}`.

Functions:

- `mark_dirty()` — set `dirty_since` (if unset) and `last_write` to now. Sub-millisecond,
  no network, never raises (errors are swallowed — a backup problem must never break a
  write).
- `backup_tick(now=None)` — called repeatedly by transports. Returns immediately unless:
  enabled AND provider configured AND dirty AND `now - last_write >= debounce_seconds`.
  Then runs `sync()`. Exceptions are caught, stored in `last_error`, dirty stays set
  (auto-retry on a later tick).
- `sync()` — walk the include set, hash every file (SHA-256), diff against manifest:
  - changed/new → `provider.upload(local, remote_rel)`
  - in manifest but gone locally → `provider.delete(remote_rel)` (true mirror)
  - update manifest after each successful operation (a mid-sync crash re-syncs only the
    remainder); clear dirty when the pass completes.
  - SQLite files are uploaded from a consistent snapshot: `sqlite3` backup API copies
    the db to a temp file, the temp file is hashed/uploaded.
- `restore(force=False)` — `provider.list_remote()`, then `provider.download()` each
  entry into place. Refuses to overwrite existing local data unless `force` (new-device
  bootstrap is the use case; an initialised ledger means probably the wrong machine).
  Rebuilds the manifest from what it downloaded.
- `verify()` — compare manifest against `provider.list_remote()`; report missing /
  extra / size-mismatched entries.
- `status()` — enabled? provider configured? dirty? last sync / last error / manifest
  size. Human-readable, used by CLI and agents.

## 4. Provider contract (backup_provider.py — the placeholder)

The one file the user's own agent implements, because drive choice and credentials are
private. Ships as a stub where every function raises
`NotImplementedError("backup_provider 未实现 — 见 Remote_Backup/SKILL.md 设置指南")`.

```python
def is_configured() -> bool          # False until implemented+credentialed
def upload(local_path: Path, remote_rel: str) -> None
def delete(remote_rel: str) -> None
def list_remote() -> dict[str, dict] # {rel_path: {"size": int, ...}}
def download(remote_rel: str, local_path: Path) -> None
```

- `remote_rel` paths are forward-slash relative paths; the provider prefixes
  `remote_root` itself (it reads config.json like every other module).
- Engine behavior with the stub: `is_configured()` returning False (or any call raising
  `NotImplementedError`) → status reports "provider 未实现", sync/tick no-op gracefully,
  nothing crashes. The skill is therefore safe to merge fully wired but unconfigured.
- The contract is documented in SKILL.md with enough precision that an agent can
  implement Google Drive / Dropbox / OneDrive / S3 / WebDAV without reading the engine.
- The stub lives in the repo. The user's private implementation replaces it locally;
  whether to commit it is the user's call — credentials must stay in environment
  variables either way, and the engine never logs them.

## 5. Hooks (who calls the skill)

- `mark_dirty()` after successful writes:
  - `Expense_Tracker/cli.py` — `add`, `delete`, `deposit-add`, `transfer-add`,
    `tax-add`, `fx-set`, `member-add`, `member-remove` (config.json is in the mirror set)
  - `Document_Keeper/cli.py` — `doc-add`, `doc-update`, `doc-ack`, `doc-remove`
  - Transports — after saving an incoming image (receipts/documents are files too)
- `backup_tick()`:
  - `telegram_bot.py` — once per poll iteration (next to the doc-reminder check)
  - `wechat_ilink.py` — in the existing background thread loop (next to the
    doc-reminder check)
- Both hook calls are wrapped so a backup failure can never break message handling or
  a CLI write (mark_dirty swallows internally; tick callers also try/except).

## 6. CLI

| Command | Behavior | Whitelisted |
|---------|----------|-------------|
| `backup-now` | Force `sync()` immediately, ignore debounce. Prints per-file actions + summary. | yes |
| `backup-status` | Print `status()`: enabled / configured / dirty / last sync / last error / file count. | yes |
| `backup-verify` | Print `verify()` report: in-sync, missing-remote, extra-remote, mismatched. | yes |
| `backup-restore [--force]` | Pull everything from remote into place (new device). Refuses over existing data without `--force`. | **no — local only** |

All commands exit 0 on success, 1 with a clear stderr message on error
(unimplemented provider, disabled, restore refusal).

## 7. Agent behavior (documented after the skill is built)

- FamilyAssistant.md gains a Remote Backup skill row (trigger words: 备份、同步、云盘、
  恢复数据) and loading-strategy line.
- Agent system prompt gains bullets: user asks "备份了吗/backup status" →
  `backup_status` tool; "立刻备份" → `backup_now`; backup questions when provider
  unimplemented → explain it's optional and offer setup.
- Setup flow (assistant-guided, local session): user says "set up backup" → the user's
  coding agent (CodeWhale/Claude) reads SKILL.md provider contract, implements
  `backup_provider.py` for the chosen drive with the user's credentials, sets
  `backup.enabled: true`, runs `backup-now` to seed, `backup-verify` to confirm.
  SKILL.md contains this checklist; FamilyAssistant just points at it.
- agent_core: `backup_now` / `backup_status` / `backup_verify` tools routed to the new
  CLI (same `_cli_path` routing extension as Document_Keeper).

## 8. Error handling

- Disabled or provider unimplemented: every entry point no-ops with a clear status;
  nothing raises into callers.
- Upload/delete failure mid-sync: manifest reflects completed operations only; dirty
  stays set; `last_error` recorded; next tick retries the remainder.
- Restore onto non-empty data without `--force`: refuse, exit 1, list what exists.
- Hash of unreadable/locked file: skip with warning, continue pass, keep dirty.
- Corrupt manifest/state JSON: treat as empty (full re-upload is safe — mirror is
  idempotent).

## 9. Testing

Provider faked in tests (in-memory dict implementing the contract):

- mark_dirty/tick: disabled → no-op; not configured → no-op; dirty + quiet → sync runs;
  dirty + recent write → waits; failure → dirty persists, retry succeeds.
- sync: new files uploaded, changed files re-uploaded (hash diff), unchanged skipped,
  local deletion propagates, manifest updated incrementally, exclusion list enforced.
- sqlite snapshot: db uploaded while connection open, content consistent.
- restore: empty target → downloads all + rebuilds manifest; non-empty target →
  refuses without force.
- verify: in-sync / missing / extra detection.
- CLI: status/now/verify exit codes with stub provider (unimplemented → exit 1 message).
- Hooks: write commands mark dirty (state file check); existing suite stays green.

## Out of scope

- Any real provider implementation (the point of the placeholder).
- Encryption before upload (drive-side at-rest encryption assumed; future option).
- Multi-device two-way sync / conflict resolution (mirror is one-way: local → remote;
  restore is explicit and manual).
- Backup versioning/history (drive's own version history covers it).
- Scheduled full re-verification.
