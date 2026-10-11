# Remote-only calendar — plan

Spec: `docs/superpowers/specs/2026-10-10-remote-only-calendar-design.md`. Executor: inline, no subagents. Each task: failing test, code, `python -m pytest -q`, commit (6-word message, no co-author line). Baseline 1185 passed.

## Interfaces (fixed across tasks)

`calendar_provider`: `get_event(uid) -> dict | None` (shape of `list_events` item), `get_task(uid) -> dict | None` (shape of `list_tasks` item). 404/410/cancelled/deleted → `None`.

`remote_schedule` (new, `.codewhale/skills/Calendar_Keeper/remote_schedule.py`):
```python
class NotConfigured(Exception)          # str = "<member> 未配置远程日历"
CFG: dict                               # config.json calendar ∪ {"enabled": False, "lookahead_days": 10}
def provider_for(member, domain)        # moved from calendar_sync; also None when CFG enabled false
def ref(kind, uid) -> str               # "e:<uid>" / "t:<uid>"
def parse_ref(raw) -> tuple[str, str]   # strips "#"; bad prefix → ValueError
def list_items(member, kind=None, start="", end="", closed=False, undated=False) -> list[dict]
def get(member, ref) -> dict | None
def add(member, kind, title, start_at="", end_at="", all_day=False, location="", notes="") -> dict
def update(member, ref, fields: dict) -> dict      # merge onto get(); done task → ValueError
def complete(member, ref) -> dict                  # event ref → ValueError
def delete(member, ref) -> dict                    # returns item deleted
def status(member) -> dict[str, bool]              # {"schedule": configured, "tasks": configured}
```
Item: `{id, kind, title, start_at, end_at, all_day, location, notes, status}`; status `active|done`. Window rule (as old `cal_db.list_range`): `start_at[:10] <= end` and `(end_at or start_at)[:10] >= start`, empty bound = open; `undated` adds tasks without due. Events always fetched via `list_events(_window_iso(start or 1970-01-01), _window_iso(end or today+lookahead, True))`. Sort: dated by `start_at`, undated last. Provider `RuntimeError` propagates.

## Tasks

1. **Provider single-item get.** `calendar_provider.py` + `TestGoogleProvider` cases (timed, 404, cancelled; task 404, deleted). Commit `Add provider lookups for single items`.
2. **remote_schedule module.** New file + `tests/test_remote_schedule.py` with in-memory `FakeProvider` (stores dicts, implements full contract incl. `get_*`). Cases: not-configured member, disabled CFG, add event/task → ref, list window + undated + closed, update merge keeps untouched fields, update done task refused, complete event refused, delete returns item, unknown ref `None`, provider error propagates, pre-1970 window iso. Commit `Add remote schedule module over provider`.
3. **CLI rewire.** `cli.py` on `remote_schedule`; drop `cal-sync`, verdict lines, `--source-image`, `CAL_DB_PATH`, backup mark. Keep `_fmt_when`, `_edited_times`, `_shift`, `_parse_day`. `NotConfigured`/`RuntimeError` → `错误: …` stderr exit 1 (`远程日历调用失败: <msg>` for RuntimeError); missing → `[错误] 无此日程`. Replace CLI test classes in `test_calendar_keeper.py` with in-process tests (`provider_for` patched). Commit `Rewire calendar CLI onto remote schedule`.
4. **Agent tools.** `agent_tools.py`: drop `schedule_context`/`CONTEXT_FNS`, `calendar_tick`, `source-image` + `relocate_image`, `calendar_status.sync`, `cal-sync` from `COMMANDS`; prompt drops snapshot + 校验 lines, ids `e:…/t:…`; `IMAGE_ROUTES` no source_image, say photo stays findable via chat_history. Update `TestAgentWiring`, `tests/test_transport_base.py:229`, `tests/test_fence.py` (list_schedule via fake provider). Commit `Drop cache hooks from calendar tools`.
5. **image_gc by dir age.** Prune `data/<member>/{inbox,schedule,tasks}/YYYY-MM` where month end < today − retention; `config.json` `image_retention_years` 1; fallback 1. Rewrite `tests/test_image_gc.py`. Commit `Prune image month folders by age`.
6. **Banner.** `gather` via `remote_schedule.list_items` (events window day..end; tasks `undated=True`, no window); any exception → `stale=True`, lists empty; drop `_refresh`. Update `tests/test_daily_banner.py`. Commit `Banner reads schedule from remote calendar`.
7. **Delete cache.** Remove `cal_db.py`, `calendar_sync.py`, `cal_db_path` fixture, old test classes, `migrate_storage.py` schedule block + its tests, `paths.member_sync_state` + `schedule/tasks` in `_DOMAINS` (keep image dir helper on `member_dir`), config knobs + comment, `FamilyAssistant.md` row, `SKILL.md` rewrite, README tree. Grep `cal_db|calendar_sync|sync_past_days|source_image` in Calendar scope → 0. Commit `Delete local schedule cache and docs`.
8. **Data cleanup.** Assert all rows `synced=1`, delete `data/Jim/{schedule,tasks}/{*.db,.sync_state.json}`. `data/` gitignored → no commit.

## Review focus

- Multi-day all-day event edited: `_event_body` collapses to one day (pre-existing; unchanged, not fixed here).
- Recurring event instance id (`abc_20261010T…`) round-trips through `ref`/`parse_ref` (`:` split once).
- Agent passes bare old id `e12` → `parse_ref` must reject with clear message, not 404.
- Member with no sync block calls `list_schedule` → placeholder error, not crash/empty list.
- Banner with Google down still sends, flagged stale.
