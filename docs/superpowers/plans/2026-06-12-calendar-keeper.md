# Calendar Keeper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Family schedule (events + tasks) with silent remote-calendar sync (Google Calendar +
Google Tasks provider), agent tools, and context injection — per
`docs/superpowers/specs/2026-06-12-calendar-keeper-design.md` (data model, provider contract,
sync semantics, and tool list are locked there; this plan sequences the work).

**Architecture:** New skill `.codewhale/skills/Calendar_Keeper/` (storage → provider → engine →
CLI), then agent_core wiring, transport hooks, config/docs. Remote wins on pull; pending local
changes push first. All remote I/O best-effort — chat flow never breaks.

**Tech Stack:** Python 3.10 stdlib only (sqlite3, urllib), pytest.

---

### Task 1: Storage layer `cal_db.py`

**Files:**
- Create: `.codewhale/skills/Calendar_Keeper/cal_db.py`
- Test: `tests/test_calendar_keeper.py` (new), `tests/conftest.py` (add CAL_DIR sys.path + `cal_db_path` fixture)

- [ ] Write failing tests: add event/task returns id; `list_upcoming(days)` filters window,
      excludes done/cancelled, orders by date, includes undated open tasks; `set_status`
      (done/cancelled) flips `synced=0`; `upsert_remote` inserts new uid then overwrites
      synced rows but skips `synced=0` rows; `pending()` returns only `synced=0`.
- [ ] Run: `python -m pytest tests/test_calendar_keeper.py -v` → FAIL (module missing).
- [ ] Implement per spec schema (note_db pattern: `_connect` auto-creates table, every fn takes
      `db_path` override).
- [ ] Run again → PASS.

### Task 2: Google provider `calendar_provider.py`

**Files:**
- Create: `.codewhale/skills/Calendar_Keeper/calendar_provider.py`
- Test: append to `tests/test_calendar_keeper.py`

- [ ] Write failing tests (fake `_http` fixture, test_remote_backup pattern):
      `is_configured` env matrix; `list_events` parses timed (`dateTime`) + all-day (`date`)
      and paginates; `create_event` payload (timed start/end with timezone, all-day `date` with
      exclusive end); `delete_event` 404 → success; `list_tasks` maps
      needsAction/completed + due date; `create_task` payload; `complete_task` PATCH;
      RuntimeError on ≥300.
- [ ] Run → FAIL. Implement: OAuth refresh-token `_token()` + `_http()` + `--auth` loopback
      (backup_provider clone, scopes `calendar.events` + `tasks`), env
      `GCAL_CLIENT_ID/SECRET/REFRESH_TOKEN`, `GCAL_CALENDAR_ID` default `primary`.
- [ ] Run → PASS.

### Task 3: Sync engine `calendar_sync.py`

**Files:**
- Create: `.codewhale/skills/Calendar_Keeper/calendar_sync.py`
- Test: append to `tests/test_calendar_keeper.py`

- [ ] Write failing tests (monkeypatch provider fns + `CALENDAR_STATE_DIR` tmp + tmp db):
      push_pending creates remote + stores uid; pushes done→complete_task,
      cancelled→delete; pull upserts remote-wins / skips pending; reconcile: in-window event uid
      vanished → cancelled, task absent → cancelled, task done remotely → done;
      `calendar_tick` throttles by `refresh_minutes`, skips when disabled/unconfigured,
      records `last_error` and never raises on provider exception.
- [ ] Run → FAIL. Implement per spec (`CFG` from config `calendar` section with
      `CALENDAR_CONFIG` override; state file `data/.calendar_state.json`).
- [ ] Run → PASS.

### Task 4: CLI `cli.py`

**Files:**
- Create: `.codewhale/skills/Calendar_Keeper/cli.py`
- Test: append to `tests/test_calendar_keeper.py`

- [ ] Write failing tests (subprocess with `CAL_DB_PATH` + `CALENDAR_STATE_DIR` + unset GCAL env):
      cal-add event/task happy path prints id + `待同步`; cal-add requires member; cal-list
      shows items / `（无日程）`; cal-done flips task; cal-delete cancels; cal-status prints
      pending count.
- [ ] Run → FAIL. Implement six subcommands per spec (`_DB_OVERRIDE` env hook, `mark_dirty`
      on writes — Expense cli pattern, best-effort `push_pending` after writes).
- [ ] Run → PASS.
- [ ] **Commit:** `feat: Calendar Keeper core - schedule store, Google provider, sync engine, CLI`

### Task 5: Agent wiring `agent_core.py`

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` (`_CAL_COMMANDS` + `_cli_path`,
  7 tool schemas + `_TOOL_MAP`, `add_event`/`add_task` → `_MEMBER_WRITE_TOOLS`,
  `_schedule_context()` injection in `handle()`, system-prompt section with no-spam rule)
- Test: append to `tests/test_calendar_keeper.py`

- [ ] Write failing tests: 7 tool names in `TOOL_SCHEMAS` + `_TOOL_MAP`; `_apply_member`
      injects member for add_event/add_task; `_cli_path("cal-add")` → Calendar_Keeper;
      `_schedule_context` formats events+tasks and returns "" when empty.
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 6: Transport hooks + config + docs

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py` (tick after member gate),
  `.codewhale/skills/Agent_Runtime/wechat_ilink.py` (tick in handle_text/handle_image),
  `config.json` (`calendar` section + whitelist), `.gitignore` (defensive token lines),
  `README.md`, `FamilyAssistant.md`
- Create: `.codewhale/skills/Calendar_Keeper/SKILL.md`

- [ ] Wire `calendar_tick` (import-guarded, wrapped try/except).
- [ ] Add config section `{enabled, lookahead_days, refresh_minutes}` + cal-* whitelist entries.
- [ ] SKILL.md: usage, CLI table, Google setup (Calendar API + Tasks API, `--auth`), provider
      contract note, privacy boundary. README/FamilyAssistant.md feature rows + config table row.
- [ ] Full suite: `python -m pytest` → all green; `python -m py_compile` on touched files.
- [ ] **Commit:** `feat: agent schedule tools, silent calendar refresh on member messages, docs`

### Self-review checklist

- [ ] Every spec section maps to a task (storage/provider/engine/CLI/agent/transport/config/docs ✓)
- [ ] No real family data or credentials anywhere in code/tests/docs
- [ ] `git grep -i "refresh_token\|client_secret"` shows env-var reads only
