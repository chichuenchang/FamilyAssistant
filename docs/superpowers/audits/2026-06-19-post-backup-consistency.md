# Post-Backup Consistency Audit

Date: 2026-06-19
Scope: live surfaces (code under `.codewhale/`, `README.md`, every `SKILL.md`, `config.json`,
`tests/`) after the per-member-backup + folder-mirror reworks and the `migrate_backup.py` removal.
(`docs/superpowers/specs|plans` are historical records — not audited.)

## Summary

1 High and 1 Low finding, both fixed in this pass; everything else clean. The High was a stray
build artifact accidentally committed; the Low was a routing-set asymmetry. No dead references to
removed code/files, no credentials tracked, agent whitelist correct, config schema consistent.

## Findings

| # | Check | File:line | Finding | Severity | Resolution |
|---|-------|-----------|---------|----------|------------|
| 1 | Tracked artifacts | `config.json.bak` (committed in `4d8ed5b`) | The migration's rollback snapshot of `config.json` was accidentally `git add`-ed alongside the legit config shrink. A stale, tracked duplicate (held the old `backup.include`/`remote_root`). | **High** | `git rm config.json.bak`; added `*.bak` to `.gitignore` so migration/rollback snapshots can never be committed again. |
| 2 | CLI routing symmetry | `agent_core.py:131` `_BACKUP_COMMANDS` | `backup-reorg` was missing from the command→skill routing set, while the other local-only backup command (`backup-restore`) was present. Cosmetic only — `_cli_path` is reached only for whitelisted commands, and `backup-reorg` is (correctly) not whitelisted, so it was never actually mis-routed. | **Low** | Added `"backup-reorg"` to `_BACKUP_COMMANDS`; extended `test_backup_commands_route` to assert it routes to `Remote_Backup`. |

## Checks that came back clean

- **Dead references** — no live hits for `migrate_backup`, `_iter_local_files`, or
  `_provider_ready` (removed). The `db_path` hits are legitimate `db_path=` test/db-layer kwargs,
  not the removed `config.json` key.
- **Removed config keys** — `config.json` has no `backup.include`, global `backup.remote_root`,
  `db_path`, `receipts_dir`, or `documents_dir`. `backup` is exactly `{enabled, debounce_seconds}`
  (committed, matches disk).
- **Credentials** — nothing matching `*creds*` / `*.bak` / `*.log` / `premigration` / `*.pyc` is
  tracked (besides the now-removed `config.json.bak`). Secrets remain env-only.
- **Agent whitelist** — `backup-restore` and `backup-reorg` are absent from `config.json`
  `wechat.allowed_commands`; the agent prompt documents `backup-restore` as local-only. Both stay
  un-callable by the agent.
- **Working tree** — `git status` clean; the migration's `config.json` shrink is committed
  (`4d8ed5b`), so disk == HEAD.
- **Tests** — full suite green (299) after the fixes.

## Notes (no action)

- The `README.md` `## 目录结构` tree is a simplified overview (omits e.g. `paths.py`,
  `image_gc.py`); acceptable for a README. `migrate_backup.py` is correctly absent.
- The `README.md` backup blurb ("换电脑 `backup-restore` 一键恢复") is a high-level summary; the
  authoritative per-member/bootstrap steps live in `## 换新机 / 灾难恢复`.

## Method note

A broad single-pass DeepSeek scan exceeded its turn budget without producing output (wrong tool
for wide cross-file auditing). The audit was then run directly with targeted `grep`/`git`
queries; future consistency passes should be scoped to one check per delegated worker.
