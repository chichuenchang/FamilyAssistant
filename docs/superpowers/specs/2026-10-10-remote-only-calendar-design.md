# Remote-only calendar

Drop local schedule cache. Agent reads/writes Google Calendar + Tasks directly, every op.

## Decisions

- Members without provider (Euphie, Wenliang Li): placeholder. Tools reply `错误: <member> 未配置远程日历`. Wire later via `members.json` sync block + credentials; no fallback.
- Item↔photo link dropped. Photo found via `chat_history` (`media` field, `Agent_Runtime/chat_archive.py`). Old 15 links dropped, no dump.
- Photo stays in inbox (no `relocate_image`), so archived `media` path stays valid.
- `image_gc`: prune month dirs `data/<member>/{inbox,schedule,tasks}/YYYY-MM` older than `image_retention_years` = 1. `notes/` untouched (notes DB links its images).
- No `schedule_context` injection: one Google call per message for nothing; prompt already forces `list_schedule`.
- Lost, accepted: offline buffer (Google down = op fails, no retry); cancelled events (remote delete) vanish from history, `--all` only adds done tasks.

## Units

| Unit | Change |
|---|---|
| `calendar_provider.py` | add `get_event(uid)`, `get_task(uid)` → normalized dict; 404/410 → `None`. Update merges onto it (PATCH sends title/location/notes whole). |
| `remote_schedule.py` (new) | `list_items(member, kind, start, end, closed)`, `add`, `update`, `complete`, `delete`, `status`. Item `{id, kind, title, start_at, end_at, all_day, location, notes, status}`. Provider via `provider_for(member, domain)` (moved from `calendar_sync`); none or `calendar.enabled` false → `NotConfigured`. |
| `cli.py` | `cal-add/list/update/done/delete/status` on `remote_schedule`. Drop `cal-sync`, verdict lines, `--source-image`, `CAL_DB_PATH`. `_edited_times` logic kept. |
| `agent_tools.py` | drop `schedule_context`, `calendar_tick`, `source-image` param + relocation, `calendar_status.sync`. Prompt: drop 校验 line + snapshot line. `IMAGE_ROUTES` drop source_image. |
| `image_gc.py` | dir-age prune above; drop `cal_db`. |
| `Daily_Banner/banner.py` | `gather` via `remote_schedule.list_items`; any exception → `stale=True`, empty lists. |
| Delete | `cal_db.py`, `calendar_sync.py`, `schedule_items` block in `migrate_storage.py`. |
| Config | drop `refresh_minutes`, `query_refresh_seconds`, `sync_horizon_days`, `sync_past_days`; `image_retention_years` 2→1. Update `config.json` comment, `FamilyAssistant.md` calendar row, `SKILL.md`, README tree. |

IDs: `e:<uid>` / `t:<uid>`. Agent copies verbatim.

## Listing

- Events: `list_events` over window. Default today..today+`lookahead_days`; `--from` absent → 1970 (`_window_iso` fixed-offset rule stays: naive `astimezone()` on pre-1970 raises `OSError [Errno 22]` on Windows).
- Tasks: `list_tasks()` all, filter client-side: due in window or undated; done only with `--all`.

## Errors

| Case | Output |
|---|---|
| `NotConfigured` | `错误: <member> 未配置远程日历`, exit 1 |
| provider `RuntimeError` | `错误: 远程日历调用失败: <msg>`, exit 1 |
| `get_*` → `None` | `[错误] 无此日程`, exit 1 |
| update done task | refuse (as now) |

## Tests

Keep `TestGoogleProvider` (+ `get_*`), `TestProviderRegistry`. New `TestRemoteSchedule` (in-memory `FakeProvider`), CLI in-process with `provider_for` patched. Update agent wiring, banner, image_gc, fence. Remove `TestCalDb`, sync engine, tick, verify/heal, source-image, migrate-schedule tests.

## Data cleanup (last, confirm first)

Check every row `synced=1`, then delete `data/Jim/{schedule,tasks}/*.db` + `.sync_state.json`. Snapshot 2026-10-10: schedule 514 rows, tasks 62, 0 unsynced.
