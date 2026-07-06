# Calendar Verify (Local↔Remote Consistency) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every remote-calendar operation queries both local and remote, checks they agree, auto-heals divergence via the existing reconcile machinery, and reports a verdict line.

**Architecture:** New read-only `verify_domain` + `verify_and_heal` in `Calendar_Keeper/calendar_sync.py` (reuses the existing 8-function provider contract — no provider changes). Every `cal-*` CLI command tails with a verdict line; `cal-status` becomes a real consistency check; `agent_core` gains prompt rules only (no dispatch changes). A 60-second freshness guard exempts just-touched rows from mismatch verdicts and from the pull-reconcile cancel loops (Google list API is not strictly read-after-write consistent).

**Tech Stack:** Python 3 stdlib only (sqlite3, datetime). pytest with the existing `FakeProvider` / `cal_db_path` / `member_engine` fixtures in `tests/test_calendar_keeper.py`.

**Spec:** `docs/superpowers/specs/2026-07-06-calendar-verify-design.md`

## Global Constraints

- Zero new external dependencies (project rule: Agent core and Calendar Keeper are stdlib-only).
- No credentials, member names, or channel ids in code or test data (use MemberA/Alex Lee placeholder registry patterns already in tests).
- Verify must NEVER block, alter, or fail the primary operation: verdicts are extra printed lines; exit codes unchanged.
- `verify_domain` / `verify_and_heal` never raise (return `{"mode": "error", ...}`), matching `calendar_tick` / `sync_for_query` philosophy.
- Local mode (member/domain without provider) and `CAL_DB_PATH` override (tests) → no remote calls, no verdict lines.
- All user-facing CLI strings are Chinese, matching existing output style.
- Comment style: Chinese, constraint-focused, matching existing files.
- Run tests from repo root: `python -m pytest tests/test_calendar_keeper.py -q` (conftest puts skill dirs on sys.path).
- Commit after every task. Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure

- Modify: `.codewhale/skills/Calendar_Keeper/cal_db.py` — add `uids()` (full local uid set; existing helpers can't answer "does local know this uid at all").
- Modify: `.codewhale/skills/Calendar_Keeper/calendar_sync.py` — freshness guard, `verify_domain`, `verify_and_heal`, guard in `_sync_events`/`_sync_tasks` cancel loops.
- Modify: `.codewhale/skills/Calendar_Keeper/cli.py` — `_verify_lines`/`_print_verify` + wire into all six commands; drop `sync_for_query` call from `cmd_cal_list`.
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` — prompt rules + two tool-schema descriptions.
- Modify: `tests/test_calendar_keeper.py` — all new tests + two aged-row fixes in one existing test.
- Modify: `.codewhale/skills/Calendar_Keeper/SKILL.md`, `FamilyAssistant.md` — docs.

---

### Task 1: `cal_db.uids()` — full local uid set

**Files:**
- Modify: `.codewhale/skills/Calendar_Keeper/cal_db.py` (append after `synced_active`, ~line 289)
- Test: `tests/test_calendar_keeper.py` (class `TestCalDb`)

**Interfaces:**
- Consumes: existing `cal_db` module internals (`_connect`).
- Produces: `uids(kind: str, db_path: Optional[str] = None) -> set[str]` — non-empty uids of ALL rows of that kind, any status/synced. Task 3's `verify_domain` uses it for the `remote_only` bucket.

- [ ] **Step 1: Write the failing test**

Append to `class TestCalDb` in `tests/test_calendar_keeper.py`:

```python
    def test_uids_returns_all_statuses_any_synced(self, cal_db_path):
        a = _add_event(cal_db_path)                      # synced=0, active
        cal_db.mark_synced(a, uid="e-1", db_path=cal_db_path)
        b = _add_task(cal_db_path)
        cal_db.mark_synced(b, uid="t-1", db_path=cal_db_path)
        cal_db.set_status(b, "done", db_path=cal_db_path)   # done + pending push
        _add_event(cal_db_path, title="无uid")               # uid='' → excluded
        assert cal_db.uids("event", db_path=cal_db_path) == {"e-1"}
        assert cal_db.uids("task", db_path=cal_db_path) == {"t-1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_keeper.py::TestCalDb::test_uids_returns_all_statuses_any_synced -q`
Expected: FAIL with `AttributeError: module 'cal_db' has no attribute 'uids'`

- [ ] **Step 3: Write minimal implementation**

Append to `.codewhale/skills/Calendar_Keeper/cal_db.py` after `synced_active`:

```python
def uids(kind: str, db_path: Optional[str] = None) -> set[str]:
    """该 kind 全部非空 uid（不限状态/synced）——校验判定"远端条目本地是否认识"用。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT uid FROM schedule_items WHERE kind = ? AND uid != ''",
        (kind,)).fetchall()
    conn.close()
    return {r["uid"] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_keeper.py::TestCalDb::test_uids_returns_all_statuses_any_synced -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Calendar_Keeper/cal_db.py tests/test_calendar_keeper.py
git commit -m "feat(cal_db): uids() — full local uid set per kind for verify

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Freshness guard + guarded reconcile cancel loops

**Files:**
- Modify: `.codewhale/skills/Calendar_Keeper/calendar_sync.py`
- Test: `tests/test_calendar_keeper.py` (class `TestSyncEngine` + one existing test updated)

**Interfaces:**
- Consumes: `cal_db.synced_active`, existing `_sync_events` / `_sync_tasks` / `refresh` / `refresh_domain`.
- Produces: `_VERIFY_FRESH_SECONDS = 60` module constant and `_is_fresh(updated_at: str, now: datetime) -> bool` in `calendar_sync`. `_sync_events(db_path, p, today, horizon, now)` and `_sync_tasks(db_path, p, now)` gain a trailing `now` parameter (internal functions; only `refresh` and `refresh_domain` call them). Task 3 reuses `_is_fresh`.

**Why existing-test edits:** `test_refresh_pulls_and_reconciles` creates rows and immediately expects the cancel/complete reconcile — those rows are now "fresh" and exempt by design. The test must age them; this is the intended behavior change, not a regression.

- [ ] **Step 1: Write the failing test + aged-row helper**

Add near the top of `tests/test_calendar_keeper.py`, after `_add_task` (~line 40):

```python
def _age(db, item_id, minutes=5):
    """把行的 updated_at 拨旧，绕过 60s 新鲜保护（校验/对账测试用）。"""
    import sqlite3
    from datetime import datetime
    old = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    c = sqlite3.connect(db)
    c.execute("UPDATE schedule_items SET updated_at = ? WHERE id = ?", (old, item_id))
    c.commit()
    c.close()
```

(`timedelta` is already imported at module top via `from datetime import date, timedelta`.)

Add to `class TestSyncEngine`:

```python
    def test_reconcile_spares_fresh_rows(self, engine):
        # Google list 读写有延迟：刚推送成功(updated_at 新鲜)的行在远端列表里
        # 暂时缺席时，绝不能被对账环节误取消。拨旧后才参与对账。
        fake, db = engine
        ev = _add_event(db, title="刚推送", start="2026-06-14T10:00", end="")
        cal_db.mark_synced(ev, uid="ev-lag", db_path=db)          # fresh
        tk = _add_task(db, title="刚推送待办")
        cal_db.mark_synced(tk, uid="t-lag", db_path=db)           # fresh
        fake.events = []                                          # 远端列表滞后
        fake.tasks = []
        result = calendar_sync.refresh(db_path=db, today=TODAY)
        assert result["errors"] == []
        assert cal_db.get_item(ev, db_path=db)["status"] == "active"   # 幸存
        assert cal_db.get_item(tk, db_path=db)["status"] == "active"
        _age(db, ev)
        _age(db, tk)
        calendar_sync.refresh(db_path=db, today=TODAY)
        assert cal_db.get_item(ev, db_path=db)["status"] == "cancelled"  # 拨旧后正常对账
        assert cal_db.get_item(tk, db_path=db)["status"] == "cancelled"
```

Update existing `test_refresh_pulls_and_reconciles` — after the four `mark_synced` calls and before `fake.events = [...]`, age the rows the reconcile must touch:

```python
        _age(db, gone)      # 新鲜保护豁免对账 → 拨旧，让远端删除生效
        _age(db, tk)
```

(`keep` and `future` need no aging — they are not cancelled by the test.)

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `python -m pytest tests/test_calendar_keeper.py::TestSyncEngine -q`
Expected: `test_reconcile_spares_fresh_rows` FAILS (fresh rows get cancelled — guard not implemented); `test_refresh_pulls_and_reconciles` still PASSES (aged rows reconcile as before).

- [ ] **Step 3: Implement guard**

In `.codewhale/skills/Calendar_Keeper/calendar_sync.py`:

Add after `_DOMAIN_KIND = {...}` (~line 50):

```python
# 校验/对账的新鲜保护窗：updated_at 距今不足此秒数的行免判。
# Google list API 非严格读写一致——刚推送的行可能短暂缺席远端列表，
# 无此保护会被误判"远端缺失"甚至被对账环节误取消。
_VERIFY_FRESH_SECONDS = 60


def _is_fresh(updated_at: str, now: datetime) -> bool:
    """行的 updated_at 在保护窗内 → True（解析失败按不新鲜处理）。"""
    try:
        return (now - datetime.fromisoformat(updated_at)).total_seconds() \
            < _VERIFY_FRESH_SECONDS
    except (TypeError, ValueError):
        return False
```

Change `_sync_events` signature and cancel loop (currently `def _sync_events(db_path, p, today, horizon)`):

```python
def _sync_events(db_path, p, today: date, horizon: date,
                 now: datetime | None = None) -> tuple[int, list[str]]:
    """活动半：拉窗口活动，按 uid 合并（remote wins）；窗口内远端消失 → 本地取消。

    新鲜保护：updated_at 在 _VERIFY_FRESH_SECONDS 内的行不参与"远端消失→取消"
    对账（Google list 读写延迟会让刚推送的行短暂缺席）。
    """
    now = now or datetime.now()
```

and in its cancel loop change:

```python
        for row in cal_db.synced_active("event", db_path=db_path):
            d = row["start_at"][:10]
            if d and t_iso <= d <= h_iso and row["uid"] not in seen \
                    and not _is_fresh(row["updated_at"], now):
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
```

Change `_sync_tasks` the same way (currently `def _sync_tasks(db_path, p)`):

```python
def _sync_tasks(db_path, p, now: datetime | None = None) -> tuple[int, list[str]]:
    """待办半：全量拉，按 uid 合并；远端完成 → 本地完成，远端消失 → 本地取消。

    新鲜保护同 _sync_events：刚推送的行免于"远端消失→取消"误判。
    """
    now = now or datetime.now()
```

and its cancel loop:

```python
        for row in cal_db.synced_active("task", db_path=db_path):
            if row["uid"] not in seen and not _is_fresh(row["updated_at"], now):
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
```

Update the two call sites to pass `now`:

In `refresh` (~line 230):
```python
    n_events, e1 = _sync_events(db_path, p, today, horizon, now)
    n_tasks, e2 = _sync_tasks(db_path, p, now)
```

In `refresh_domain` (~line 266):
```python
    if domain == "schedule":
        n, e = _sync_events(db_path, p, today, horizon, now)
    else:
        n, e = _sync_tasks(db_path, p, now)
```

- [ ] **Step 4: Run the whole calendar suite**

Run: `python -m pytest tests/test_calendar_keeper.py -q`
Expected: ALL PASS (new guard test + updated reconcile test + everything else).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Calendar_Keeper/calendar_sync.py tests/test_calendar_keeper.py
git commit -m "feat(calendar): 60s freshness guard — reconcile spares just-pushed rows

Google list API is not read-after-write consistent; per-op verify raises
pull frequency, so an unguarded cancel loop could cancel an event pushed
moments ago.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `verify_domain` — read-only local↔remote compare

**Files:**
- Modify: `.codewhale/skills/Calendar_Keeper/calendar_sync.py` (append after `sync_for_query`)
- Test: `tests/test_calendar_keeper.py` (new class `TestVerifyDomain`)

**Interfaces:**
- Consumes: `cal_db.pending`, `cal_db.synced_active`, `cal_db.uids` (Task 1), `_is_fresh` (Task 2), `provider_for`, `CFG`, `_DOMAIN_KIND`, `_paths.member_store`.
- Produces:
  - `verify_domain(member: str, domain: str, *, db_path=None, prov=None, now: datetime | None = None) -> dict` — never raises. Returns `{"mode": "local"}` | `{"mode": "error", "error": str}` | `{"mode": "remote", "in_sync": bool, "pending": list[str], "local_only": list[str], "remote_only": list[str], "drift": list[str], "local_total": int, "remote_total": int}`. Bucket entries are human-readable strings like `"#12 游泳课"`.
  - `_norm(v) -> str` and `_fields_differ(kind: str, row: dict, remote: dict) -> bool` helpers (Task 4/5 don't call these directly, but tests may).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_calendar_keeper.py` after `class TestSyncForQuery`:

```python
class TestVerifyDomain:
    """只读校验：查本地+远端，分桶报告差异。永不抛。"""

    def _v(self, fake, db, domain="schedule", **kw):
        return calendar_sync.verify_domain("MemberA", domain,
                                           db_path=db, prov=fake, **kw)

    def test_in_sync_event(self, engine):
        fake, db = engine
        ev = _add_event(db, title="游泳课", start="2026-06-14T10:00",
                        end="2026-06-14T11:00")
        cal_db.mark_synced(ev, uid="ev-1", db_path=db)
        _age(db, ev)
        fake.events = [{"uid": "ev-1", "title": "游泳课",
                        "start": "2026-06-14T10:00", "end": "2026-06-14T11:00",
                        "all_day": False, "location": "", "notes": ""}]
        r = self._v(fake, db)
        assert r["mode"] == "remote" and r["in_sync"] is True
        assert r["local_total"] == 1 and r["remote_total"] == 1

    def test_pending_bucket(self, engine):
        fake, db = engine
        _add_event(db, title="没推出去")            # synced=0
        r = self._v(fake, db)
        assert r["in_sync"] is False
        assert len(r["pending"]) == 1 and "没推出去" in r["pending"][0]

    def test_local_only_aged_vs_fresh(self, engine):
        fake, db = engine
        ev = _add_event(db, title="远端缺失", start="2026-06-14T10:00", end="")
        cal_db.mark_synced(ev, uid="ev-x", db_path=db)
        fake.events = []
        assert self._v(fake, db)["in_sync"] is True       # fresh → 豁免
        _age(db, ev)
        r = self._v(fake, db)
        assert r["in_sync"] is False
        assert len(r["local_only"]) == 1 and "远端缺失" in r["local_only"][0]

    def test_remote_only_bucket(self, engine):
        fake, db = engine
        fake.events = [{"uid": "ev-gmail", "title": "航班 AC123",
                        "start": "2026-06-14T08:00", "end": "2026-06-14T10:00",
                        "all_day": False, "location": "", "notes": ""}]
        r = self._v(fake, db)
        assert r["in_sync"] is False
        assert len(r["remote_only"]) == 1 and "航班" in r["remote_only"][0]

    def test_drift_event_title(self, engine):
        fake, db = engine
        ev = _add_event(db, title="旧名", start="2026-06-14T10:00",
                        end="2026-06-14T11:00")
        cal_db.mark_synced(ev, uid="ev-1", db_path=db)
        _age(db, ev)
        fake.events = [{"uid": "ev-1", "title": "新名",
                        "start": "2026-06-14T10:00", "end": "2026-06-14T11:00",
                        "all_day": False, "location": "", "notes": ""}]
        r = self._v(fake, db)
        assert r["in_sync"] is False and len(r["drift"]) == 1

    def test_drift_task_done_on_phone(self, engine):
        # 手机上直接划掉待办：本地 active + 远端 done → drift
        fake, db = engine
        tk = _add_task(db, title="买蛋糕", due="2026-06-15")
        cal_db.mark_synced(tk, uid="t-1", db_path=db)
        _age(db, tk)
        fake.tasks = [{"uid": "t-1", "title": "买蛋糕", "due": "2026-06-15",
                       "notes": "", "done": True}]
        r = self._v(fake, db, domain="tasks")
        assert r["in_sync"] is False and len(r["drift"]) == 1

    def test_event_outside_window_ignored(self, engine):
        fake, db = engine
        far = _add_event(db, title="远期", start="2099-01-01T10:00", end="")
        cal_db.mark_synced(far, uid="ev-far", db_path=db)
        _age(db, far)
        fake.events = []
        assert self._v(fake, db)["in_sync"] is True   # 窗口外不参与校验

    def test_provider_error_mode(self, engine):
        fake, db = engine
        fake.fail_list = True
        r = self._v(fake, db)
        assert r["mode"] == "error" and "network down" in r["error"]

    def test_local_mode_and_unconfigured(self, engine, monkeypatch):
        fake, db = engine
        monkeypatch.setattr(calendar_sync, "provider_for", lambda m, d: None)
        assert calendar_sync.verify_domain("MemberA", "schedule",
                                           db_path=db)["mode"] == "local"
        fake.configured = False
        assert self._v(fake, db)["mode"] == "local"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calendar_keeper.py::TestVerifyDomain -q`
Expected: FAIL with `AttributeError: module 'calendar_sync' has no attribute 'verify_domain'`

- [ ] **Step 3: Implement**

Append to `.codewhale/skills/Calendar_Keeper/calendar_sync.py` after `sync_for_query`:

```python
# ── 校验（每操作本地↔远端一致性核对） ────────────────────────────

def _norm(v) -> str:
    return (v or "").strip()


def _fields_differ(kind: str, row: dict, remote: dict) -> bool:
    """本地行 vs 远端条目核心字段是否漂移。

    待办：synced_active 只给 active 行 → 远端 done 即漂移（用户在手机上划掉），
    修复时 remote-wins 会把本地也置 done。
    活动 end 为空时远端曾默认 +1h → 首次校验判漂移，一轮修复即收敛，属预期。
    """
    if kind == "event":
        return (_norm(row["title"]) != _norm(remote.get("title"))
                or _norm(row["start_at"]) != _norm(remote.get("start"))
                or _norm(row["end_at"]) != _norm(remote.get("end"))
                or int(row["all_day"] or 0) != int(bool(remote.get("all_day")))
                or _norm(row["location"]) != _norm(remote.get("location"))
                or _norm(row["notes"]) != _norm(remote.get("notes")))
    return (bool(remote.get("done"))
            or _norm(row["title"]) != _norm(remote.get("title"))
            or _norm(row["start_at"]) != _norm(remote.get("due"))
            or _norm(row["notes"]) != _norm(remote.get("notes")))


def verify_domain(member: str, domain: str, *, db_path=None, prov=None,
                  now: datetime | None = None) -> dict:
    """只读校验某成员某域：查本地 + 查远端，分桶报告差异。永不抛。

    返回：
        {"mode": "local"}                          未配置/本地模式（调用方静默跳过）
        {"mode": "error", "error": str}            远端查询失败
        {"mode": "remote", "in_sync": bool,
         "pending": [...], "local_only": [...],    待推送 / 远端缺失
         "remote_only": [...], "drift": [...],     本地缺失 / 字段漂移
         "local_total": n, "remote_total": n}
    桶元素为人类可读短句（"#12 游泳课"），CLI 直接拼 verdict。
    新鲜保护（_VERIFY_FRESH_SECONDS）豁免 local_only/drift 判定；
    pending 不豁免——推送失败必须立刻可见。
    """
    try:
        if domain not in _DOMAIN_KIND:
            raise ValueError(f"domain 必须是 {tuple(_DOMAIN_KIND)}")
        p = prov if prov is not None else provider_for(member, domain)
        try:
            if p is None or not p.is_configured():
                return {"mode": "local"}
        except Exception:
            return {"mode": "local"}
        now = now or datetime.now()
        today = now.date()
        kind = _DOMAIN_KIND[domain]
        db_path = db_path or str(_paths.member_store(member, domain))

        if kind == "event":
            horizon_days = max(int(CFG.get("sync_horizon_days", 90)),
                               int(CFG.get("lookahead_days", 10)))
            horizon = today + timedelta(days=horizon_days)
            time_min = datetime.combine(today, dtime.min).astimezone() \
                .isoformat(timespec="seconds")
            time_max = datetime.combine(horizon, dtime(23, 59, 59)).astimezone() \
                .isoformat(timespec="seconds")
            remote = {e["uid"]: e for e in p.list_events(time_min, time_max)}
        else:
            remote = {t["uid"]: t for t in p.list_tasks()}

        local_uids = cal_db.uids(kind, db_path=db_path)
        pend = [f"#{r['id']} {r['title'][:20]}"
                for r in cal_db.pending(db_path=db_path) if r["kind"] == kind]

        local_only: list[str] = []
        drift: list[str] = []
        t_iso = today.isoformat()
        for row in cal_db.synced_active(kind, db_path=db_path):
            if kind == "event":
                d = row["start_at"][:10]
                if not d or not (t_iso <= d <= horizon.isoformat()):
                    continue                      # 窗口外不参与（拉取也拉不到）
            if _is_fresh(row["updated_at"], now):
                continue                          # 读写延迟保护
            r = remote.get(row["uid"])
            if r is None:
                local_only.append(f"#{row['id']} {row['title'][:20]}")
            elif _fields_differ(kind, row, r):
                drift.append(f"#{row['id']} {row['title'][:20]}")

        remote_only = [_norm(remote[u].get("title"))[:20] or "(无标题)"
                       for u in remote if u not in local_uids]
        in_sync = not (pend or local_only or remote_only or drift)
        return {"mode": "remote", "in_sync": in_sync, "pending": pend,
                "local_only": local_only, "remote_only": remote_only,
                "drift": drift, "local_total": len(local_uids),
                "remote_total": len(remote)}
    except Exception as e:
        return {"mode": "error", "error": str(e)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_calendar_keeper.py::TestVerifyDomain -q`
Expected: PASS (all 9)

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Calendar_Keeper/calendar_sync.py tests/test_calendar_keeper.py
git commit -m "feat(calendar): verify_domain — read-only local vs remote diff

Buckets: pending / local_only / remote_only / drift (incl. task done-state
for tasks slashed done on the phone). Fresh rows exempt except pending.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `verify_and_heal` — auto-heal via reconcile, then re-verify

**Files:**
- Modify: `.codewhale/skills/Calendar_Keeper/calendar_sync.py` (append after `verify_domain`)
- Test: `tests/test_calendar_keeper.py` (new class `TestVerifyAndHeal`)

**Interfaces:**
- Consumes: `verify_domain` (Task 3), existing `refresh_domain`.
- Produces: `verify_and_heal(member: str, domain: str, *, db_path=None, prov=None, now: datetime | None = None) -> dict` — the `verify_domain` result dict plus keys `healed: bool` (True only if a heal ran and the re-verify passed), `heal_pushed: int`, `heal_synced: int` (0 when no heal ran), optional `heal_error: str`. Never raises. Task 5's CLI consumes exactly these keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_calendar_keeper.py` after `class TestVerifyDomain` (uses `member_engine` so `refresh_domain`'s member-store/state paths resolve against tmp `DATA_ROOT`):

```python
class TestVerifyAndHeal:
    """不一致 → refresh_domain 修复 → 复检。"""

    def test_heals_remote_only_event(self, member_engine):
        import paths
        fake, tmp = member_engine
        sdb = str(paths.member_store("MemberA", "schedule"))
        cal_db._connect(db_path=sdb).close()          # 建库
        fake.events = [{"uid": "ev-gmail", "title": "航班 AC123",
                        "start": "2026-06-13T08:00", "end": "2026-06-13T10:00",
                        "all_day": False, "location": "", "notes": ""}]
        r = calendar_sync.verify_and_heal("MemberA", "schedule", db_path=sdb)
        assert r["mode"] == "remote"
        assert r["healed"] is True and r["in_sync"] is True
        assert r["heal_synced"] == 1
        rows = cal_db.list_upcoming(days=3650, today=date(2026, 6, 12), db_path=sdb)
        assert [x["uid"] for x in rows] == ["ev-gmail"]   # 修复=拉平到本地

    def test_heals_task_done_on_phone(self, member_engine):
        import paths
        fake, tmp = member_engine
        tdb = str(paths.member_store("MemberA", "tasks"))
        tk = cal_db.add_item(kind="task", title="买蛋糕", start_at="2026-06-15",
                             member="MemberA", db_path=tdb)
        cal_db.mark_synced(tk, uid="t-1", db_path=tdb)
        _age(tdb, tk)
        fake.tasks = [{"uid": "t-1", "title": "买蛋糕", "due": "2026-06-15",
                       "notes": "", "done": True}]
        r = calendar_sync.verify_and_heal("MemberA", "tasks", db_path=tdb)
        assert r["healed"] is True and r["in_sync"] is True
        assert cal_db.get_item(tk, db_path=tdb)["status"] == "done"   # 划掉生效

    def test_still_divergent_when_push_keeps_failing(self, member_engine):
        import paths
        fake, tmp = member_engine
        sdb = str(paths.member_store("MemberA", "schedule"))
        cal_db.add_item(kind="event", title="推不动", start_at="2026-06-13T10:00",
                        member="MemberA", db_path=sdb)
        fake.fail_create = True
        r = calendar_sync.verify_and_heal("MemberA", "schedule", db_path=sdb)
        assert r["mode"] == "remote"
        assert r["healed"] is False and r["in_sync"] is False
        assert len(r["pending"]) == 1

    def test_in_sync_short_circuits_no_heal(self, member_engine, monkeypatch):
        import paths
        fake, tmp = member_engine
        sdb = str(paths.member_store("MemberA", "schedule"))
        cal_db._connect(db_path=sdb).close()
        called = []
        monkeypatch.setattr(calendar_sync, "refresh_domain",
                            lambda *a, **k: called.append(1) or {"pushed": 0,
                                                                 "synced": 0,
                                                                 "errors": []})
        r = calendar_sync.verify_and_heal("MemberA", "schedule", db_path=sdb)
        assert r["in_sync"] is True and r["healed"] is False
        assert called == []                        # 一致 → 不跑修复

    def test_local_mode_passthrough(self, member_engine, monkeypatch):
        monkeypatch.setattr(calendar_sync, "provider_for", lambda m, d: None)
        r = calendar_sync.verify_and_heal("MemberA", "schedule")
        assert r["mode"] == "local" and r["healed"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calendar_keeper.py::TestVerifyAndHeal -q`
Expected: FAIL with `AttributeError: module 'calendar_sync' has no attribute 'verify_and_heal'`

- [ ] **Step 3: Implement**

Append to `.codewhale/skills/Calendar_Keeper/calendar_sync.py` after `verify_domain`:

```python
def verify_and_heal(member: str, domain: str, *, db_path=None, prov=None,
                    now: datetime | None = None) -> dict:
    """校验；不一致则 refresh_domain（先推后拉+对账，remote wins）后复检。永不抛。

    返回最终 verify_domain 结果 + healed（修复后复检通过才 True）
    + heal_pushed / heal_synced（未跑修复时为 0）。
    复检与修复共用同一 prov/db_path；修复刚写入的行 updated_at 新鲜 →
    复检的新鲜保护自然豁免它们，静态远端快照下也能收敛。
    """
    first = verify_domain(member, domain, db_path=db_path, prov=prov, now=now)
    if first.get("mode") != "remote" or first.get("in_sync"):
        return {**first, "healed": False, "heal_pushed": 0, "heal_synced": 0}
    try:
        healed = refresh_domain(member, domain, db_path=db_path, prov=prov,
                                now=now)
    except Exception as e:
        return {**first, "healed": False, "heal_pushed": 0, "heal_synced": 0,
                "heal_error": str(e)}
    second = verify_domain(member, domain, db_path=db_path, prov=prov, now=now)
    if second.get("mode") != "remote":
        second = first                       # 复检查询失败 → 报首轮差异
    return {**second, "healed": bool(second.get("in_sync")),
            "heal_pushed": healed.get("pushed", 0),
            "heal_synced": healed.get("synced", 0)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_calendar_keeper.py::TestVerifyAndHeal -q`
Expected: PASS (all 5). Then full file: `python -m pytest tests/test_calendar_keeper.py -q` — ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Calendar_Keeper/calendar_sync.py tests/test_calendar_keeper.py
git commit -m "feat(calendar): verify_and_heal — auto-reconcile on divergence, re-verify

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: CLI — verdict line on all six commands

**Files:**
- Modify: `.codewhale/skills/Calendar_Keeper/cli.py`
- Test: `tests/test_calendar_keeper.py` (new class `TestCliVerify` + one subprocess assertion)

**Interfaces:**
- Consumes: `calendar_sync.verify_and_heal` (Task 4).
- Produces: CLI behavior only — verdict lines appended to stdout of cal-add / cal-list / cal-done / cal-delete / cal-sync / cal-status. Internal helpers `_verify_lines(member: str, domains: list[str]) -> list[str]` and `_print_verify(member, domains)`.

- [ ] **Step 1: Write the failing tests**

CLI verify needs the fake provider, so these tests run **in-process** (subprocess can't be monkeypatched). Load the module under a unique name (three skills have a `cli.py`). Add after `class TestCliPerMember`:

```python
def _load_calkeeper_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("calkeeper_cli", str(_CLI))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli_verify(monkeypatch, tmp_path):
    """in-process CLI + fake provider（subprocess 打不了桩）。"""
    monkeypatch.delenv("CAL_DB_PATH", raising=False)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CALENDAR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BACKUP_STATE_DIR", str(tmp_path))
    mod = _load_calkeeper_cli()
    monkeypatch.setattr(mod, "_DB_OVERRIDE", None)
    fake = FakeProvider()
    monkeypatch.setattr(calendar_sync, "provider_for",
                        lambda m, d: fake if m == "MemberA" else None)
    return mod, fake


def _ns(**kw):
    import argparse
    base = {"member": "MemberA", "kind": "event", "title": "X", "date": None,
            "start": None, "end": None, "all_day": False, "location": None,
            "notes": None, "source_image": None, "days": 10, "all": False,
            "id": 1}
    base.update(kw)
    return argparse.Namespace(**base)


class TestCliVerify:
    def test_add_in_sync_verdict(self, cli_verify, capsys):
        mod, fake = cli_verify
        mod.cmd_cal_add(_ns(title="游泳课", date=D1, start="14:00", end="15:00"))
        out = capsys.readouterr().out
        assert "已添加" in out
        assert "校验: 本地=远端一致（活动）" in out   # 刚推送→新鲜豁免→一致

    def test_list_heals_remote_task_done_and_orders_output(self, cli_verify, capsys):
        # 手机上划掉的待办：cal-list 先修复再列 → 列表不再含它，verdict 说已修复
        import paths
        mod, fake = cli_verify
        tdb = str(paths.member_store("MemberA", "tasks"))
        tk = cal_db.add_item(kind="task", title="买蛋糕", start_at=D3,
                             member="MemberA", db_path=tdb)
        cal_db.mark_synced(tk, uid="t-1", db_path=tdb)
        _age(tdb, tk)
        fake.tasks = [{"uid": "t-1", "title": "买蛋糕", "due": D3,
                       "notes": "", "done": True}]
        mod.cmd_cal_list(_ns(kind="task"))
        out = capsys.readouterr().out
        assert "已自动修复" in out
        assert "买蛋糕" not in out.split("校验")[0]   # 修复后已 done，不在开放列表
        assert cal_db.get_item(tk, db_path=tdb)["status"] == "done"

    def test_add_push_failure_still_divergent(self, cli_verify, capsys):
        mod, fake = cli_verify
        fake.fail_create = True
        mod.cmd_cal_add(_ns(title="推不动", date=D1))
        out = capsys.readouterr().out
        assert "已添加" in out                       # 主操作不受影响
        assert "⚠ 校验: 本地≠远端（活动）" in out and "待推送1" in out

    def test_verify_error_line(self, cli_verify, capsys):
        mod, fake = cli_verify
        fake.events = []
        fake.fail_list = True
        mod.cmd_cal_add(_ns(title="X", date=D1))
        out = capsys.readouterr().out
        assert "已添加" in out and "校验失败（活动）" in out

    def test_local_member_no_verdict(self, cli_verify, capsys):
        mod, fake = cli_verify
        mod.cmd_cal_add(_ns(member="Robin", title="本地", date=D1))
        out = capsys.readouterr().out
        assert "已添加" in out and "校验" not in out

    def test_done_and_delete_verify_their_domain(self, cli_verify, capsys):
        import paths
        mod, fake = cli_verify
        tdb = str(paths.member_store("MemberA", "tasks"))
        tk = cal_db.add_item(kind="task", title="T", member="MemberA", db_path=tdb)
        cal_db.mark_synced(tk, uid="t-1", db_path=tdb)
        fake.tasks = [{"uid": "t-1", "title": "T", "due": "", "notes": "",
                       "done": False}]
        mod.cmd_cal_done(_ns(id=tk))
        out = capsys.readouterr().out
        assert "已完成待办" in out and "（待办）" in out and "校验" in out

    def test_status_appends_verify(self, cli_verify, capsys):
        mod, fake = cli_verify
        mod.cmd_cal_status(_ns())
        out = capsys.readouterr().out
        assert "日历同步（MemberA）" in out
        assert out.count("校验") >= 2               # 活动+待办各一行

    def test_sync_appends_verify(self, cli_verify, capsys):
        mod, fake = cli_verify
        mod.cmd_cal_sync(_ns())
        out = capsys.readouterr().out
        assert "已刷新" in out and "校验" in out
```

And one subprocess regression guard inside existing `class TestCli`:

```python
    def test_override_mode_prints_no_verify(self, cal_db_path, tmp_path):
        # CAL_DB_PATH 覆盖（测试模式）绝不打远端、绝不出校验行
        r = _cli(["cal-add", "--member", "MemberA", "--kind", "task",
                  "--title", "x"], cal_db_path, tmp_path)
        assert r.returncode == 0 and "校验" not in r.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calendar_keeper.py::TestCliVerify -q`
Expected: FAIL — verdicts absent (`assert "校验: ..." in out` fails); `test_override_mode_prints_no_verify` PASSES already (nothing prints 校验 yet — fine, it's the regression guard).

- [ ] **Step 3: Implement in `.codewhale/skills/Calendar_Keeper/cli.py`**

Add after `_sync_suffix` (~line 97):

```python
_DOMAIN_LABEL = {"schedule": "活动", "tasks": "待办"}


def _verify_lines(member: str, domains: list[str]) -> list[str]:
    """每操作校验：查本地+远端并对账，返回 verdict 行。绝不抛、绝不影响主操作。

    覆盖库（测试）/无成员 → 空。本地模式域 → 无行（不打扰未配置用户）。
    """
    if _DB_OVERRIDE or not member:
        return []
    lines: list[str] = []
    for d in domains:
        label = _DOMAIN_LABEL[d]
        try:
            r = calendar_sync.verify_and_heal(member, d)
        except Exception as e:                    # verify_and_heal 永不抛，双保险
            lines.append(f"校验失败（{label}）: {e}")
            continue
        if r.get("mode") == "local":
            continue
        if r.get("mode") == "error":
            lines.append(f"校验失败（{label}）: {r.get('error')}")
        elif r.get("healed"):
            lines.append(f"校验: 发现不一致，已自动修复"
                         f"（推送{r['heal_pushed']}/拉取{r['heal_synced']}）（{label}）")
        elif r.get("in_sync"):
            lines.append(f"校验: 本地=远端一致（{label}）")
        else:
            parts = []
            for key, word in (("pending", "待推送"), ("local_only", "远端缺失"),
                              ("remote_only", "本地缺失"), ("drift", "字段不一致")):
                if r.get(key):
                    parts.append(f"{word}{len(r[key])}（{r[key][0]}）")
            lines.append(f"⚠ 校验: 本地≠远端（{label}）— " + "、".join(parts))
    return lines


def _print_verify(member: str, domains: list[str]) -> None:
    for line in _verify_lines(member, domains):
        print(line)
```

Wire the six commands:

`cmd_cal_add` — append as last line:
```python
    _print_verify(args.member, ["schedule" if args.kind == "event" else "tasks"])
```

`cmd_cal_list` — replace the `sync_for_query` block at the top:
```python
def cmd_cal_list(args):
    # 查询前先校验+修复（无节流）：捕获远端新加事件与手机上划掉的待办，
    # 列表读的是修复后的本地。verdict 行列在清单之后。
    domains = {"event": ["schedule"], "task": ["tasks"]}.get(
        args.kind, ["schedule", "tasks"])
    verify = _verify_lines(args.member or "", domains)
```
(the rest of the function body is unchanged until the end; after the `for r in rows: print(...)` loop and after the `if not rows: print("（无日程）")` branch, print the collected lines):
```python
    for line in verify:
        print(line)
```
Note: the early `return` in the empty branch must be removed so verdicts still print:
```python
    if not rows:
        print("（无日程）")
    else:
        for r in rows:
            print(_fmt_item(r))
    for line in verify:
        print(line)
```

`cmd_cal_done` — append as last line:
```python
    _print_verify(args.member, ["tasks"])
```

`cmd_cal_delete` — append as last line:
```python
    _print_verify(args.member,
                  ["schedule" if item["kind"] == "event" else "tasks"])
```

`cmd_cal_sync` — append at the very end (after the errors loop; `member` local var already exists):
```python
    _print_verify(member, ["schedule", "tasks"])
```

`cmd_cal_status` — in the `if member and not _DB_OVERRIDE:` branch, after the per-domain `print(line)` loop and before `return`:
```python
        _print_verify(member, ["schedule", "tasks"])
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_calendar_keeper.py -q`
Expected: ALL PASS — new `TestCliVerify`, override guard, and every pre-existing CLI test (they run with `CAL_DB_PATH` set → `_verify_lines` returns `[]`, output unchanged).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Calendar_Keeper/cli.py tests/test_calendar_keeper.py
git commit -m "feat(calendar-cli): verdict line on every command — verify+heal local vs remote

cal-list now verifies (unthrottled) instead of sync_for_query; listing
reads healed state. cal-status reports true consistency per domain.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Agent runtime — prompt rules + tool descriptions

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` (4 edits, all text)
- Test: `tests/test_calendar_keeper.py` (extend `class TestAgentWiring`)

**Interfaces:**
- Consumes: nothing new (CLI already emits verdicts through `_run_cli` output).
- Produces: prompt text the tests assert on. No signature changes.

- [ ] **Step 1: Write the failing tests**

Add to `class TestAgentWiring`:

```python
    def test_prompt_forces_tool_on_schedule_query(self):
        import agent_core
        p = agent_core._build_system_prompt()
        assert "必须调 list_schedule" in p          # 查询必须过工具（远端核对）
        assert "校验" in p                          # verdict 转告规则
        assert "calendar_status 不是凭据" not in p   # 旧拐杖已退役

    def test_calendar_tool_descs_mention_verify(self):
        import agent_core
        descs = {t["function"]["name"]: t["function"]["description"]
                 for t in agent_core.TOOL_SCHEMAS}
        assert "核对" in descs["calendar_status"]
        assert "核对" in descs["list_schedule"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calendar_keeper.py::TestAgentWiring -q`
Expected: the two new tests FAIL (strings absent); the four old ones PASS.

- [ ] **Step 3: Edit `.codewhale/skills/Agent_Runtime/agent_core.py`**

Edit 1 — line 246, replace:
```
- **calendar_status 不是凭据**：它只反映已入队的同步条数，不能据此声称某条新日程已存在或已同步——某日程到底有没有，以你 add_event 的返回为准
```
with:
```
- **calendar_status 现在核对真伪**：它会实时查询远端并与本地对账，可用于回答"同步了吗/本地和日历一致吗"；但某条新日程是否创建成功，仍以你 add_event 的返回为准
```

Edit 2 — line 250, replace:
```
- 用户问"接下来有什么安排/这周有什么事/我的待办"→ 按上下文回答或调 list_schedule
```
with:
```
- 用户问"接下来有什么安排/这周有什么事/我的待办"→ **必须调 list_schedule 再回答**（它会先与远端核对同步——家人可能刚在手机日历上加了事、或直接划掉了待办）；注入的上下文只是快照，可能过期，仅作话题参考
```

Edit 3 — line 253, replace:
```
- 新增/完成/取消会自动同步到远程日历；"待同步"= 暂未推送会自动重试，无需向用户解释技术细节
```
with:
```
- 新增/完成/取消会自动同步到远程日历；"待同步"= 暂未推送会自动重试，无需向用户解释技术细节
- 日程工具结果末尾的"校验"行 = 本地↔远端实时核对结论：一致/已自动修复时一笔带过即可；出现"本地≠远端/校验失败"时必须明确转告用户哪里不一致，绝不隐瞒
```

Edit 4 — tool schemas. Line 942, replace:
```python
    _fn("list_schedule", "查询未来日程与开放待办（用户问\"接下来有什么安排\"\"待办清单\"）", {
```
with:
```python
    _fn("list_schedule", "查询未来日程与开放待办（查询前自动与远端核对同步；用户问\"接下来有什么安排\"\"待办清单\"）", {
```
Line 954, replace:
```python
    _fn("calendar_status", "查看日历同步状态（启用/配置/上次刷新/待同步/错误）", {}),
```
with:
```python
    _fn("calendar_status", "查看日历同步状态并实时核对本地↔远端一致性（启用/配置/上次刷新/校验结论）", {}),
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_calendar_keeper.py::TestAgentWiring -q` then `python -m pytest tests/test_agent_member.py -q` (prompt-adjacent suite).
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_calendar_keeper.py
git commit -m "feat(agent): calendar prompt — always list_schedule on queries, relay verify verdicts

calendar_status is now a real local vs remote check; retire the
'not-evidence' crutch. Injected schedule context demoted to topical
snapshot only.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Docs + full suite + wrap-up

**Files:**
- Modify: `.codewhale/skills/Calendar_Keeper/SKILL.md`
- Modify: `FamilyAssistant.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `Calendar_Keeper/SKILL.md`**

Replace the 查询前拉取 bullet (工作方式 section):
```
- **查询前拉取**：用户问日程（cal-list）时，先调 `calendar_sync.sync_for_query(member)`
  按 `query_refresh_seconds` 短节流主动拉远端，再读本地。捕获 Google 端新加但后台
  tick 尚未拉到的事件（如 Gmail 自动建日程），避免回答陈旧。本地模式成员跳过。
```
with:
```
- **每操作校验**：所有 cal-* 命令收尾对该成员相关域做本地↔远端实时核对
  （`calendar_sync.verify_and_heal`，无节流）：拉远端快照与本地分桶比对
  （待推送/远端缺失/本地缺失/字段漂移；updated_at 60 秒内的新鲜行豁免，
  防 Google list 读写延迟误判），不一致即走 `refresh_domain` 自动修复并复检，
  输出一行"校验"结论。手机上直接划掉的待办、Gmail 自动建的日程都会在下一次
  操作时被拉平。cal-list 先校验修复再读本地（`sync_for_query` 保留但主路径不再用）。
  本地模式成员静默跳过，绝不影响主操作。
```

CLI table — replace the cal-status row:
```
| `cal-sync` | 立即强制刷新（忽略节流） | ✅ |
| `cal-status` | 同步状态（启用/配置/上次刷新/待同步/错误） | ✅ |
```
with:
```
| `cal-sync` | 立即强制刷新（忽略节流）+ 校验 | ✅ |
| `cal-status` | 同步状态 + 本地↔远端实时校验结论 | ✅ |
```

配置 section — replace:
```
`query_refresh_seconds`（60，查询路径同步节流）/
```
with:
```
`query_refresh_seconds`（60，兼容保留——主路径已改为每操作无节流校验）/
```

- [ ] **Step 2: Update `FamilyAssistant.md`**

Line 14, in the Calendar Keeper row, replace:
```
| **Calendar Keeper** | 按成员私有的日程与待办（活动/待办分库），与各成员自己的远程日历静默同步（作者已实现 Google Calendar + Tasks provider，按成员/域选择，用户可按契约换其他日历服务） | [SKILL.md](.codewhale/skills/Calendar_Keeper/SKILL.md) | 日程、安排、活动、待办、任务、日历 |
```
with:
```
| **Calendar Keeper** | 按成员私有的日程与待办（活动/待办分库），与各成员自己的远程日历静默同步，每次日程操作实时核对本地↔远端一致性并自动修复（作者已实现 Google Calendar + Tasks provider，按成员/域选择，用户可按契约换其他日历服务） | [SKILL.md](.codewhale/skills/Calendar_Keeper/SKILL.md) | 日程、安排、活动、待办、任务、日历 |
```

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: ALL PASS (whole project, not just calendar — agent prompt changes must not break other suites).

- [ ] **Step 4: Commit**

```bash
git add .codewhale/skills/Calendar_Keeper/SKILL.md FamilyAssistant.md
git commit -m "docs(calendar): per-op local vs remote verify — SKILL.md + overview

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- Spec coverage: engine verify (Task 3), heal (Task 4), freshness guard incl. reconcile loops (Task 2), `uids` support (Task 1), CLI verdicts on all six commands + sync_for_query removal from cal-list + ordering (Task 5), prompt/query rule/status crutch retirement/tool descs (Task 6), docs (Task 7). Costs/out-of-scope sections need no code.
- Existing-test impact called out explicitly (Task 2: `test_refresh_pulls_and_reconciles` aging).
- Type consistency: `verify_and_heal` returns `heal_pushed`/`heal_synced` — CLI (Task 5) consumes exactly those keys; `verify_domain` buckets `pending`/`local_only`/`remote_only`/`drift` — CLI iterates exactly those.
