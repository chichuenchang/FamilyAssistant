# tests/test_knowking_agent.py — KnowKing 工具在 Agent 端的接线。
#
# 断言：工具在 schema/map 里挂上；Agent 带 channel；工具把频道上下文注入并调
# knowking_jobs.submit；缺上下文时报错；只在显式关键词触发（描述里声明）。
import sys
from pathlib import Path

AR = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"
sys.path.insert(0, str(AR))

import agent_core as ac
import knowking_jobs as kj


class TestRegistration:
    def test_knowking_in_schema_and_map(self):
        schema_names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
        assert "knowking" in schema_names
        assert "knowking" in ac._TOOL_MAP

    def test_knowking_schema_requires_topic(self):
        fn = next(t["function"] for t in ac.TOOL_SCHEMAS
                  if t["function"]["name"] == "knowking")
        assert fn["parameters"]["required"] == ["topic"]

    def test_knowking_description_names_trigger_keywords(self):
        fn = next(t["function"] for t in ac.TOOL_SCHEMAS
                  if t["function"]["name"] == "knowking")
        desc = fn["function"]["description"] if "function" in fn else fn["description"]
        for kw in ("knowking", "kk", "懂王"):
            assert kw in desc

    def test_knowking_not_a_cli_command(self):
        # bespoke tool (own subprocess/uv); must NOT be routed as a skill CLI command
        assert "knowking" not in ac.ALLOWED_COMMANDS


class TestChannel:
    def test_agent_stores_channel(self):
        assert ac.Agent(channel="telegram").channel == "telegram"
        assert ac.Agent(channel="wechat").channel == "wechat"

    def test_agent_channel_defaults_empty(self):
        assert ac.Agent().channel == ""


class TestContextInjection:
    def test_apply_context_injects_channel_user_member_for_knowking(self):
        out = ac._apply_context("knowking", {"topic": "X"}, "telegram", "555", "Jim")
        assert out["__channel"] == "telegram"
        assert out["__user"] == "555"
        assert out["member"] == "Jim"
        assert out["topic"] == "X"

    def test_apply_context_noop_for_other_tools(self):
        out = ac._apply_context("web_search", {"query": "X"}, "telegram", "555", "Jim")
        assert "__channel" not in out and "__user" not in out


class TestToolBehaviour:
    def test_tool_submits_and_returns_ack(self, monkeypatch):
        seen = {}

        def fake_submit(topic, channel, user, member, **kw):
            seen.update(topic=topic, channel=channel, user=user, member=member)
            return "job-1", "🔎 已开始"

        monkeypatch.setattr(kj, "submit", fake_submit)
        out = ac._tool_knowking({"topic": "大家怎么看 X", "__channel": "telegram",
                                 "__user": "555", "member": "Jim Zheng"})
        assert out == "🔎 已开始"
        assert seen == {"topic": "大家怎么看 X", "channel": "telegram",
                        "user": "555", "member": "Jim Zheng"}

    def test_tool_requires_channel_context(self, monkeypatch):
        # missing __channel/__user (e.g. local test mode) → clear error, no submit
        called = {"n": 0}
        monkeypatch.setattr(kj, "submit",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ("x", "y"))
        out = ac._tool_knowking({"topic": "X", "member": "Jim"})
        assert out.startswith("[错误]")
        assert called["n"] == 0

    def test_tool_empty_topic_errors(self):
        out = ac._tool_knowking({"topic": "  ", "__channel": "telegram", "__user": "5"})
        assert out.startswith("[错误]")


class TestTransportWiring:
    """两个传输层都挂上了投递钩子（真正的 poll_and_deliver），且用各自频道名。"""

    def test_telegram_wires_delivery_hook(self):
        import telegram_bot
        assert telegram_bot._knowking_deliver is kj.poll_and_deliver

    def test_wechat_wires_delivery_hook(self):
        import wechat_ilink
        assert wechat_ilink._knowking_deliver is kj.poll_and_deliver

    def test_delivery_hook_routes_seeded_job(self, tmp_path, monkeypatch):
        # 端到端投递：种一个 done 任务 → 传输层引用的钩子把它发出去。
        import json
        import telegram_bot
        jdir = tmp_path / ".knowking_jobs"
        jdir.mkdir()
        (jdir / "j.json").write_text(json.dumps({
            "id": "j", "channel": "telegram", "user": "42", "member": "Jim",
            "topic": "TP", "status": "done", "report": "R", "error": "",
            "created_at": kj._now()}), encoding="utf-8")
        monkeypatch.setattr(kj, "jobs_dir", lambda jd=None: jdir)
        sent = []
        telegram_bot._knowking_deliver(lambda u, t: sent.append((u, t)), "telegram")
        assert sent and sent[0][0] == "42" and "R" in sent[0][1]
