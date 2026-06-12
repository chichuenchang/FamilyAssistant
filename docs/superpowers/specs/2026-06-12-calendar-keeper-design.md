# Calendar Keeper — Design

Date: 2026-06-12
Status: approved

## Purpose

Give the Family Assistant a family schedule: events/activities and tasks/todos, mirrored to the
user's remote calendar (Google Calendar + Google Tasks for this family). When a message arrives
from a known member, the agent silently refreshes the next-N-days window from the remote calendar
— no proactive announcements. The agent reports upcoming events / the task list only when asked,
and anything the agent creates on user demand is pushed to the remote calendar.

## Decisions

- **Remote is source of truth for schedule data** (unlike backup, where local wins): family members
  also edit Google Calendar directly from their phones. Local SQLite is a cache + offline buffer.
- **Tasks are real tasks**: events → Google Calendar API, todos → Google Tasks API (`@default`
  list). Both ride the same OAuth client/refresh token. No "todos as fake all-day events" hack.
- **Family-shared visibility**: one family calendar; items record the creating `member` for
  attribution but are visible to all members (calendar ≠ private notes).
- **Silent refresh, on known-member traffic only**: throttled pull (default every 15 min, window
  10 days) triggered by the transport layer after the member gate passes. No idle-loop polling,
  no proactive pushes to the user.
- **Provider is swappable**: `calendar_provider.py` implements a documented contract
  (same philosophy as `Remote_Backup/backup_provider.py`). Google implementation ships;
  other users rewrite that one file (CalDAV, Outlook, …) and the engine is untouched.
- **No private data in git**: credentials via env vars only (`GCAL_CLIENT_ID` /
  `GCAL_CLIENT_SECRET` / `GCAL_REFRESH_TOKEN`, optional `GCAL_CALENDAR_ID`, default `primary` —
  a calendar id is an email-like string, so it is env, not config). Schedule data lives in
  `data/ledger.db` (git-ignored, already in `backup.include`). `config.json` gains only
  non-private knobs. Defensive `.gitignore` entries for token files mirror Remote_Backup's.

## Architecture

New skill `.codewhale/skills/Calendar_Keeper/`:

- `cal_db.py` — storage layer on existing `data/ledger.db` (note_db pattern, idempotent
  `CREATE TABLE IF NOT EXISTS` on connect):

```sql
CREATE TABLE IF NOT EXISTS schedule_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- 'event' | 'task'
  uid TEXT DEFAULT '',             -- remote id; '' = never pushed
  title TEXT NOT NULL,
  start_at TEXT DEFAULT '',        -- ISO 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DD'; task due date; '' = undated task
  end_at TEXT DEFAULT '',
  all_day INTEGER DEFAULT 0,
  location TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  member TEXT DEFAULT '',          -- creator attribution ('' for remote-origin)
  status TEXT DEFAULT 'active',    -- active | done | cancelled
  origin TEXT DEFAULT 'local',     -- local | remote
  synced INTEGER DEFAULT 0,        -- 1 = remote state matches local
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

- `calendar_provider.py` — Google Calendar + Tasks implementation. Stdlib urllib only (project
  convention, zero new deps). OAuth refresh-token flow identical to backup_provider, scopes
  `calendar.events` + `tasks` (minimal: cannot manage calendars, only events/tasks).
  One-time `--auth` local-loopback flow prints the `setx GCAL_REFRESH_TOKEN` line.
  **Contract** (module docstring; errors raise RuntimeError; `is_configured()` false ⇔ env unset):
  - `is_configured() -> bool`
  - `list_events(time_min_iso, time_max_iso) -> list[dict]` — recurring events pre-expanded
    (`singleEvents=true`); dicts: `{uid,title,start,end,all_day,location,notes}`
  - `create_event(item: dict) -> uid`
  - `delete_event(uid)` — not-found is success
  - `list_tasks() -> list[dict]` — full `@default` list incl. completed/hidden;
    dicts: `{uid,title,due,notes,done}`
  - `create_task(item: dict) -> uid`
  - `complete_task(uid)` / `delete_task(uid)` — not-found is success
- `calendar_sync.py` — engine, the only caller of the provider:
  - `calendar_tick()` — transport layers call after the member gate passes. enabled + configured +
    last refresh ≥ `refresh_minutes` ago → `refresh()`. Never raises; errors recorded in
    `data/.calendar_state.json` (`last_refresh`, `last_error`). State dir overridable via
    `CALENDAR_STATE_DIR` (tests).
  - `refresh()` — **push pending, then pull**:
    1. Push: rows `synced=0` — active+no uid → create remote, store uid; done(task)+uid →
       complete remote; cancelled+uid → delete remote; then `synced=1`. Push failures leave the
       row pending (retried next tick); per-row isolation.
    2. Pull events in `[today, today+lookahead_days]` + all tasks. Upsert by uid: local row
       `synced=1` → overwrite with remote fields (**remote wins**); `synced=0` → skip (pending
       local change wins until pushed); unknown uid → insert `origin=remote, synced=1`.
    3. Reconcile deletions: synced active events with start in-window whose uid vanished from the
       pull → `cancelled`; synced active tasks with uid absent from full task list → `cancelled`;
       tasks reported `done=true` remotely → `done`.
  - `force_sync()` (tick without throttle), `push_pending()` (used by cal-add for immediate
    sync), `status()`.
  - Config from `config.json` `calendar` section (env `CALENDAR_CONFIG` overrides path for tests).
- `cli.py` — agent/local interface (`CAL_DB_PATH` env override for tests; write commands call
  Remote_Backup `mark_dirty`, Expense_Tracker pattern):
  - `cal-add --member M --kind event|task --title T [--date YYYY-MM-DD] [--start HH:MM]
    [--end HH:MM] [--all-day] [--location L] [--notes N]` — local insert, then best-effort
    immediate `push_pending()`; reports `已同步` / `待同步`.
  - `cal-list [--days N] [--kind K] [--member M] [--all]` — upcoming events (date order) + open
    tasks; `--all` includes done/cancelled.
  - `cal-done --id N` (task → done), `cal-delete --id N` (→ cancelled), both push best-effort.
  - `cal-sync` (force refresh now), `cal-status` (enabled/configured/last refresh/error/pending).
- `SKILL.md` — usage + Google setup guide (mirrors Remote_Backup's; may reuse the same Google
  Cloud OAuth client as backup, but needs its own `--auth` grant because scopes differ).

## Agent integration (`agent_core.py`)

- `_CAL_COMMANDS` routed to Calendar_Keeper in `_cli_path`; force-allowed like notes
  (`ALLOWED_COMMANDS |= _CAL_COMMANDS`) and listed in config whitelist for visibility.
- Seven tools: `add_event`, `add_task` (both in `_MEMBER_WRITE_TOOLS`), `list_schedule`,
  `complete_task`, `remove_schedule_item`, `sync_calendar`, `calendar_status`.
- **Context injection** `_schedule_context()`: in-process `cal_db` read of next-`lookahead_days`
  events + open tasks, one compact block appended to the system message. Empty → no block.
  Failure → never breaks `handle()` (notes pattern).
- System prompt: routing hints ("安排/某天有事/活动" → add_event; "待办/要做X" → add_task;
  "接下来有什么安排/待办清单" → answer from context or list_schedule; 完成/取消 → complete/remove)
  plus the **no-spam rule**: schedule context is for answering when asked — never proactively
  announce upcoming events.

## Transport hooks

`telegram_bot.py` / `wechat_ilink.py`: after `resolve()` returns a member (text and image paths),
call `calendar_tick()` (wrapped, never breaks message handling). This is the "known member or
channel" trigger; unregistered traffic never touches the calendar.

## Config (`config.json` `calendar` section, non-private)

`enabled` (code fallback false; this family's config true) / `lookahead_days` (10) /
`refresh_minutes` (15). Credentials and calendar id stay in env vars.

## Error handling

- Provider unconfigured → everything still works locally; items queue as pending (`待同步`).
- Remote failures → recorded in state file, surfaced via `cal-status`, retried next tick.
- `calendar_tick` / context injection / cal-add push are all best-effort: chat flow never breaks.

## Testing (`tests/test_calendar_keeper.py`)

- `cal_db` CRUD, status transitions, upsert-by-uid.
- Provider unit tests with faked `_http` (test_remote_backup pattern): event/task create payloads,
  list parsing (timed + all-day), delete 404-is-success, `is_configured` env matrix.
- Engine with monkeypatched provider: push-pending lifecycle (create/complete/delete), pull
  remote-wins overwrite, pending-local-wins skip, deletion/completion reconciliation, throttle
  honors `refresh_minutes`, unconfigured skip, provider exception → `last_error` set + never raises.
- Agent wiring: tool names in `TOOL_SCHEMAS`/`_TOOL_MAP`, member injection on add tools,
  `_schedule_context` formatting + empty case.
- No real names, ids, or tokens in test data.

## Out of scope

- Editing items (delete + re-add covers it), attendees/invitations, multiple calendars,
  per-member private calendars, reminders/notifications from calendar data (Document_Keeper owns
  proactive reminders), webhook push channels (poll-on-message only), purging old rows.
