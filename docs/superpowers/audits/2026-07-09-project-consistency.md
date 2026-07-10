# Project consistency audit — 2026-07-09

Scope: three parallel sweeps — (A) README/FamilyAssistant.md vs repo, (B) all nine
SKILL.md vs their CLIs, (C) config.json / env vars / requirements / paths discipline.
Key findings spot-verified by hand. Test suite: 470 passing at audit time.

**Status: all findings fixed same day** (commit "fix: resolve consistency audit
findings"). During the fix a live bug surfaced beyond the audit text:
`agent_core._notes_context` / `_worksheets_context` called note_db/sheet_db without
db_path, so pinned-notes/worksheets context injection silently read the stray legacy
`data/ledger.db` (empty tables) instead of the member store — pinned items never
reached the LLM. Fixed by resolving `paths.member_store(member, "notes")`; the legacy
`DB_PATH = data/ledger.db` defaults were removed entirely (db_path now required,
raises ValueError). The stray `data/ledger.db` itself (pre-migration leftover; all 26
schedule items verified present in member stores; last touched 2026-06-20) was left
on disk — safe to delete manually. Regression tests: `tests/test_path_discipline.py`.

## Confirmed doc-vs-code mismatches

1. `.codewhale/skills/Web_Reach/cli.py:5` — usage header claims web-search uses
   "Jina s.jina.ai，无需 key". Actual (`reach.py:80-87`): s.jina.ai 401s keyless,
   code fetches DuckDuckGo HTML through r.jina.ai. SKILL.md and README describe
   the real behavior; only this docstring lies.
2. `.codewhale/skills/Any_Search/cli.py:5` — usage header says `--sub-domain`
   (hyphen). argparse defines `--sub_domain` / `--sub_domain_params` (underscore);
   the hyphen form is rejected. SKILL.md uses the correct form.
3. `.codewhale/skills/Agent_Runtime/SKILL.md:9-16` — directory tree omits
   `paths.py` and `migrate_storage.py`; both exist, are load-bearing, and are
   cited later in the same doc (line 103).
4. `Calendar_Keeper/SKILL.md:58,67` — `cal-list` documented with optional
   `--member`, and the example omits it. Production (no `CAL_DB_PATH` override)
   raises `按成员私有日历，需要 --member` (`cli.py:71`), exit 1.
5. `Expense_Tracker/SKILL.md:131,136` + `cli.py:84` docstring — claims
   receipt_path is project-root-relative and channel photos land in `receipts/`.
   Actual: stored value is data_root-relative (`Family/receipts/...`,
   `paths.to_rel` at cli.py:111), and transports save inbound photos to
   `data/<成员>/inbox/YYYY-MM/…` (telegram_bot.py:100, wechat_ilink.py:164).
   SKILL.md:17 states the correct form — the doc contradicts itself.
6. `Document_Keeper/SKILL.md:38` — field table says `file_path` stores
   `documents/<类型>/...`; actual is `Family/documents/<类型>/...`
   (data_root-relative). SKILL.md:20 has it right.
7. `Remote_Backup/SKILL.md:13` — tree line enumerates subcommands but omits
   `backup-reorg` (cli.py:138-154 has it; the SKILL's own CLI table at line 41
   documents it).
8. Minor: README.md top-level tree omits `FamilyAssistant.md`; Agent_Runtime
   SKILL.md:93-98 message-handling list doesn't yet mention WeChat quote
   injection (`_with_quote`, added today).

## Dependencies / env vars

9. `weixin-ilink` in neither requirements file — only a manual
   `pip install "weixin-ilink[qr]"` note in README. requirements-optional.txt's
   stated purpose covers exactly this kind of channel dep.
10. `numpy` imported directly (`Note_Keeper/chart.py:103`) but undeclared;
    installed only transitively via matplotlib.
11. Undocumented env overrides (used in code, absent from README/SKILL.md):
    `DATA_ROOT`, `DOC_KEEPER_DB`, `BACKUP_CONFIG`, `BACKUP_STATE_DIR`,
    `BACKUP_MEMBERS`, `CALENDAR_CONFIG`, `CALENDAR_STATE_DIR`,
    `IMAGE_GC_STATE_DIR`. Siblings `CAL_DB_PATH` / `NOTE_DB_PATH` are
    documented — inconsistent treatment.
12. No documented-but-unused env vars; config.json keys all have readers with
    defaults. Clean.

## Latent path bugs (code-vs-code)

13. Hardcoded `ROOT / "data"` bypassing `paths.data_root()` (which honors
    `DATA_ROOT` env + `config.data_root`): `telegram_bot.py:60` (offset file),
    `wechat_ilink.py:73` (creds), `agent_core.py:113` (debug log dir),
    `members.py:32` (circular-import caveat), `Document_Keeper/reminder.py:25`
    (no override at all), `backup_sync.py:71`, `calendar_sync.py:92`,
    `image_gc.py:47`. A non-default data_root strands these files in `./data`.
14. `backup_sync.py:61-64` re-implements data-root resolution from config but
    ignores `DATA_ROOT` env — same key resolves differently than paths.py.
15. Legacy defaults `DB_PATH = ROOT / "data" / "ledger.db"` in `cal_db.py:36`,
    `note_db.py:29`, `sheet_db.py:21` point at the pre-migration location;
    a call without `db_path` silently creates a stray old-layout DB.
16. Dead config loads: `note_db.py:19-26` and `cal_db.py:26-33` build `_cfg`
    but never read a key.

## Verified clean

README install/run instructions vs argparse; all CLI command tables and flags
(incl. recent `--member` on cal-done/delete/sync/status); config.json key
readers; agent tool whitelist claims (doc-remove / backup-restore / member-*
excluded); FamilyAssistant.md config-key table; OAuth scopes; matplotlib and
yt-dlp entries in requirements-optional.txt; tick wiring in both transports;
all referenced file paths.
