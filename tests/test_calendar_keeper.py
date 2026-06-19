# tests/test_calendar_keeper.py — Calendar Keeper skill tests.
# No real family names, channel ids, or credentials in test data.
import json
from datetime import date

import pytest

import cal_db
import calendar_provider


TODAY = date(2026, 6, 12)


def _add_event(db, title="游泳课", start="2026-06-13T14:00", end="2026-06-13T15:00",
               member="MemberA", **kw):
    return cal_db.add_item(kind="event", title=title, start_at=start, end_at=end,
                           member=member, db_path=db, **kw)


def _add_task(db, title="买蛋糕", due="2026-06-15", member="MemberA", **kw):
    return cal_db.add_item(kind="task", title=title, start_at=due,
                           member=member, db_path=db, **kw)


class TestCalDb:
    def test_add_and_get_roundtrip(self, cal_db_path):
        iid = _add_event(cal_db_path, location="泳馆", notes="带泳镜")
        item = cal_db.get_item(iid, db_path=cal_db_path)
        assert item["kind"] == "event"
        assert item["title"] == "游泳课"
        assert item["start_at"] == "2026-06-13T14:00"
        assert item["end_at"] == "2026-06-13T15:00"
        assert item["location"] == "泳馆"
        assert item["member"] == "MemberA"
        assert item["status"] == "active"
        assert item["origin"] == "local"
        assert item["synced"] == 0
        assert item["uid"] == ""

    def test_add_requires_title(self, cal_db_path):
        with pytest.raises(ValueError):
            cal_db.add_item(kind="event", title="  ", db_path=cal_db_path)

    def test_add_rejects_unknown_kind(self, cal_db_path):
        with pytest.raises(ValueError):
            cal_db.add_item(kind="meeting", title="x", db_path=cal_db_path)

    def test_list_upcoming_window_and_order(self, cal_db_path):
        _add_event(cal_db_path, title="窗口外", start="2026-06-30T09:00", end="")
        late = _add_event(cal_db_path, title="后", start="2026-06-20T09:00", end="")
        early = _add_event(cal_db_path, title="先", start="2026-06-13T08:00", end="")
        rows = cal_db.list_upcoming(days=10, today=TODAY, db_path=cal_db_path)
        assert [r["id"] for r in rows] == [early, late]

    def test_list_upcoming_keeps_spanning_and_drops_past(self, cal_db_path):
        spanning = _add_event(cal_db_path, title="跨今天",
                              start="2026-06-10T09:00", end="2026-06-13T18:00")
        _add_event(cal_db_path, title="已过去",
                   start="2026-06-01T09:00", end="2026-06-02T10:00")
        rows = cal_db.list_upcoming(days=10, today=TODAY, db_path=cal_db_path)
        assert [r["id"] for r in rows] == [spanning]

    def test_list_upcoming_includes_undated_open_tasks(self, cal_db_path):
        dated = _add_task(cal_db_path, title="有期限", due="2026-06-14")
        undated = _add_task(cal_db_path, title="无期限", due="")
        rows = cal_db.list_upcoming(days=10, today=TODAY, db_path=cal_db_path)
        assert [r["id"] for r in rows] == [dated, undated]  # undated tasks last

    def test_list_upcoming_excludes_closed_by_default(self, cal_db_path):
        open_id = _add_task(cal_db_path, title="open", due="2026-06-14")
        done_id = _add_task(cal_db_path, title="done", due="2026-06-14")
        cal_db.set_status(done_id, "done", db_path=cal_db_path)
        rows = cal_db.list_upcoming(days=10, today=TODAY, db_path=cal_db_path)
        assert [r["id"] for r in rows] == [open_id]
        all_rows = cal_db.list_upcoming(days=10, today=TODAY,
                                        include_closed=True, db_path=cal_db_path)
        assert {r["id"] for r in all_rows} == {open_id, done_id}

    def test_list_upcoming_filters_kind_and_member(self, cal_db_path):
        ev = _add_event(cal_db_path, member="MemberA")
        _add_task(cal_db_path, member="MemberB", due="2026-06-14")
        only_events = cal_db.list_upcoming(days=10, today=TODAY, kind="event",
                                           db_path=cal_db_path)
        assert [r["id"] for r in only_events] == [ev]
        only_a = cal_db.list_upcoming(days=10, today=TODAY, member="MemberA",
                                      db_path=cal_db_path)
        assert [r["id"] for r in only_a] == [ev]

    def test_set_status_marks_unsynced(self, cal_db_path):
        iid = _add_task(cal_db_path)
        cal_db.mark_synced(iid, uid="remote-1", db_path=cal_db_path)
        assert cal_db.get_item(iid, db_path=cal_db_path)["synced"] == 1
        assert cal_db.set_status(iid, "done", db_path=cal_db_path) is True
        item = cal_db.get_item(iid, db_path=cal_db_path)
        assert item["status"] == "done"
        assert item["synced"] == 0          # local change → pending push
        assert item["uid"] == "remote-1"    # uid preserved

    def test_set_status_from_remote_keeps_synced(self, cal_db_path):
        iid = _add_task(cal_db_path)
        cal_db.mark_synced(iid, uid="remote-1", db_path=cal_db_path)
        cal_db.set_status(iid, "cancelled", from_remote=True, db_path=cal_db_path)
        item = cal_db.get_item(iid, db_path=cal_db_path)
        assert item["status"] == "cancelled"
        assert item["synced"] == 1          # remote-origin change, nothing to push

    def test_set_status_unknown_id(self, cal_db_path):
        assert cal_db.set_status(999, "done", db_path=cal_db_path) is False

    def test_pending_and_mark_synced(self, cal_db_path):
        iid = _add_event(cal_db_path)
        assert [r["id"] for r in cal_db.pending(db_path=cal_db_path)] == [iid]
        cal_db.mark_synced(iid, uid="gcal-42", db_path=cal_db_path)
        assert cal_db.pending(db_path=cal_db_path) == []
        item = cal_db.get_item(iid, db_path=cal_db_path)
        assert item["uid"] == "gcal-42" and item["synced"] == 1

    def test_upsert_remote_insert_then_overwrite(self, cal_db_path):
        fields = {"title": "远程活动", "start_at": "2026-06-14T10:00",
                  "end_at": "2026-06-14T11:00", "all_day": 0,
                  "location": "公园", "notes": "", "status": "active"}
        iid = cal_db.upsert_remote("event", "uid-7", fields, db_path=cal_db_path)
        item = cal_db.get_item(iid, db_path=cal_db_path)
        assert item["origin"] == "remote" and item["synced"] == 1
        assert item["member"] == ""
        # remote edit wins over synced local copy
        again = cal_db.upsert_remote("event", "uid-7",
                                     {**fields, "title": "改名了"}, db_path=cal_db_path)
        assert again == iid
        assert cal_db.get_item(iid, db_path=cal_db_path)["title"] == "改名了"

    def test_upsert_remote_skips_pending_local(self, cal_db_path):
        iid = _add_task(cal_db_path, title="本地待办")
        cal_db.mark_synced(iid, uid="t-1", db_path=cal_db_path)
        cal_db.set_status(iid, "done", db_path=cal_db_path)  # pending local change
        cal_db.upsert_remote("task", "t-1",
                             {"title": "远程覆盖", "start_at": "", "end_at": "",
                              "all_day": 0, "location": "", "notes": "",
                              "status": "active"}, db_path=cal_db_path)
        item = cal_db.get_item(iid, db_path=cal_db_path)
        assert item["title"] == "本地待办"      # pending row untouched
        assert item["status"] == "done"

    def test_synced_active(self, cal_db_path):
        a = _add_event(cal_db_path)
        cal_db.mark_synced(a, uid="e-1", db_path=cal_db_path)
        _add_event(cal_db_path, title="未同步")            # synced=0 → excluded
        b = _add_task(cal_db_path)
        cal_db.mark_synced(b, uid="t-1", db_path=cal_db_path)
        rows = cal_db.synced_active("event", db_path=cal_db_path)
        assert [r["id"] for r in rows] == [a]


# ── Google provider（HTTP 全部打桩，零网络） ─────────────────────


@pytest.fixture
def gcal(monkeypatch):
    """Google provider with env creds set and HTTP layer faked."""
    monkeypatch.setenv("GCAL_CLIENT_ID", "cid")
    monkeypatch.setenv("GCAL_CLIENT_SECRET", "cs")
    monkeypatch.setenv("GCAL_REFRESH_TOKEN", "rt")
    monkeypatch.delenv("GCAL_CALENDAR_ID", raising=False)
    monkeypatch.setattr(calendar_provider, "_token", lambda: "TOK")
    monkeypatch.setattr(calendar_provider, "_tz_offset", lambda: "+00:00")

    calls = []

    class FakeHttp:
        def __init__(self):
            self.responses = []  # list of (status, body-bytes)

        def __call__(self, method, url, data=None, headers=None):
            calls.append({"method": method, "url": url, "data": data,
                          "headers": headers or {}})
            return self.responses.pop(0)

    fake = FakeHttp()
    monkeypatch.setattr(calendar_provider, "_http", fake)
    return fake, calls


def _items_resp(*items, next_token=None):
    body = {"items": list(items)}
    if next_token:
        body["nextPageToken"] = next_token
    return (200, json.dumps(body).encode())


class TestGoogleProvider:
    def test_is_configured_requires_all_env(self, monkeypatch):
        for var in ("GCAL_CLIENT_ID", "GCAL_CLIENT_SECRET", "GCAL_REFRESH_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        assert calendar_provider.is_configured() is False
        monkeypatch.setenv("GCAL_CLIENT_ID", "x")
        monkeypatch.setenv("GCAL_CLIENT_SECRET", "y")
        assert calendar_provider.is_configured() is False
        monkeypatch.setenv("GCAL_REFRESH_TOKEN", "z")
        assert calendar_provider.is_configured() is True

    def test_list_events_parses_timed_and_allday(self, gcal):
        fake, calls = gcal
        fake.responses = [
            _items_resp(
                {"id": "ev1", "summary": "游泳课", "location": "泳馆",
                 "start": {"dateTime": "2026-06-13T14:00:00+00:00"},
                 "end": {"dateTime": "2026-06-13T15:00:00+00:00"}},
                next_token="P2"),
            _items_resp(
                {"id": "ev2", "summary": "学校放假",
                 "start": {"date": "2026-06-15"}, "end": {"date": "2026-06-16"}},
                {"id": "ev3", "status": "cancelled",
                 "start": {"date": "2026-06-15"}, "end": {"date": "2026-06-16"}}),
        ]
        out = calendar_provider.list_events("2026-06-12T00:00:00+00:00",
                                            "2026-06-22T23:59:00+00:00")
        assert "calendars/primary/events" in calls[0]["url"]
        assert "singleEvents=true" in calls[0]["url"]
        assert "pageToken=P2" in calls[1]["url"]
        assert out == [
            {"uid": "ev1", "title": "游泳课", "start": "2026-06-13T14:00",
             "end": "2026-06-13T15:00", "all_day": False,
             "location": "泳馆", "notes": ""},
            {"uid": "ev2", "title": "学校放假", "start": "2026-06-15",
             "end": "2026-06-16", "all_day": True, "location": "", "notes": ""},
        ]  # cancelled 的 ev3 被跳过

    def test_create_event_timed_payload(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b'{"id": "new-ev"}')]
        uid = calendar_provider.create_event(
            {"title": "牙医", "start_at": "2026-06-14T09:30",
             "end_at": "2026-06-14T10:00", "all_day": 0,
             "location": "诊所", "notes": "带保险卡"})
        assert uid == "new-ev"
        body = json.loads(calls[0]["data"])
        assert calls[0]["method"] == "POST"
        assert body["summary"] == "牙医"
        assert body["start"] == {"dateTime": "2026-06-14T09:30:00+00:00"}
        assert body["end"] == {"dateTime": "2026-06-14T10:00:00+00:00"}
        assert body["location"] == "诊所"
        assert body["description"] == "带保险卡"

    def test_create_event_default_end_one_hour(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b'{"id": "e"}')]
        calendar_provider.create_event(
            {"title": "x", "start_at": "2026-06-14T23:30", "end_at": "",
             "all_day": 0, "location": "", "notes": ""})
        body = json.loads(calls[0]["data"])
        assert body["end"] == {"dateTime": "2026-06-15T00:30:00+00:00"}

    def test_create_event_allday_payload(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b'{"id": "e"}')]
        calendar_provider.create_event(
            {"title": "放假", "start_at": "2026-06-15", "end_at": "",
             "all_day": 1, "location": "", "notes": ""})
        body = json.loads(calls[0]["data"])
        assert body["start"] == {"date": "2026-06-15"}
        assert body["end"] == {"date": "2026-06-16"}  # 全天 end 独占

    def test_delete_event_missing_is_noop(self, gcal):
        fake, calls = gcal
        fake.responses = [(404, b"not found")]
        calendar_provider.delete_event("gone")  # must not raise

    def test_list_tasks_maps_fields(self, gcal):
        fake, calls = gcal
        fake.responses = [_items_resp(
            {"id": "t1", "title": "买蛋糕", "due": "2026-06-15T00:00:00.000Z",
             "status": "needsAction", "notes": "巧克力"},
            {"id": "t2", "title": "已完成", "status": "completed"},
            {"id": "t3", "title": "已删除", "status": "needsAction", "deleted": True},
        )]
        out = calendar_provider.list_tasks()
        assert "lists/%40default/tasks" in calls[0]["url"] \
            or "lists/@default/tasks" in calls[0]["url"]
        assert "showCompleted=true" in calls[0]["url"]
        assert out == [
            {"uid": "t1", "title": "买蛋糕", "due": "2026-06-15",
             "notes": "巧克力", "done": False},
            {"uid": "t2", "title": "已完成", "due": "", "notes": "", "done": True},
        ]  # deleted 的 t3 被跳过

    def test_create_task_payload(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b'{"id": "new-t"}')]
        uid = calendar_provider.create_task(
            {"title": "买蛋糕", "start_at": "2026-06-15", "notes": ""})
        assert uid == "new-t"
        body = json.loads(calls[0]["data"])
        assert body["title"] == "买蛋糕"
        assert body["due"] == "2026-06-15T00:00:00.000Z"

    def test_complete_task_patch_and_404_ok(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b"{}")]
        calendar_provider.complete_task("t1")
        assert calls[0]["method"] == "PATCH"
        assert json.loads(calls[0]["data"]) == {"status": "completed"}
        fake.responses = [(404, b"gone")]
        calendar_provider.complete_task("vanished")  # must not raise

    def test_api_error_raises_runtimeerror(self, gcal):
        fake, calls = gcal
        fake.responses = [(500, b"boom")]
        with pytest.raises(RuntimeError):
            calendar_provider.list_tasks()


# ── provider 注册表 ─────────────────────────────────────────────

import providers


class TestProviderRegistry:
    def test_schedule_google_has_event_half(self):
        p = providers.get("schedule", "google_calendar")
        assert p is not None
        for fn in ("is_configured", "list_events", "create_event", "delete_event"):
            assert hasattr(p, fn)

    def test_tasks_google_has_task_half(self):
        p = providers.get("tasks", "google_tasks")
        assert p is not None
        for fn in ("is_configured", "list_tasks", "create_task",
                   "complete_task", "delete_task"):
            assert hasattr(p, fn)

    def test_unknown_or_local_is_none(self):
        assert providers.get("schedule", "local") is None
        assert providers.get("schedule", "") is None
        assert providers.get("tasks", "nope") is None


# ── 同步引擎（provider 全部打桩） ───────────────────────────────

import calendar_sync


class FakeProvider:
    """契约形状的假 provider，记录调用。"""

    def __init__(self):
        self.configured = True
        self.events = []          # list_events 返回值
        self.tasks = []           # list_tasks 返回值
        self.created = []         # (kind, item)
        self.completed = []       # task uids
        self.deleted = []         # (kind, uid)
        self.fail_create = False
        self.fail_list = False
        self._uid_seq = 0

    def is_configured(self):
        return self.configured

    def list_events(self, time_min, time_max):
        if self.fail_list:
            raise RuntimeError("network down")
        return list(self.events)

    def create_event(self, item):
        if self.fail_create:
            raise RuntimeError("create failed")
        self._uid_seq += 1
        self.created.append(("event", dict(item)))
        return f"ev-{self._uid_seq}"

    def delete_event(self, uid):
        self.deleted.append(("event", uid))

    def list_tasks(self):
        if self.fail_list:
            raise RuntimeError("network down")
        return list(self.tasks)

    def create_task(self, item):
        if self.fail_create:
            raise RuntimeError("create failed")
        self._uid_seq += 1
        self.created.append(("task", dict(item)))
        return f"t-{self._uid_seq}"

    def complete_task(self, uid):
        self.completed.append(uid)

    def delete_task(self, uid):
        self.deleted.append(("task", uid))


@pytest.fixture
def engine(monkeypatch, tmp_path, cal_db_path):
    """calendar_sync with fake provider, tmp state dir, enabled config."""
    fake = FakeProvider()
    monkeypatch.setattr(calendar_sync, "provider", fake)
    monkeypatch.setenv("CALENDAR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(calendar_sync, "CFG", {
        "enabled": True, "lookahead_days": 10, "refresh_minutes": 15})
    return fake, cal_db_path


class TestSyncEngine:
    def test_push_pending_creates_remote(self, engine):
        fake, db = engine
        ev = _add_event(db)
        tk = _add_task(db)
        pushed, errors = calendar_sync.push_pending(db_path=db)
        assert pushed == 2 and errors == []
        assert [k for k, _ in fake.created] == ["event", "task"]
        assert cal_db.get_item(ev, db_path=db)["uid"] == "ev-1"
        assert cal_db.get_item(tk, db_path=db)["uid"] == "t-2"
        assert cal_db.pending(db_path=db) == []

    def test_push_pending_done_and_cancelled(self, engine):
        fake, db = engine
        tk = _add_task(db)
        cal_db.mark_synced(tk, uid="t-9", db_path=db)
        cal_db.set_status(tk, "done", db_path=db)
        ev = _add_event(db)
        cal_db.mark_synced(ev, uid="ev-9", db_path=db)
        cal_db.set_status(ev, "cancelled", db_path=db)
        never_pushed = _add_task(db, title="本地即取消")
        cal_db.set_status(never_pushed, "cancelled", db_path=db)
        pushed, errors = calendar_sync.push_pending(db_path=db)
        assert errors == []
        assert fake.completed == ["t-9"]
        assert ("event", "ev-9") in fake.deleted
        assert fake.created == []           # 从未上云的取消项不会先创建再删除
        assert cal_db.pending(db_path=db) == []

    def test_push_failure_isolated_per_row(self, engine):
        fake, db = engine
        a = _add_event(db)
        b = _add_task(db)
        cal_db.mark_synced(b, uid="t-1", db_path=db)
        cal_db.set_status(b, "done", db_path=db)
        fake.fail_create = True             # 只让 create 失败，complete 正常
        pushed, errors = calendar_sync.push_pending(db_path=db)
        assert pushed == 1 and len(errors) == 1
        assert cal_db.get_item(a, db_path=db)["synced"] == 0   # 留待下轮重试
        assert cal_db.get_item(b, db_path=db)["synced"] == 1

    def test_refresh_pulls_and_reconciles(self, engine):
        fake, db = engine
        # 本地已同步：ev-keep 仍在远端，ev-gone 已被远端删除（窗口内）
        keep = _add_event(db, title="保留", start="2026-06-14T10:00", end="")
        cal_db.mark_synced(keep, uid="ev-keep", db_path=db)
        gone = _add_event(db, title="远端已删", start="2026-06-15T10:00", end="")
        cal_db.mark_synced(gone, uid="ev-gone", db_path=db)
        future = _add_event(db, title="窗口外不动", start="2026-07-20T10:00", end="")
        cal_db.mark_synced(future, uid="ev-future", db_path=db)
        tk = _add_task(db, title="远端已完成")
        cal_db.mark_synced(tk, uid="t-done", db_path=db)
        fake.events = [
            {"uid": "ev-keep", "title": "保留(改名)", "start": "2026-06-14T10:00",
             "end": "2026-06-14T11:00", "all_day": False, "location": "", "notes": ""},
            {"uid": "ev-new", "title": "远端新活动", "start": "2026-06-16",
             "end": "2026-06-17", "all_day": True, "location": "", "notes": ""},
        ]
        fake.tasks = [
            {"uid": "t-done", "title": "远端已完成", "due": "2026-06-15",
             "notes": "", "done": True},
        ]
        result = calendar_sync.refresh(db_path=db, today=TODAY)
        assert result["errors"] == []
        assert cal_db.get_item(keep, db_path=db)["title"] == "保留(改名)"
        assert cal_db.get_item(gone, db_path=db)["status"] == "cancelled"
        assert cal_db.get_item(gone, db_path=db)["synced"] == 1
        assert cal_db.get_item(future, db_path=db)["status"] == "active"
        assert cal_db.get_item(tk, db_path=db)["status"] == "done"
        new_rows = [r for r in cal_db.list_upcoming(days=10, today=TODAY, db_path=db)
                    if r["uid"] == "ev-new"]
        assert len(new_rows) == 1 and new_rows[0]["origin"] == "remote"

    def test_refresh_records_error_and_continues(self, engine):
        fake, db = engine
        fake.fail_list = True
        result = calendar_sync.refresh(db_path=db, today=TODAY)
        assert result["errors"]
        st = calendar_sync.status(db_path=db)
        assert st["last_error"]
        assert st["last_refresh"]           # 失败也记尝试时间（节流按尝试算）

    def test_status_counts_pending(self, engine):
        fake, db = engine
        _add_event(db)
        st = calendar_sync.status(db_path=db)
        assert st["enabled"] is True and st["configured"] is True
        assert st["pending"] == 1


# ── 按成员 + 域同步（tick 遍历成员） ────────────────────────────


@pytest.fixture
def member_engine(monkeypatch, tmp_path):
    """tick 测试：MemberA 两域都接 fake provider，数据根指向 tmp。"""
    fake = FakeProvider()
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(calendar_sync, "_registered_members", lambda: ["MemberA"])
    monkeypatch.setattr(calendar_sync, "provider_for",
                        lambda m, d: fake if m == "MemberA" else None)
    monkeypatch.setattr(calendar_sync, "CFG",
                        {"enabled": True, "lookahead_days": 10, "refresh_minutes": 15})
    return fake, tmp_path


class TestPerMemberTick:
    def test_tick_pushes_member_store_and_throttles(self, member_engine):
        import paths
        from datetime import datetime as dt
        fake, tmp = member_engine
        sdb = str(paths.member_store("MemberA", "schedule"))
        ev = cal_db.add_item(kind="event", title="X", start_at="2026-06-13T10:00",
                             member="MemberA", db_path=sdb)
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 0)) is True
        assert cal_db.get_item(ev, db_path=sdb)["uid"]            # pushed to remote
        assert ("event", ) and any(k == "event" for k, _ in fake.created)
        # 节流窗口内 → 不跑
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 10)) is False
        # 超过节流 → 再跑
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 16)) is True

    def test_tick_local_member_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
        monkeypatch.setattr(calendar_sync, "_registered_members", lambda: ["LocalOnly"])
        monkeypatch.setattr(calendar_sync, "provider_for", lambda m, d: None)
        monkeypatch.setattr(calendar_sync, "CFG",
                            {"enabled": True, "lookahead_days": 10, "refresh_minutes": 15})
        from datetime import datetime as dt
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 0)) is False

    def test_tick_disabled_returns_false(self, member_engine, monkeypatch):
        from datetime import datetime as dt
        monkeypatch.setattr(calendar_sync, "CFG", {**calendar_sync.CFG, "enabled": False})
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 0)) is False

    def test_tick_unconfigured_provider_skipped(self, member_engine):
        from datetime import datetime as dt
        fake, tmp = member_engine
        fake.configured = False
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 0)) is False

    def test_tick_never_raises(self, member_engine, monkeypatch):
        import paths
        from datetime import datetime as dt
        fake, tmp = member_engine
        monkeypatch.setattr(calendar_sync, "refresh_domain",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert calendar_sync.calendar_tick(now=dt(2026, 6, 12, 9, 0)) is False
        st = calendar_sync._load_state(paths.member_sync_state("MemberA", "schedule"))
        assert st.get("last_error")


# ── CLI（subprocess，环境变量全隔离） ───────────────────────────

import os
import subprocess
import sys as _sys
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / ".codewhale" / "skills" / "Calendar_Keeper" / "cli.py"

D1 = (date.today() + timedelta(days=1)).isoformat()   # 明天（窗口内，不依赖死日期）
D3 = (date.today() + timedelta(days=3)).isoformat()


def _cli(args, db, tmp):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GCAL_")}
    env["CAL_DB_PATH"] = db
    env["CALENDAR_STATE_DIR"] = str(tmp)
    env["CALENDAR_CONFIG"] = str(tmp / "no-such-config.json")
    env["BACKUP_STATE_DIR"] = str(tmp)      # mark_dirty 状态文件隔离
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([_sys.executable, str(_CLI)] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, cwd=str(_ROOT))


class TestCli:
    def test_cal_add_event_and_list(self, cal_db_path, tmp_path):
        r = _cli(["cal-add", "--member", "MemberA", "--kind", "event",
                  "--title", "游泳课", "--date", D1, "--start", "14:00",
                  "--end", "15:00", "--location", "泳馆"], cal_db_path, tmp_path)
        assert r.returncode == 0, r.stderr
        assert "#1" in r.stdout and "待同步" in r.stdout
        item = cal_db.get_item(1, db_path=cal_db_path)
        assert item["start_at"] == f"{D1}T14:00"
        assert item["end_at"] == f"{D1}T15:00"
        out = _cli(["cal-list"], cal_db_path, tmp_path)
        assert "游泳课" in out.stdout and "泳馆" in out.stdout

    def test_cal_add_task_undated(self, cal_db_path, tmp_path):
        r = _cli(["cal-add", "--member", "MemberA", "--kind", "task",
                  "--title", "买蛋糕"], cal_db_path, tmp_path)
        assert r.returncode == 0, r.stderr
        assert cal_db.get_item(1, db_path=cal_db_path)["start_at"] == ""

    def test_cal_add_event_requires_date(self, cal_db_path, tmp_path):
        r = _cli(["cal-add", "--member", "M", "--kind", "event",
                  "--title", "没日期"], cal_db_path, tmp_path)
        assert r.returncode != 0

    def test_cal_add_requires_member(self, cal_db_path, tmp_path):
        r = _cli(["cal-add", "--kind", "task", "--title", "x"],
                 cal_db_path, tmp_path)
        assert r.returncode != 0

    def test_cal_list_empty(self, cal_db_path, tmp_path):
        r = _cli(["cal-list"], cal_db_path, tmp_path)
        assert r.returncode == 0
        assert "无日程" in r.stdout

    def test_cal_done_task_only(self, cal_db_path, tmp_path):
        tk = _add_task(cal_db_path, due=D3)
        ev = _add_event(cal_db_path, start=f"{D1}T09:00", end="")
        r = _cli(["cal-done", "--id", str(tk)], cal_db_path, tmp_path)
        assert r.returncode == 0 and "已完成" in r.stdout
        assert cal_db.get_item(tk, db_path=cal_db_path)["status"] == "done"
        r2 = _cli(["cal-done", "--id", str(ev)], cal_db_path, tmp_path)
        assert r2.returncode != 0          # 活动不能"完成"，要用 cal-delete
        r3 = _cli(["cal-done", "--id", "999"], cal_db_path, tmp_path)
        assert r3.returncode != 0

    def test_cal_delete_cancels(self, cal_db_path, tmp_path):
        ev = _add_event(cal_db_path, start=f"{D1}T09:00", end="")
        r = _cli(["cal-delete", "--id", str(ev)], cal_db_path, tmp_path)
        assert r.returncode == 0 and "已取消" in r.stdout
        assert cal_db.get_item(ev, db_path=cal_db_path)["status"] == "cancelled"
        r2 = _cli(["cal-delete", "--id", "999"], cal_db_path, tmp_path)
        assert r2.returncode != 0

    def test_cal_status_reports_pending(self, cal_db_path, tmp_path):
        _add_event(cal_db_path, start=f"{D1}T09:00", end="")
        r = _cli(["cal-status"], cal_db_path, tmp_path)
        assert r.returncode == 0
        assert "待同步: 1" in r.stdout
        assert "未配置" in r.stdout        # GCAL_* 在测试环境里被清掉

    def test_cal_sync_unconfigured(self, cal_db_path, tmp_path):
        r = _cli(["cal-sync"], cal_db_path, tmp_path)
        assert r.returncode == 0
        assert "未配置" in r.stdout


def _cli_member(args, data_root, tmp):
    """无 CAL_DB_PATH 覆盖：按成员路由到 data/<dir>/{schedule,tasks}/*.db。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GCAL_")}
    env["DATA_ROOT"] = str(data_root)
    env["CALENDAR_STATE_DIR"] = str(tmp)
    env["CALENDAR_CONFIG"] = str(tmp / "no-such-config.json")
    env["BACKUP_STATE_DIR"] = str(tmp)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([_sys.executable, str(_CLI)] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, cwd=str(_ROOT))


class TestCliPerMember:
    """无覆盖时事件入 schedule.db、待办入 tasks.db，按成员私有。依赖真实 members.json。"""

    def test_event_and_task_go_to_separate_stores(self, tmp_path):
        data_root = tmp_path / "data"
        r = _cli_member(["cal-add", "--member", "Jim Zheng", "--kind", "event",
                         "--title", "游泳课", "--date", D1, "--start", "14:00"],
                        data_root, tmp_path)
        assert r.returncode == 0, r.stderr
        r = _cli_member(["cal-add", "--member", "Jim Zheng", "--kind", "task",
                         "--title", "买蛋糕"], data_root, tmp_path)
        assert r.returncode == 0, r.stderr
        assert (data_root / "Jim" / "schedule" / "schedule.db").exists()
        assert (data_root / "Jim" / "tasks" / "tasks.db").exists()
        # event lives in schedule.db only
        ev = cal_db.list_upcoming(days=10, today=date.today(),
                                  db_path=str(data_root / "Jim" / "schedule" / "schedule.db"))
        assert [r["title"] for r in ev] == ["游泳课"]
        tk = cal_db.list_upcoming(days=10, today=date.today(), include_closed=True,
                                  db_path=str(data_root / "Jim" / "tasks" / "tasks.db"))
        assert [r["title"] for r in tk] == ["买蛋糕"]

    def test_list_merges_member_stores(self, tmp_path):
        data_root = tmp_path / "data"
        _cli_member(["cal-add", "--member", "Jim Zheng", "--kind", "event",
                     "--title", "游泳课", "--date", D1, "--start", "14:00"],
                    data_root, tmp_path)
        _cli_member(["cal-add", "--member", "Jim Zheng", "--kind", "task",
                     "--title", "买蛋糕"], data_root, tmp_path)
        out = _cli_member(["cal-list", "--member", "Jim Zheng"], data_root, tmp_path)
        assert out.returncode == 0, out.stderr
        assert "游泳课" in out.stdout and "买蛋糕" in out.stdout


# ── Agent 接线（agent_core） ────────────────────────────────────

_TOOLS = ("add_event", "add_task", "list_schedule", "complete_task",
          "remove_schedule_item", "sync_calendar", "calendar_status")
_COMMANDS = {"cal-add", "cal-list", "cal-done", "cal-delete",
             "cal-sync", "cal-status"}


class TestAgentWiring:
    def test_tools_registered(self):
        import agent_core
        names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        for n in _TOOLS:
            assert n in names, n
            assert n in agent_core._TOOL_MAP, n

    def test_cal_commands_routed_and_allowed(self):
        import agent_core
        path = agent_core._cli_path("cal-add")
        assert path.parts[-2:] == ("Calendar_Keeper", "cli.py")
        assert _COMMANDS <= agent_core.ALLOWED_COMMANDS

    def test_member_injected_on_schedule_writes(self):
        import agent_core
        out = agent_core._apply_member("add_event",
                                       {"member": "假冒", "title": "x"}, "MemberA")
        assert out["member"] == "MemberA"
        out2 = agent_core._apply_member("add_task", {"title": "y"}, "MemberA")
        assert out2["member"] == "MemberA"

    def test_schedule_context_formats_and_empty(self, cal_db_path):
        import agent_core
        assert agent_core._schedule_context(db_path=cal_db_path) == ""
        _add_event(cal_db_path, title="游泳课",
                   start=f"{D1}T14:00", end=f"{D1}T15:00", location="泳馆")
        _add_task(cal_db_path, title="买蛋糕", due=D3)
        block = agent_core._schedule_context(db_path=cal_db_path)
        assert "游泳课" in block and "@泳馆" in block
        assert "☐ 买蛋糕" in block
        assert "不要主动播报" in block      # 防刷屏规则随块注入
