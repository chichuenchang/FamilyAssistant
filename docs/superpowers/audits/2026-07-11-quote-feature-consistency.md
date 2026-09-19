# Consistency audit — quote feature + requirements merge (2026-07-11)

Scope: last night's changes (WeChat quote resolution `94f18e1`, single-instance
lock, msg caches, requirements merge) vs the rest of the project.

## Findings & fixes

1. **backup_sync.py `_HARD_EXCLUDE_NAMES`** — new runtime-state files
   `data/wechat_recent_msgs.json` / `data/wechat_sent_msgs.json` were not
   excluded, violating the remote-backup rule "credentials and runtime state
   are never backed up" (2026-06-11 spec). **Fixed**: added both names.
2. **Agent_Runtime/SKILL.md:100** — quote bullet still described the dead
   `ref_msg` 摘要 mechanism ("SDK extract_text 约定"). **Fixed**: now documents
   msg_id cache + ±15s sent-time match + Telegram native `reply_to_message`.
3. **Agent_Runtime/SKILL.md:71** — runtime-state file list missing the two new
   cache files and the single-instance lock (port 47831). **Fixed**: added.
4. **wechat_ilink.py `_with_quote` docstring** — stale claim that the quoted
   title comes from the SDK convention. **Fixed**: points at `_quoted_text`.
5. **Channel parity: telegram_bot.py ignored `reply_to_message`** — WeChat got
   quote injection, Telegram (where the quoted full text is free in the update)
   did not. **Fixed**: `_tg_quoted_text` + `_with_quote` injection, same
   `[引用: …]\n` convention; `tests/test_telegram_quote.py` (7 cases).
6. **Requirements merge** — `requirements-dev.txt` + `requirements-optional.txt`
   → single `requirements.txt`; README tree updated. No other live references
   existed (historical plans/audits/specs intentionally untouched).

## Checked, no action

- New cache files are git-ignored (`git check-ignore` confirms).
- Commit `94f18e1` contains all quote-feature code + tests + design doc.
- Design doc (2026-07-09 spec) already corrected with measured wire facts
  (`ref_msg` carries only msg_id/时间戳; send response `{}`; poll stream does
  not echo BOT messages); Telegram parity section added.
- Telegram single-instance lock: not added — Telegram's `getUpdates` rejects
  concurrent pollers server-side (409), double-launch fails loudly on its own.
- Historical docs referencing the old requirements files are records of past
  work; rewriting them would falsify history.

## Verification

Full suite after fixes: **569 passed** (was 562 before Telegram parity tests).
