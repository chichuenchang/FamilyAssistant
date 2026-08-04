# LLM Runtime Switch — Design

Date: 2026-08-04
Status: approved

## Goal

Let any registered chat user switch the agent's DeepSeek model (`deepseek-v4-flash` ↔
`deepseek-v4-pro`) and reasoning effort (`low`/`medium`/`high`/`max`) at runtime, per
user, without restarting the bot and without touching environment variables.

User decisions made during design:

- Interface: **slash commands** handled inline in `Agent.handle` (zero tokens,
  deterministic, same style as the existing `/clear`), not an LLM function tool.
- Persistence: **state file** `data/.llm_overrides.json`, survives restarts.
- Scope: **per-user** overrides (keyed by channel user id, same key as dialog history).
- Read pattern: **no file I/O on the message path** — the file is read once at `Agent()`
  construction and again only when a switch command is processed (merge-on-write).

## Current state

`Agent._call_llm` (`agent_core.py`) reads `DEEPSEEK_MODEL` (default `deepseek-v4-flash`)
and `DEEPSEEK_REASONING_EFFORT` (default `max`) from the process environment on every
call. Changing either requires editing env and restarting the transport process.

## 1. State

New state file `data/.llm_overrides.json` (via `paths.data_root()`, so tests get
`DATA_ROOT` isolation for free):

```json
{
  "wx_abc123": {"model": "deepseek-v4-pro", "effort": "high"},
  "tg_42": {"effort": "low"}
}
```

- Both keys optional per user; a user with no entry (or an empty entry) runs on
  env/default.
- Follows the existing state-dotfile convention (`.backup_state.json`,
  `.doc_reminder_state`, …): dotfile under `data/`, excluded from backup.
- Added to `_HARD_EXCLUDE_NAMES` in `Remote_Backup/backup_sync.py` so it is not synced.

## 2. Commands

Parsed inline in `Agent.handle`, right next to the existing `/clear` interception —
before any LLM call, so they cost zero tokens and work on every channel:

| command | effect |
|---------|--------|
| `/model` | show this user's effective model and whether it's override or env/default |
| `/model flash` / `/model pro` | set per-user model override (aliases for `deepseek-v4-flash` / `deepseek-v4-pro`; full IDs also accepted) |
| `/model reset` | clear this user's model override |
| `/effort` | show this user's effective effort, same provenance note |
| `/effort low\|medium\|high\|max` | set per-user effort override |
| `/effort reset` | clear this user's effort override |
| anything else | usage reply: `用法: /model [flash\|pro\|reset]` (resp. `/effort`) |

On set/reset:

1. Update the in-memory dict for this user.
2. Re-read `data/.llm_overrides.json` from disk, merge this user's entry into what was
   read (so a concurrent switch from the *other* transport process is not lost),
   atomic-write back (temp file + `os.replace`, same pattern as `members._save_members`).

The merge-on-write is the only cross-process coordination. Note the contention unit is
the *whole file*, not per-user entries: two switches landing in the same few milliseconds
across transports can still lose one update (the loser keeps it in memory until restart
and self-heals on its next switch). Accepted tradeoff — switch commands are rare and
human-typed, and user ids are channel-namespaced.

## 3. Resolution

`_call_llm` gains a `user` parameter (passed from `handle`/`handle_image`). Per call:

```
model  = override[user].model  or env DEEPSEEK_MODEL            or "deepseek-v4-flash"
effort = override[user].effort or env DEEPSEEK_REASONING_EFFORT or "max"
```

Overrides live in `self._llm_overrides` (a plain dict) loaded once in `Agent.__init__`;
missing/corrupt file → `{}` + log warning. No file access per message.

## 4. Error handling

- Unknown command argument → usage reply, no state change.
- Corrupt/missing state file at load → start with empty overrides, log warning.
- State file write failure → in-memory override still applies for this session, error
  logged; the chat reply notes the setting may not survive restart.
- Invalid values found in the state file (e.g. hand-edited) are ignored during load
  (validated against the allowed sets).

## 5. Testing

Extend the agent tests (existing `DATA_ROOT`-isolated style):

- Command parsing: set / show / reset / invalid for both `/model` and `/effort`.
- Per-user isolation: user A's `pro` does not affect user B.
- Resolution precedence: override > env var > default (monkeypatched env).
- Persistence: switch, construct a fresh `Agent`, override still effective.
- Full model IDs accepted; aliases map correctly.
- No LLM call made for switch commands (no API key needed to switch).

## 6. Docs

- `Agent_Runtime/SKILL.md`: document the new commands and the state file.
- `README.md`: one line in the feature/env section if warranted (env vars remain the
  boot-time defaults).
