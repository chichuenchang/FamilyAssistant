# tests/test_daily_banner.py — 每日早报：时间闸门、每日一次、失败退避、取数、成文兜底。
import json
from datetime import date, datetime

import pytest

import banner
import members

CFG = {"enabled": True, "time": "08:20", "catchup_until": "12:00", "lookahead_days": 3}
AT_0830 = datetime(2026, 9, 17, 8, 30)
REAL_BUILD = banner.build       # env 夹具会把 build 打桩掉


def _sync(f, *a):
    f(*a)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """临时 data_root + members.json：Alex opt-in（telegram 两个 id），Bo 未 opt-in。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({
        "Alex": {"telegram": ["1", "2"], "banner": True},
        "Bo": {"telegram": ["3"]},
    }), encoding="utf-8")
    monkeypatch.setattr(members, "MEMBERS_PATH", mp)
    monkeypatch.setattr(banner, "build", lambda member, day, cfg: f"brief {member}")
    banner._running.clear()
    banner._last_try.clear()
    banner._sent.clear()
    return mp


class Sent:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, cid, text):
        self.calls.append((cid, text))
        return self.ok


class TestWindow:
    @pytest.mark.parametrize("hm,expect", [((8, 19), False), ((8, 20), True),
                                           ((11, 59), True), ((12, 0), False)])
    def test_edges(self, hm, expect):
        assert banner.in_window(datetime(2026, 9, 17, *hm), CFG) is expect


class TestTick:
    def test_disabled_does_nothing(self, env):
        push = Sent()
        assert banner.tick(push, "telegram", now=AT_0830, cfg={**CFG, "enabled": False},
                           spawn=_sync) == []
        assert push.calls == []

    def test_outside_window_does_nothing(self, env):
        push = Sent()
        assert banner.tick(push, "telegram", now=datetime(2026, 9, 17, 7, 0), cfg=CFG,
                           spawn=_sync) == []

    def test_only_opted_in_member_gets_brief_on_every_id(self, env):
        push = Sent()
        assert banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync) == ["Alex"]
        assert push.calls == [("1", "brief Alex"), ("2", "brief Alex")]

    def test_once_per_day(self, env):
        push = Sent()
        banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        assert banner.tick(push, "telegram", now=datetime(2026, 9, 17, 9, 0), cfg=CFG,
                           spawn=_sync) == []
        assert len(push.calls) == 2

    def test_next_day_sends_again(self, env):
        push = Sent()
        banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        assert banner.tick(push, "telegram", now=datetime(2026, 9, 18, 8, 21), cfg=CFG,
                           spawn=_sync) == ["Alex"]

    def test_failed_push_not_recorded_and_backs_off(self, env, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(banner.time, "monotonic", lambda: clock[0])
        push = Sent(ok=False)
        assert banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync) == ["Alex"]
        clock[0] += 60
        assert banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync) == []
        clock[0] += banner.RETRY_S
        push.ok = True
        assert banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync) == ["Alex"]

    def test_push_exception_counts_as_failure(self, env):
        def boom(cid, text):
            raise OSError("down")
        banner.tick(boom, "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        assert "Alex" not in banner._load_state().get("telegram", {})

    def test_stale_state_read_does_not_resend(self, env, monkeypatch):
        push = Sent()
        banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        monkeypatch.setattr(banner, "_load_state", lambda: {})   # 读盘早于另一线程写入
        assert banner.tick(push, "telegram", now=datetime(2026, 9, 17, 9, 0), cfg=CFG,
                           spawn=_sync) == []
        assert len(push.calls) == 2

    def test_state_save_failure_does_not_resend(self, env, monkeypatch):
        def boom(*a):
            raise OSError("locked")
        monkeypatch.setattr(banner.jsonfile, "save", boom)
        push = Sent()
        banner.tick(push, "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        assert banner.tick(push, "telegram", now=datetime(2026, 9, 17, 11, 0), cfg=CFG,
                           spawn=_sync) == []
        assert len(push.calls) == 2

    def test_running_member_not_started_twice(self, env):
        started = []
        banner.tick(Sent(), "telegram", now=AT_0830, cfg=CFG,
                    spawn=lambda f, *a: started.append(a))
        assert banner.tick(Sent(), "telegram", now=AT_0830, cfg=CFG,
                           spawn=lambda f, *a: started.append(a)) == []
        assert len(started) == 1

    def test_build_exception_releases_guard(self, env, monkeypatch):
        def bad(member, day, cfg):
            raise RuntimeError("x")
        monkeypatch.setattr(banner, "build", bad)
        banner.tick(Sent(), "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        assert banner._running == set()

    def test_channels_tracked_separately(self, env, tmp_path):
        env.write_text(json.dumps({"Alex": {"telegram": ["1"], "wechat": ["w"], "banner": True}}),
                       encoding="utf-8")
        banner.tick(Sent(), "telegram", now=AT_0830, cfg=CFG, spawn=_sync)
        assert banner.tick(Sent(), "wechat", now=AT_0830, cfg=CFG, spawn=_sync) == ["Alex"]


# ── 取数 + 成文 ──────────────────────────────────────────────

import cal_db
import calendar_sync
import gmail_provider
import mail_rules
import paths
import tool_runtime as rt

DAY = date(2026, 9, 17)


@pytest.fixture
def data(env, monkeypatch):
    """Alex 的日程/待办库 + 远端刷新打桩（默认成功）。"""
    monkeypatch.setattr(calendar_sync, "refresh_range",
                        lambda member, domain, a, b: {"errors": []})
    monkeypatch.setitem(calendar_sync.CFG, "enabled", True)
    sched = str(paths.member_store("Alex", "schedule"))
    tasks = str(paths.member_store("Alex", "tasks"))
    return sched, tasks


class TestGather:
    def test_events_inside_window_only(self, data):
        sched, _ = data
        cal_db.add_item("event", "游泳课", "2026-09-19T14:00", location="Y", db_path=sched)
        cal_db.add_item("event", "太远", "2026-09-20T09:00", db_path=sched)
        cal_db.add_item("event", "昨天", "2026-09-16T09:00", db_path=sched)
        d = banner.gather("Alex", DAY, CFG)
        assert d.events == ["09-19 周六 14:00 游泳课 @Y"]

    def test_event_overflow_noted(self, data):
        sched, _ = data
        for i in range(banner.EVENT_CAP + 2):
            cal_db.add_item("event", f"e{i}", "2026-09-17T10:00", db_path=sched)
        d = banner.gather("Alex", DAY, CFG)
        assert len(d.events) == banner.EVENT_CAP + 1 and d.events[-1] == "…还有 2 项"

    def test_tasks_overdue_first_undated_last(self, data):
        _, tasks = data
        cal_db.add_item("task", "无期限", db_path=tasks)
        cal_db.add_item("task", "下周", "2026-09-25", db_path=tasks)
        cal_db.add_item("task", "欠着", "2026-09-10", db_path=tasks)
        d = banner.gather("Alex", DAY, CFG)
        assert d.tasks == ["欠着（逾期 09-10）", "下周（截止 09-25）", "无期限"]

    def test_task_overflow_counts_all(self, data):
        _, tasks = data
        for i in range(banner.TASK_CAP + 5):
            cal_db.add_item("task", f"t{i}", db_path=tasks)
        d = banner.gather("Alex", DAY, CFG)
        assert d.tasks[-1] == "…还有 5 项"
        assert f"待办 {banner.TASK_CAP + 5} 项" in banner.template(d)

    def test_done_task_excluded(self, data):
        _, tasks = data
        tid = cal_db.add_item("task", "做完了", "2026-09-17", db_path=tasks)
        cal_db.set_status(tid, "done", db_path=tasks)
        assert banner.gather("Alex", DAY, CFG).tasks == []

    def test_refresh_error_marks_stale(self, data, monkeypatch):
        monkeypatch.setattr(calendar_sync, "refresh_range",
                            lambda *a: {"errors": ["HTTP 500"]})
        assert banner.gather("Alex", DAY, CFG).stale

    def test_refresh_exception_marks_stale(self, data, monkeypatch):
        def boom(*a):
            raise OSError("offline")
        monkeypatch.setattr(calendar_sync, "refresh_range", boom)
        assert banner.gather("Alex", DAY, CFG).stale

    def test_calendar_disabled_skips_refresh(self, data, monkeypatch):
        monkeypatch.setitem(calendar_sync.CFG, "enabled", False)
        monkeypatch.setattr(calendar_sync, "refresh_range",
                            lambda *a: pytest.fail("不该刷新"))
        assert not banner.gather("Alex", DAY, CFG).stale

    def test_no_mail_block_means_no_mail(self, data):
        assert banner.gather("Alex", DAY, CFG).mails is None

    def test_unread_mail_minus_muted(self, data, env, monkeypatch):
        env.write_text(json.dumps({"Alex": {"telegram": ["1"], "banner": True,
                                            "mail": {"enabled": True}}}), encoding="utf-8")
        monkeypatch.setattr(gmail_provider, "is_configured", lambda prefix: True)
        seen = {}

        def search(q, n, prefix):
            seen["q"] = q
            return [{"from": "Bank <a@bank.com>", "subject": "账单"},
                    {"from": "Shop <x@shop.example>", "subject": "促销"}]
        monkeypatch.setattr(gmail_provider, "search", search)
        mail_rules.add("Alex", kind="domain", value="shop.example")
        d = banner.gather("Alex", DAY, CFG)
        assert seen["q"] == banner.MAIL_QUERY
        assert d.mails == ["Bank <a@bank.com>｜账单"]

    def test_mail_failure_drops_only_mail(self, data, env, monkeypatch):
        sched, _ = data
        cal_db.add_item("event", "会", "2026-09-17T10:00", db_path=sched)
        env.write_text(json.dumps({"Alex": {"telegram": ["1"], "banner": True,
                                            "mail": {"enabled": True}}}), encoding="utf-8")
        monkeypatch.setattr(gmail_provider, "is_configured", lambda prefix: True)

        def boom(*a):
            raise OSError("gmail down")
        monkeypatch.setattr(gmail_provider, "search", boom)
        d = banner.gather("Alex", DAY, CFG)
        assert d.mails is None and d.events


def _digest(**kw):
    base = dict(day=DAY, events=[], tasks=[], mails=None, stale=False)
    return banner.Digest(**{**base, **kw})


class TestCompose:
    def test_empty_digest_skips_llm(self):
        out = banner.compose(_digest(), chat=lambda *a: pytest.fail("不该调 LLM"))
        assert "无" in out

    def test_llm_text_used(self):
        out = banner.compose(_digest(events=["09-17 周四 10:00 会"]),
                             chat=lambda *a: {"content": " 早报正文 "})
        assert out == "早报正文"

    @pytest.mark.parametrize("reply", [None, {"content": ""}])
    def test_llm_empty_falls_back_to_template(self, reply):
        out = banner.compose(_digest(events=["09-17 周四 10:00 会"]), chat=lambda *a: reply)
        assert "09-17 周四 10:00 会" in out

    def test_llm_exception_falls_back(self):
        def boom(*a):
            raise RuntimeError("x")
        out = banner.compose(_digest(tasks=["交表"]), chat=boom)
        assert "交表" in out

    def test_llm_called_without_tools(self):
        got = {}

        def chat(messages, tools, model, effort):
            got["tools"] = tools
            return {"content": "ok"}
        banner.compose(_digest(tasks=["交表"]), chat=chat)
        assert got["tools"] is None

    def test_remote_text_fenced(self):
        text = banner.llm_input(_digest(events=["e"], tasks=["t"], mails=["m｜s"]))
        assert text.count(rt.FENCE_NONCE) == 6

    def test_template_shows_stale_warning(self):
        assert "远端同步失败" in banner.template(_digest(tasks=["t"], stale=True))

    def test_template_notes_mail_zero_when_configured(self):
        assert "未读邮件 0" in banner.template(_digest(tasks=["t"], mails=[]))

    def test_build_wires_gather_and_compose(self, data, monkeypatch):
        sched, _ = data
        cal_db.add_item("event", "会", "2026-09-17T10:00", db_path=sched)
        monkeypatch.setattr(banner, "compose", lambda d, chat=None: f"n={len(d.events)}")
        assert REAL_BUILD("Alex", DAY, CFG) == "n=1"


def test_registered_as_fast_tick():
    import skill_registry
    assert banner.tick in skill_registry.load().fast_ticks
