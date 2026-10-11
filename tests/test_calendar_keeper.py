# tests/test_calendar_keeper.py — Calendar Keeper：Google provider、注册表、Agent 接线。
# remote_schedule 与 CLI 见 tests/test_remote_schedule.py。无真实姓名/凭据。
import json
import os
from datetime import date, timedelta

import pytest

import calendar_provider


@pytest.fixture(autouse=True)
def _no_real_gcal(monkeypatch):
    """Hard guard: no test in this module may reach the real Google Calendar/Tasks.

    Strips inherited GCAL_* before every test. Provider tests set their own fake
    creds via the ``gcal`` fixture, which depends on this one so it runs after.
    """
    for k in list(os.environ):
        if k.startswith("GCAL_"):
            monkeypatch.delenv(k, raising=False)


# ── Google provider（HTTP 全部打桩，零网络） ─────────────────────


@pytest.fixture
def gcal(monkeypatch, _no_real_gcal):
    """Google provider with env creds set and HTTP layer faked.

    Depends on ``_no_real_gcal`` so inherited real creds are stripped first,
    then these fakes are set — provider tests stay hermetic regardless of shell.
    """
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
    def test_update_event_patches_and_clears_other_time_kind(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b"{}"), (200, b"{}")]
        calendar_provider.update_event("ev1", {"title": "游泳课", "start_at": "2026-06-13T14:00",
                                               "end_at": "", "all_day": 0,
                                               "location": "", "notes": "带泳镜"})
        assert calls[0]["method"] == "PATCH" and calls[0]["url"].endswith("/events/ev1")
        body = json.loads(calls[0]["data"])
        assert body["start"] == {"dateTime": "2026-06-13T14:00:00+00:00", "date": None}
        assert body["end"]["dateTime"] == "2026-06-13T15:00:00+00:00"
        assert body["description"] == "带泳镜" and body["location"] == ""
        calendar_provider.update_task("t1", {"title": "买蛋糕", "start_at": "", "notes": ""})
        body = json.loads(calls[1]["data"])
        assert calls[1]["method"] == "PATCH" and body["due"] is None

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

    def test_update_multiday_allday_keeps_span(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, b"{}")]
        calendar_provider.update_event("ev1", {"title": "露营", "start_at": "2026-06-15",
                                               "end_at": "2026-06-17", "all_day": 1,
                                               "location": "", "notes": ""})
        body = json.loads(calls[0]["data"])
        assert body["start"]["date"] == "2026-06-15"
        assert body["end"]["date"] == "2026-06-18"   # end_at 含当日 → Google 独占

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

    def test_get_event_maps_like_list(self, gcal):
        fake, calls = gcal
        fake.responses = [(200, json.dumps(
            {"id": "ev1", "summary": "游泳课", "location": "泳馆",
             "start": {"dateTime": "2026-06-13T14:00:00+00:00"},
             "end": {"dateTime": "2026-06-13T15:00:00+00:00"}}).encode())]
        assert calendar_provider.get_event("ev1") == {
            "uid": "ev1", "title": "游泳课", "start": "2026-06-13T14:00",
            "end": "2026-06-13T15:00", "all_day": False, "location": "泳馆", "notes": ""}
        assert calls[0]["method"] == "GET" and calls[0]["url"].endswith("/events/ev1")

    def test_get_event_missing_or_cancelled_is_none(self, gcal):
        fake, _ = gcal
        fake.responses = [(404, b"gone"), (200, json.dumps(
            {"id": "ev2", "status": "cancelled", "start": {"date": "2026-06-15"},
             "end": {"date": "2026-06-16"}}).encode())]
        assert calendar_provider.get_event("gone") is None
        assert calendar_provider.get_event("ev2") is None

    def test_get_task_maps_and_missing(self, gcal):
        fake, calls = gcal
        fake.responses = [
            (200, json.dumps({"id": "t1", "title": "买蛋糕", "due": "2026-06-15T00:00:00.000Z",
                              "status": "completed"}).encode()),
            (410, b"gone"),
            (200, json.dumps({"id": "t3", "title": "x", "deleted": True}).encode())]
        assert calendar_provider.get_task("t1") == {
            "uid": "t1", "title": "买蛋糕", "due": "2026-06-15", "notes": "", "done": True}
        assert calls[0]["method"] == "GET" and calls[0]["url"].endswith("/tasks/t1")
        assert calendar_provider.get_task("t2") is None
        assert calendar_provider.get_task("t3") is None

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


# ── Agent 工具路由（run_cli 打桩，不起子进程） ─────────────────

D1 = (date.today() + timedelta(days=1)).isoformat()   # 明天（窗口内，不依赖死日期）
D3 = (date.today() + timedelta(days=3)).isoformat()


class TestForMemberRouting:
    """默认归发送者；显式 for-member（须已登记）路由到目标成员；来图不搬迁、不透传。"""

    @pytest.fixture
    def seen(self, tmp_path, monkeypatch):
        import members
        import tool_runtime as rt
        mp = tmp_path / "members.json"
        mp.write_text(json.dumps({"Alex Lee": {"dir": "Alex"}, "Robin": {"dir": "Robin"}}),
                      encoding="utf-8")
        monkeypatch.setattr(members, "MEMBERS_PATH", mp)
        calls = []
        monkeypatch.setattr(rt, "run_cli", lambda cmd, args=None: calls.append((cmd, args)) or "ok")
        return calls

    def _tools(self):
        import agent_core
        return agent_core.REGISTRY.modules["Calendar_Keeper"]

    def test_event_defaults_to_sender(self, seen):
        self._tools().tool_add_event({"member": "Alex Lee", "title": "关于Robin的活动", "date": D1})
        assert seen == [("cal-add", {"member": "Alex Lee", "title": "关于Robin的活动",
                                     "date": D1, "kind": "event"})]

    def test_event_for_member_routes_to_target(self, seen):
        self._tools().tool_add_event({"member": "Alex Lee", "for-member": "Robin",
                                      "title": "Robin recital", "date": D1})
        assert seen[0][1]["member"] == "Robin" and "for-member" not in seen[0][1]

    def test_task_due_becomes_date_and_routes(self, seen):
        self._tools().tool_add_task({"member": "Alex Lee", "for-member": "Robin",
                                     "title": "Robin homework", "due": D3})
        assert seen == [("cal-add", {"member": "Robin", "title": "Robin homework",
                                     "date": D3, "kind": "task"})]

    def test_for_member_unregistered_falls_back_to_sender(self, seen):
        self._tools().tool_add_event({"member": "Alex Lee", "for-member": "Stranger",
                                      "title": "X", "date": D1})
        assert seen[0][1]["member"] == "Alex Lee"

    def test_source_image_not_passed_or_moved(self, seen, tmp_path):
        img = tmp_path / "invite.jpg"
        img.write_bytes(b"x")
        self._tools().tool_add_event({"member": "Alex Lee", "title": "Party", "date": D1,
                                      "source-image": str(img)})
        assert "source-image" not in seen[0][1] and img.exists()


# ── Agent 接线（agent_core） ────────────────────────────────────

_TOOLS = ("add_event", "add_task", "list_schedule", "complete_task",
          "remove_schedule_item", "update_schedule_item", "calendar_status")
_COMMANDS = {"cal-add", "cal-list", "cal-update", "cal-done", "cal-delete", "cal-status"}


def _desc(name):
    import agent_core
    return next(t["function"] for t in agent_core.TOOL_SCHEMAS
                if t["function"]["name"] == name)


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
        assert "cal-sync" not in agent_core.ALLOWED_COMMANDS

    def test_member_injected_on_schedule_writes(self):
        import agent_core
        out = agent_core._apply_member("add_event",
                                       {"member": "假冒", "title": "x"}, "MemberA")
        assert out["member"] == "MemberA"
        out2 = agent_core._apply_member("add_task", {"title": "y"}, "MemberA")
        assert out2["member"] == "MemberA"

    def test_member_forced_on_read_and_mutate_tools(self):
        # 回归：这五个工具历史上不在任何强制集 → member="" → CLI 报 "需要 --member"。必须强制注入发送者。
        import agent_core
        for tool in ("complete_task", "remove_schedule_item", "list_schedule",
                     "update_schedule_item", "calendar_status"):
            out = agent_core._apply_member(tool, {"member": "假冒", "id": "t:1"}, "MemberA")
            assert out.get("member") == "MemberA", tool

    def test_prompt_forces_tool_on_schedule_query(self):
        import agent_core
        p = agent_core._build_system_prompt()
        assert "必须调 list_schedule" in p
        assert "本地↔远端" not in p and "日程快照" not in p   # 缓存时代的话术已退役

    def test_list_schedule_exposes_history_window(self):
        # 回归：schema 只有 days（未来窗口）→ LLM 无从查历史，直接答"我只能看到未来日程"
        fn = _desc("list_schedule")
        props = fn["parameters"]["properties"]
        assert "from" in props and "to" in props
        assert "历史" in fn["description"] or "过去" in props["from"]["description"]

    def test_prompt_tells_agent_history_is_queryable(self):
        import agent_core
        p = agent_core._build_system_prompt()
        assert "历史日程一样能查" in p
        assert "查不到历史" in p          # 明令禁止那句错话

    def test_ids_described_with_remote_prefix(self):
        for name in ("update_schedule_item", "complete_task", "remove_schedule_item"):
            assert "e:" in _desc(name)["parameters"]["properties"]["id"]["description"] \
                or "t:" in _desc(name)["parameters"]["properties"]["id"]["description"], name

    def test_no_schedule_snapshot_injection_or_sync_tick(self):
        import agent_core
        cal = agent_core.REGISTRY.modules["Calendar_Keeper"]
        assert not getattr(cal, "CONTEXT_FNS", [])
        ticks = {getattr(f, "__name__", "") for f in cal.MESSAGE_TICKS}
        assert ticks == {"image_gc_tick"}

    def test_calendar_status_is_plain_status(self, monkeypatch):
        import agent_core
        import tool_runtime as rt
        seen = []
        monkeypatch.setattr(rt, "run_cli", lambda cmd, args=None: seen.append((cmd, args)) or "ok")
        agent_core._TOOL_MAP["calendar_status"]({"member": "MemberA"})
        assert seen == [("cal-status", {"member": "MemberA"})]
        assert "sync" not in _desc("calendar_status")["parameters"]["properties"]
