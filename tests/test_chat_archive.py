# tests/test_chat_archive.py — 对话长期存档（jsonl 追加）+ chat_history 工具回读。
import json
import os
import subprocess
import sys

import pytest

import agent_core
import chat_archive


@pytest.fixture(autouse=True)
def archive_file(tmp_path, monkeypatch):
    f = tmp_path / "chat_history.jsonl"
    monkeypatch.setattr(chat_archive, "_path", lambda: f)
    return f


def _agent(monkeypatch, channel="wechat"):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    agent = agent_core.Agent(idle_clear_hours=0, channel=channel)
    monkeypatch.setattr(agent, "_call_llm", lambda msgs, user="": {"content": "42 元"})
    return agent


def test_handle_appends_said_and_reply(monkeypatch, archive_file):
    agent = _agent(monkeypatch)
    agent.handle("引用: 旧消息\n发票多少钱", user="u", member="爸爸", said="发票多少钱")
    row = json.loads(archive_file.read_text(encoding="utf-8").splitlines()[0])
    assert (row["channel"], row["user"], row["said"], row["reply"]) == (
        "wechat", "u", "发票多少钱", "42 元")


def test_llm_failure_after_tool_still_archived(monkeypatch, archive_file):
    agent = _agent(monkeypatch)
    calls = iter([{"content": "", "tool_calls": [{"id": "1", "function": {
        "name": "chat_history", "arguments": "{}"}}]}, None])
    monkeypatch.setattr(agent, "_call_llm", lambda msgs, user="": next(calls))
    out = agent.handle("机票订了吗", user="u", member="爸爸")
    assert "工具已执行" in out
    row = json.loads(archive_file.read_text(encoding="utf-8").splitlines()[0])
    assert row["said"] == "机票订了吗"


def test_commands_not_archived(monkeypatch, archive_file):
    agent = _agent(monkeypatch)
    agent.handle("/clear", user="u", member="爸爸")
    assert not archive_file.exists()


def test_survives_new_agent_instance(monkeypatch):
    _agent(monkeypatch).handle("机票订了吗", user="u", member="爸爸")
    _agent(monkeypatch).handle("酒店呢", user="u", member="爸爸")   # 模拟重启
    out = chat_archive.read("wechat", "u")
    assert out.index("机票") < out.index("酒店")   # 旧在前


def test_read_per_user_and_channel():
    chat_archive.append("wechat", "a", "甲的问题", "甲的回答")
    assert chat_archive.read("wechat", "b") == chat_archive.EMPTY
    assert chat_archive.read("telegram", "a") == chat_archive.EMPTY
    assert "甲的问题" in chat_archive.read("wechat", "a")


def test_read_query_limit_and_bad_lines(archive_file):
    for i in range(5):
        chat_archive.append("wechat", "u", f"问题{i}", f"回答{i}")
    with archive_file.open("a", encoding="utf-8") as f:
        f.write('{"半行\n')
    chat_archive.append("wechat", "u", "机票订了吗", "订了")
    assert "问题0" not in chat_archive.read("wechat", "u", query="机票")
    last2 = chat_archive.read("wechat", "u", limit=2)
    assert "问题4" in last2 and "机票" in last2 and "问题3" not in last2


def test_partial_line_without_newline_keeps_next_row(archive_file):
    archive_file.write_text('{"半行', encoding="utf-8")
    chat_archive.append("wechat", "u", "机票订了吗", "订了")
    assert "机票" in chat_archive.read("wechat", "u")


_WRITER = """
import sys, threading, pathlib, chat_archive
f = pathlib.Path(sys.argv[1])
chat_archive._path = lambda: f
ts = [threading.Thread(target=lambda t=t: [chat_archive.append("wechat", "u", f"{sys.argv[2]}-{t}-{i}", "x")
                                           for i in range(25)]) for t in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
"""


def test_concurrent_appends_lose_nothing(archive_file):
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER, str(archive_file), str(p)],
                              env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
             for p in range(3)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    rows = [json.loads(ln) for ln in archive_file.read_text(encoding="utf-8").splitlines()]
    assert len({r["said"] for r in rows}) == 3 * 4 * 25


def test_missing_file_is_empty():
    assert chat_archive.read("wechat", "u") == chat_archive.EMPTY


def test_tool_reads_injected_user_only():
    chat_archive.append("wechat", "u", "旧话题", "旧回复")
    tool = agent_core._TOOL_MAP["chat_history"]
    targs = agent_core._apply_context("chat_history", {"query": "旧"}, "wechat", "u", "爸爸")
    assert "旧话题" in tool(targs)
    assert "旧话题" not in tool({"__channel": "wechat", "__user": "other"})
    assert tool({}).startswith("[错误]")
