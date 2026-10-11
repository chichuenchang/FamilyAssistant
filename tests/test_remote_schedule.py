# tests/test_remote_schedule.py — 远程直连日程层（Calendar_Keeper/remote_schedule.py）。
# 无真实姓名/凭据；provider 全部为内存假实现。
from datetime import date, timedelta

import pytest

import remote_schedule as rs

D1 = (date.today() + timedelta(days=1)).isoformat()
D3 = (date.today() + timedelta(days=3)).isoformat()


class FakeProvider:
    """内存版 provider 契约：活动/待办存 dict，记录调用。"""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.windows = []
        self.updated = []
        self.configured = True
        self.fail = False
        self._seq = 0

    def _uid(self, p):
        self._seq += 1
        return f"{p}{self._seq}"

    def _check(self):
        if self.fail:
            raise RuntimeError("network down")

    def is_configured(self):
        return self.configured

    def list_events(self, tmin, tmax):
        self._check()
        self.windows.append((tmin, tmax))
        return list(self.events.values())

    def get_event(self, uid):
        self._check()
        return self.events.get(uid)

    def create_event(self, item):
        self._check()
        uid = self._uid("ev")
        self.events[uid] = {"uid": uid, "title": item["title"], "start": item["start_at"],
                            "end": item["end_at"], "all_day": bool(item["all_day"]),
                            "location": item["location"], "notes": item["notes"]}
        return uid

    def update_event(self, uid, item):
        self.updated.append(("event", uid, dict(item)))

    def delete_event(self, uid):
        self.events.pop(uid, None)

    def list_tasks(self):
        self._check()
        return list(self.tasks.values())

    def get_task(self, uid):
        self._check()
        return self.tasks.get(uid)

    def create_task(self, item):
        self._check()
        uid = self._uid("tk")
        self.tasks[uid] = {"uid": uid, "title": item["title"], "due": item["start_at"],
                           "notes": item["notes"], "done": False}
        return uid

    def update_task(self, uid, item):
        self.updated.append(("task", uid, dict(item)))

    def complete_task(self, uid):
        self.tasks[uid]["done"] = True

    def delete_task(self, uid):
        self.tasks.pop(uid, None)


@pytest.fixture
def fake(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(rs, "provider_for", lambda m, d: p if m == "MemberA" else None)
    monkeypatch.setattr(rs, "CFG", {"enabled": True, "lookahead_days": 10})
    return p


class TestProviderFor:
    def test_disabled_config_means_no_provider(self, monkeypatch):
        import members
        monkeypatch.setattr(members, "sync_pref",
                            lambda m, d: {"provider": "google_calendar", "enabled": True})
        monkeypatch.setattr(rs, "CFG", {"enabled": False})
        assert rs.provider_for("MemberA", "schedule") is None
        monkeypatch.setattr(rs, "CFG", {"enabled": True})
        assert rs.provider_for("MemberA", "schedule") is not None

    def test_member_without_sync_block_has_none(self, monkeypatch):
        import members
        monkeypatch.setattr(members, "sync_pref", lambda m, d: None)
        monkeypatch.setattr(rs, "CFG", {"enabled": True})
        assert rs.provider_for("MemberB", "tasks") is None


class TestRef:
    def test_round_trip_keeps_colons_in_uid(self):
        r = rs.ref("event", "abc_20261010T090000Z")
        assert r == "e:abc_20261010T090000Z"
        assert rs.parse_ref("#" + r) == ("event", "abc_20261010T090000Z")
        assert rs.parse_ref("t:x:y") == ("task", "x:y")

    @pytest.mark.parametrize("raw", ["e12", "12", "x:abc", "e:", ""])
    def test_old_or_bad_ids_rejected(self, raw):
        with pytest.raises(ValueError, match="id 格式不对"):
            rs.parse_ref(raw)


class TestNotConfigured:
    def test_placeholder_member_raises(self, fake):
        with pytest.raises(rs.NotConfigured, match="MemberB 未配置远程日历"):
            rs.list_items("MemberB")
        with pytest.raises(rs.NotConfigured):
            rs.add("MemberB", "event", "X", start_at=D1)

    def test_provider_without_credentials_raises(self, fake):
        fake.configured = False
        with pytest.raises(rs.NotConfigured):
            rs.list_items("MemberA", "task")

    def test_empty_member_rejected(self, fake):
        with pytest.raises(ValueError, match="--member"):
            rs.list_items("")

    def test_status_reports_each_domain(self, fake):
        assert rs.status("MemberA") == {"schedule": True, "tasks": True}
        assert rs.status("MemberB") == {"schedule": False, "tasks": False}


class TestAddAndList:
    def test_add_event_returns_ref_and_lists(self, fake):
        it = rs.add("MemberA", "event", "游泳课", start_at=f"{D1}T14:00",
                    end_at=f"{D1}T15:00", location="泳馆")
        assert it["id"].startswith("e:ev") and it["status"] == "active"
        rows = rs.list_items("MemberA", "event", date.today().isoformat(), D3)
        assert [r["title"] for r in rows] == ["游泳课"]
        assert rows[0]["location"] == "泳馆" and rows[0]["id"] == it["id"]

    def test_event_needs_date_and_title(self, fake):
        with pytest.raises(ValueError):
            rs.add("MemberA", "event", "无日期")
        with pytest.raises(ValueError):
            rs.add("MemberA", "task", "  ")

    def test_window_filters_events(self, fake):
        rs.add("MemberA", "event", "近", start_at=D1)
        rs.add("MemberA", "event", "远", start_at="2099-01-01")
        rows = rs.list_items("MemberA", "event", date.today().isoformat(), D3)
        assert [r["title"] for r in rows] == ["近"]

    def test_allday_end_is_inclusive(self, fake):
        # Google 全天 end 独占：单日全天活动不能漏进次日窗口
        fake.events["a"] = {"uid": "a", "title": "放假", "start": D1,
                            "end": (date.fromisoformat(D1) + timedelta(days=1)).isoformat(),
                            "all_day": True, "location": "", "notes": ""}
        nxt = (date.fromisoformat(D1) + timedelta(days=1)).isoformat()
        assert rs.list_items("MemberA", "event", nxt, nxt) == []
        assert rs.list_items("MemberA", "event", D1, D1)[0]["end_at"] == ""

    def test_tasks_undated_and_closed_flags(self, fake):
        rs.add("MemberA", "task", "无期限")
        rs.add("MemberA", "task", "后天", start_at=D3)
        done = rs.add("MemberA", "task", "做完了", start_at=D1)
        rs.complete("MemberA", done["id"])
        today = date.today().isoformat()
        assert [r["title"] for r in rs.list_items("MemberA", "task", today, D3)] == ["后天"]
        assert [r["title"] for r in rs.list_items("MemberA", "task", today, D3,
                                                  undated=True)] == ["后天", "无期限"]
        closed = rs.list_items("MemberA", "task", today, D3, closed=True)
        assert {r["title"]: r["status"] for r in closed} == {"做完了": "done", "后天": "active"}

    def test_no_window_tasks_include_overdue(self, fake):
        rs.add("MemberA", "task", "欠着", start_at="2020-01-01")
        assert [r["title"] for r in rs.list_items("MemberA", "task", undated=True)] == ["欠着"]

    def test_open_start_reaches_epoch_with_fixed_offset(self, fake):
        rs.list_items("MemberA", "event", end="2026-01-01")
        tmin, tmax = fake.windows[-1]
        assert tmin.startswith("1970-01-01T00:00:00") and tmax.startswith("2026-01-01T23:59:59")

    def test_mixed_kinds_sorted_undated_last(self, fake):
        rs.add("MemberA", "task", "无期限")
        rs.add("MemberA", "event", "晚", start_at=D3)
        rs.add("MemberA", "event", "早", start_at=D1)
        rows = rs.list_items("MemberA", start=date.today().isoformat(), end=D3, undated=True)
        assert [r["title"] for r in rows] == ["早", "晚", "无期限"]

    def test_provider_error_propagates(self, fake):
        fake.fail = True
        with pytest.raises(RuntimeError, match="network down"):
            rs.list_items("MemberA", "event")


class TestMutations:
    def test_update_merges_untouched_fields(self, fake):
        it = rs.add("MemberA", "event", "牙医", start_at=f"{D1}T09:00",
                    end_at=f"{D1}T10:00", location="诊所", notes="带卡")
        out = rs.update("MemberA", it["id"], {"title": "看牙"})
        kind, uid, sent = fake.updated[-1]
        assert (kind, uid) == ("event", it["id"][2:])
        assert sent["title"] == "看牙" and sent["location"] == "诊所" and sent["notes"] == "带卡"
        assert sent["start_at"] == f"{D1}T09:00" and out["title"] == "看牙"

    def test_update_done_task_refused(self, fake):
        it = rs.add("MemberA", "task", "T")
        rs.complete("MemberA", it["id"])
        with pytest.raises(ValueError, match="已完成"):
            rs.update("MemberA", it["id"], {"title": "改"})

    def test_complete_event_refused(self, fake):
        it = rs.add("MemberA", "event", "E", start_at=D1)
        with pytest.raises(ValueError, match="cal-delete"):
            rs.complete("MemberA", it["id"])

    def test_delete_returns_item_and_removes(self, fake):
        it = rs.add("MemberA", "event", "E", start_at=D1)
        assert rs.delete("MemberA", it["id"])["title"] == "E"
        assert rs.get("MemberA", it["id"]) is None

    def test_unknown_ref_not_found(self, fake):
        assert rs.get("MemberA", "t:nope") is None
        for op in (lambda: rs.update("MemberA", "t:nope", {"title": "x"}),
                   lambda: rs.complete("MemberA", "t:nope"),
                   lambda: rs.delete("MemberA", "e:nope")):
            with pytest.raises(rs.NotFound):
                op()
