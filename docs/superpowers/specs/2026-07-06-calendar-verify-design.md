# Calendar Verify — Local↔Remote Consistency on Every Operation

Date: 2026-07-06
Status: approved

## Purpose

Today the calendar stack pushes and pulls, but nothing ever *compares* local and remote.
"已同步" verdicts come from the local `synced` flag; `cal-status` reports queue depth, not truth.
A push can fail silently, a family member can slash a task done directly on their phone's
Google Tasks, and the agent will confidently answer from a stale cache.

This change makes every remote-calendar-related operation query **both** local and remote,
check they agree, auto-heal divergence through the existing reconcile machinery, and report
the verdict. Not a new subsystem — a verification pass wired into the paths that already exist.

## Decisions

- **Verify on every calendar operation, unthrottled.** cal-add / cal-list / cal-done /
  cal-delete / cal-sync / cal-status all end with a local↔remote verify for the member's
  remote-enabled domain(s) touched by the operation. No throttle on the verify path (user
  requirement: *always*). Background `calendar_tick` keeps its own throttle unchanged.
- **Auto-heal + report.** On divergence, run the existing `refresh_domain` (push → pull →
  reconcile, remote wins), then re-verify. The user sees one verdict line: in-sync, healed,
  or still-divergent with details. Verify never blocks or undoes the primary operation.
- **User queries must hit the tool.** When the user asks about events/tasks ("what's my
  schedule", "my todos"), the agent must call `list_schedule` — which now verifies+heals —
  and must not answer from injected context alone. Rationale: users complete ("slash out")
  tasks directly on the remote calendar; injected context is a snapshot and can be stale.
- **cal-status becomes real.** It runs an actual verify per domain and reports true
  local=remote consistency, replacing the queue-count-only status. The system-prompt crutch
  "calendar_status 不是凭据" is retired.
- **CLI is the choke point.** Verification lives in `Calendar_Keeper` (engine + CLI), not in
  `agent_core` dispatch — covers both agent tool calls and manual CLI use. The agent runtime
  contributes prompt rules (relay verdicts, always-call-tool-on-query) and an updated
  `calendar_status` tool description only.
- **Freshness guard.** Rows touched within the last 60 s are exempt from mismatch verdicts
  *and* from the pull-reconcile cancel loops. Google's list API is not strictly
  read-after-write consistent; without the guard, the much higher pull frequency could
  flag — or worse, cancel — an event pushed moments ago.
- **Local mode stays silent.** Members/domains without a remote provider produce no remote
  calls and no verdict line. Test override (`CAL_DB_PATH`) skips verify, matching the
  existing no-real-push convention.

## Design

### 1. Engine — `Calendar_Keeper/calendar_sync.py`

New, both never raise:

```python
verify_domain(member, domain, *, db_path=None, prov=None, now=None) -> dict
verify_and_heal(member, domain, *, db_path=None, prov=None, now=None) -> dict
```

`verify_domain` — read-only compare:

- Provider `None`/unconfigured → `{"mode": "local"}` (caller prints nothing).
- Remote snapshot via the **existing** provider contract (no provider changes):
  schedule → `list_events(today .. horizon)` with the same horizon math as
  `refresh_domain` (`max(sync_horizon_days, lookahead_days)`); tasks → `list_tasks()`.
- Local snapshot: `cal_db.pending()` (filtered to the domain's kind) +
  `cal_db.synced_active(kind)`; events window-filtered by start-date exactly like the
  reconcile loop in `_sync_events`; tasks unfiltered.
- Diff buckets:
  - `pending` — local rows with `synced=0` (push failed or never ran).
  - `local_only` — local active rows with a uid that the remote snapshot lacks.
  - `remote_only` — remote uids absent from the local db (by `(kind, uid)`).
  - `drift` — same uid, differing core fields, normalized (strip strings, `all_day`
    as int, `''`/`None` equivalent). Events: title/start/end/all_day/location/notes.
    Tasks: title/due/notes **and done-state** — a task completed on the remote side
    while locally active is drift (this is the slashed-out-on-phone case; heal marks
    it done locally via the existing `upsert_remote` status mapping).
- Freshness guard: rows with `updated_at` newer than `_VERIFY_FRESH_SECONDS = 60`
  are exempt from `local_only`/`drift` verdicts.
- Returns `{"mode": "remote", "in_sync": bool, "pending": n, "local_only": [...],
  "remote_only": [...], "drift": [...], "local_total": n, "remote_total": n}`.
  Bucket entries carry id/uid + truncated title for human-readable verdicts.
- Any exception → `{"mode": "error", "error": str(e)}`.

`verify_and_heal` — `verify_domain`; if `in_sync` → return it. Otherwise
`refresh_domain(member, domain)` (existing push→pull→reconcile), re-verify, and return
the final verdict with `healed: bool` (true when the second pass is in-sync) plus the
heal's pushed/synced counts.

Guard in reconcile: the delete/complete cancel loops in `_sync_events` / `_sync_tasks`
skip rows whose `updated_at` is within the same 60 s guard (one-line condition each).
This protects the existing tick path too, and matters more now that pulls run per-op.

### 2. CLI — `Calendar_Keeper/cli.py`

Shared tail helper, called by every command with the member and the touched domain(s):

- cal-add / cal-done / cal-delete → the affected domain (`schedule` for events,
  `tasks` for tasks; cal-delete uses the found row's kind).
- cal-list → touched domains (both, or the `--kind`-selected one). Its current
  `sync_for_query` call is **removed** — verify subsumes it (a remote-new event is
  `remote_only` → heal merges it before the list is read). The
  `query_refresh_seconds` config knob is retired from this path; `sync_for_query`
  itself stays (kept for compat/tests — cal-list was its only production caller).
  Ordering: verify+heal first, then read local and print — so the listing reflects
  healed state.
- cal-sync → verify after `force_sync` (confirms the forced refresh actually
  converged).
- cal-status → per-domain verify verdict appended to the existing per-domain lines;
  this is now the authoritative "are we synced" answer.

Verdict lines (one per verified domain, printed after the primary output):

- in sync → `校验: 本地=远端一致（活动|待办）`
- healed → `校验: 发现不一致，已自动修复（推送N/拉取M）（活动|待办）`
- still divergent → `⚠ 校验: 本地≠远端（活动|待办）— 待推送2、远端缺失1（#12 游泳课…）、字段不一致1`
- verify errored → `校验失败（活动|待办）: <error>`
- local mode / `CAL_DB_PATH` override → no line.

The primary operation's output and exit code are never altered by verify failures.

### 3. Agent runtime — `Agent_Runtime/agent_core.py`

Prompt (`## 日程与待办` section) — no dispatch/code-path changes:

- Calendar tool results now end with 校验 verdict lines: relay the verdict briefly;
  when it says 仍不一致 (still divergent), tell the user plainly what's off — never
  hide it, never dump raw diff.
- **Query rule**: when the user asks about their schedule/events/tasks, always call
  `list_schedule` and answer from its (verified) output. Injected context is for
  passive topical relevance only — it may be stale because family members edit the
  remote calendar directly (e.g. completing tasks on their phone).
- Retire the "calendar_status 不是凭据" paragraph: `calendar_status` now performs a
  real local↔remote comparison and is trustworthy for "are we synced" questions.
  (The "did my add succeed" rule stays: add_event's own return remains the evidence
  for a specific new item.)
- Tool schema: `calendar_status` description → "查看日历同步状态并实时核对本地↔远端一致性";
  `list_schedule` description gains "（查询前自动与远端核对同步）".

### 4. Tests — `tests/`

Fake provider (existing conftest pattern):

- `verify_domain`: in-sync; each bucket (`pending`, `local_only`, `remote_only`,
  `drift` incl. remote-done task); freshness exemption; provider error → `mode=error`;
  local mode → `mode=local`.
- `verify_and_heal`: divergent → heal → in-sync (`healed=True`); heal insufficient
  (provider keeps failing) → still divergent verdict.
- Reconcile guard: a just-pushed row (fresh `updated_at`) missing from a lagging
  remote list survives the cancel loop; a stale one is still cancelled.
- CLI: each command prints the correct verdict line with a remote-enabled fake
  provider; healed path on cal-list picks up a remote-completed task and lists it
  done; `CAL_DB_PATH` override and local-mode members print no verdict.

### 5. Docs

- `Calendar_Keeper/SKILL.md`: 工作方式 gains the per-op verify bullet (replacing the
  查询前拉取 bullet's `sync_for_query` description); cal-status row updated; config
  note for `query_refresh_seconds` marked as background-path only.
- `FamilyAssistant.md` calendar row: mention per-operation local↔remote verification.

## Costs and limits

- In-sync operation: +1 remote list call per touched domain (cal-list without
  `--kind`: 2). Divergent: +1 heal cycle + 1 re-verify. Family-scale traffic is far
  inside Google API quotas.
- Latency: each verified op gains roughly one Google round-trip (~0.5–2 s).

## Out of scope

- Webhooks/push channels, event edit path, per-member GCAL credential namespaces,
  proactive announcements — all unchanged from existing boundaries.
- Context injection (`_inject` on every message) stays a local read; it is not an
  "operation" and must not hit the network per message.
