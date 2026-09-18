# tests/test_daily_banner.py — 每日早报：时间闸门、每日一次、失败退避、取数、成文兜底。
import json
from datetime import date, datetime

import pytest

import banner
import members

CFG = {"enabled": True, "time": "08:20", "catchup_until": "12:00", "lookahead_days": 3}
AT_0830 = datetime(2026, 9, 17, 8, 30)


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
