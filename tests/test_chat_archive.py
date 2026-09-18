# tests/test_chat_archive.py — 移出上下文的对话进内存档案，chat_history 工具回读。
import agent_core
import chat_archive


def setup_function(_fn):
    chat_archive._ARCHIVE.clear()


def _turn(q, a):
    return [{"role": "user", "content": q}, {"role": "assistant", "content": a}]


def test_stash_keeps_user_and_final_reply_only():
    chat_archive.stash("u", [
        {"role": "user", "content": "发票多少钱"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t"}]},
        {"role": "tool", "tool_call_id": "t", "content": "工具原始结果"},
        {"role": "assistant", "content": "42 元"},
    ])
    out = chat_archive.read("u")
    assert "发票多少钱" in out and "42 元" in out
    assert "工具原始结果" not in out


def test_read_empty_and_per_user():
    chat_archive.stash("a", _turn("甲的问题", "甲的回答"))
    assert "甲的问题" not in chat_archive.read("b")
    assert chat_archive.read("b") == chat_archive.EMPTY


def test_read_query_and_limit():
    for i in range(5):
        chat_archive.stash("u", _turn(f"问题{i}", f"回答{i}"))
    chat_archive.stash("u", _turn("机票订了吗", "订了"))
    assert "机票" in chat_archive.read("u", query="机票")
    assert "问题0" not in chat_archive.read("u", query="机票")
    last2 = chat_archive.read("u", limit=2)
    assert "问题4" in last2 and "机票" in last2 and "问题3" not in last2
    assert last2.index("问题4") < last2.index("机票")   # 旧在前


def test_cap_drops_oldest():
    for i in range(chat_archive.CAP + 5):
        chat_archive.stash("u", _turn(f"q{i}", f"a{i}"))
    assert len(chat_archive._ARCHIVE["u"]) == chat_archive.CAP


def test_idle_clear_archives(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = agent_core.Agent(idle_clear_hours=2)
    agent.history["u"] = _turn("旧话题", "旧回复")
    agent._last_active["u"] = 1_000_000.0
    monkeypatch.setattr(agent_core.time, "time", lambda: 1_000_000.0 + 3 * 3600)
    agent.handle("新话题", user="u", member="爸爸")
    assert "旧话题" in chat_archive.read("u")


def test_clear_command_archives():
    agent = agent_core.Agent(idle_clear_hours=0)
    agent.history["u"] = _turn("旧话题", "旧回复")
    agent.handle("/clear", user="u", member="爸爸")
    assert "旧回复" in chat_archive.read("u")


def test_budget_trim_archives_dropped_turns():
    agent = agent_core.Agent(history_size=2, context_max_tokens=0, idle_clear_hours=0)
    for i in range(4):
        agent._save_history("u", _turn(f"问{i}", f"答{i}"))
    out = chat_archive.read("u")
    assert "问0" in out and "问1" in out and "问2" not in out


def test_tool_reads_injected_user_only():
    chat_archive.stash("u", _turn("旧话题", "旧回复"))
    targs = agent_core._apply_context("chat_history", {"query": "旧"}, "wechat", "u", "爸爸")
    out = agent_core._TOOL_MAP["chat_history"](targs)
    assert "旧话题" in out
    assert "旧话题" not in agent_core._TOOL_MAP["chat_history"]({"__user": "other"})
    assert agent_core._TOOL_MAP["chat_history"]({}).startswith("[错误]")
