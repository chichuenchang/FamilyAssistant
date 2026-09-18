# tests/test_agent_llm_switch.py — /model /effort 运行时切换（每用户覆盖 + 持久化）。
import json

import agent_core


def test_load_llm_overrides_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert agent_core._load_llm_overrides() == {}


def test_load_llm_overrides_validates_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / ".llm_overrides.json").write_text(json.dumps({
        "u1": {"model": "deepseek-v4-pro", "effort": "high"},   # 未登记模型丢弃，effort 保留
        "u2": {"model": "gpt-99", "effort": "ludicrous"},   # 非法值整条丢弃
        "u3": "not-a-dict",
        "u4": {"model": "deepseek-flash"},                # 登记模型单键也合法
        "u5": {"model": "CLAUDE", "effort": 3},           # 别名不分大小写 → 规范名
    }), encoding="utf-8")
    assert agent_core._load_llm_overrides() == {
        "u1": {"effort": "high"}, "u4": {"model": "deepseek-flash"},
        "u5": {"model": "claude-opus-5"}}


def test_load_llm_overrides_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / ".llm_overrides.json").write_text("{broken", encoding="utf-8")
    assert agent_core._load_llm_overrides() == {}


def test_save_llm_overrides_roundtrip_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    data = {"u1": {"effort": "max"}}
    agent_core._save_llm_overrides(data)
    assert agent_core._load_llm_overrides() == data
    assert [p.name for p in (tmp_path / ".state").iterdir()] == [".llm_overrides.json"]  # 无临时文件残留


def _agent(tmp_path, monkeypatch):
    """干净环境下的 Agent：数据根隔离，LLM 相关环境变量清空。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    for v in ("LLM_MODEL", "LLM_EFFORT", "DEEPSEEK_MODEL", "DEEPSEEK_REASONING_EFFORT",
              "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return agent_core.Agent(idle_clear_hours=0)


def test_llm_settings_defaults(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    assert a._llm_settings("u1") == ("deepseek-flash", "high")


def test_llm_settings_env_beats_default(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")   # 未登记名字原样直发
    monkeypatch.setenv("LLM_EFFORT", "max")
    assert a._llm_settings("u1") == ("deepseek-v4-pro", "max")
    monkeypatch.setenv("LLM_MODEL", "Claude")            # 别名 → 规范名
    assert a._llm_settings("u1")[0] == "claude-opus-5"


def test_llm_settings_legacy_env_names(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "low")
    assert a._llm_settings("u1") == ("deepseek-v4-pro", "low")


def test_llm_settings_override_beats_env(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "max")
    a._llm_overrides["u1"] = {"effort": "low"}
    assert a._llm_settings("u1") == ("deepseek-flash", "low")
    monkeypatch.delenv("DEEPSEEK_REASONING_EFFORT")
    assert a._llm_settings("u2") == ("deepseek-flash", "high")  # 不影响其他用户


def test_handle_passes_user_to_call_llm(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")  # 过 handle 的 key 检查；stub 不发请求
    seen = {}

    def stub(messages, user=""):
        seen["user"] = user
        return {"content": "好"}

    a._call_llm = stub
    assert a.handle("你好", user="wx_1", member="Jim") == "好"
    assert seen["user"] == "wx_1"


def test_model_command_query_lists_models(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model", user="u1", member="Jim")
    assert "当前模型：deepseek-flash（默认）" in r
    assert "claude-opus-5（claude/opus），未配置 ANTHROPIC_API_KEY" in r
    assert "用法" in a.handle("/model pro", user="u1", member="Jim")
    assert a._llm_overrides == {}


def test_model_command_switch_alias_reset(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    r = a.handle("/model Claude", user="u1", member="Jim")
    assert "✅" in r and "claude-opus-5" in r
    assert a._llm_settings("u1")[0] == "claude-opus-5"
    assert a._llm_settings("u2")[0] == "deepseek-flash"   # 不影响其他用户
    assert "claude-opus-5（你的个人覆盖）" in a.handle("/model", user="u1", member="Jim")
    a.handle("/model reset", user="u1", member="Jim")
    assert a._llm_settings("u1")[0] == "deepseek-flash"
    assert agent_core._load_llm_overrides() == {}


def test_model_command_refuses_unconfigured_key(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model claude", user="u1", member="Jim")
    assert r == "模型 claude-opus-5 未配置 ANTHROPIC_API_KEY，无法切换。"
    assert a._llm_overrides == {}


def test_handle_reports_missing_key_of_selected_model(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    a.handle("/model opus", user="u1", member="Jim")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert a.handle("hi", user="u1", member="Jim") == "未配置 ANTHROPIC_API_KEY。"


def test_call_llm_routes_by_user_model(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    a.handle("/model claude", user="u1", member="Jim")
    seen = {}
    monkeypatch.setattr(agent_core._llm, "chat",
                        lambda msgs, tools, model, effort: seen.update(model=model, effort=effort))
    a._call_llm([], user="u1")
    assert seen == {"model": "claude-opus-5", "effort": "high"}


def test_effort_command_set_show_reset(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/effort low", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "low"
    assert "low" in a.handle("/effort", user="u1", member="Jim")
    a.handle("/effort reset", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "high"


def test_command_invalid_arg_shows_usage_no_state_change(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model turbo", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[0] == "deepseek-flash"
    r = a.handle("/effort xhigh", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[1] == "high"


def test_commands_need_no_api_key_and_no_llm_call(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)  # fixture 已删 DEEPSEEK_API_KEY
    called = []
    a._call_llm = lambda *args, **kw: called.append(1)
    a.handle("/model", user="u1", member="Jim")
    a.handle("/effort high", user="u1", member="Jim")
    assert not called


def test_overrides_persist_across_instances(tmp_path, monkeypatch):
    a1 = _agent(tmp_path, monkeypatch)
    a1.handle("/effort low", user="u1", member="Jim")
    a2 = agent_core.Agent(idle_clear_hours=0)  # 同 DATA_ROOT 新实例 = 模拟重启
    assert a2._llm_settings("u1") == ("deepseek-flash", "low")


def test_persist_merges_other_process_writes(tmp_path, monkeypatch):
    a1 = _agent(tmp_path, monkeypatch)
    a1.handle("/effort low", user="u1", member="Jim")
    # 另一进程（另一个 Agent 实例）同时给 u2 写入覆盖
    a2 = agent_core.Agent(idle_clear_hours=0)
    a2.handle("/effort high", user="u2", member="Jim")
    # a1 再切换 u1 时不得丢掉 u2 的覆盖
    a1.handle("/effort max", user="u1", member="Jim")
    disk = agent_core._load_llm_overrides()
    assert disk["u2"] == {"effort": "high"}
    assert disk["u1"] == {"effort": "max"}


def test_state_file_excluded_from_backup():
    import backup_sync
    assert ".llm_overrides.json" in backup_sync._HARD_EXCLUDE_NAMES


def test_system_prompt_documents_slash_commands(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    sp = a.system_prompt
    # 用户迷茫时 Agent 要能从 system prompt 里查到用法并转述
    assert "/model <名字或别名>" in sp and "/model reset" in sp
    assert "/effort low|medium|high|max" in sp and "/effort reset" in sp


def test_agent_knows_own_model_and_effort(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    seen = []
    a._call_llm = lambda msgs, user="": seen.append(msgs) or {"content": "好"}
    a.handle("你好", user="u1", member="Jim")
    sysmsg = seen[0][1]["content"]   # 易变 system 条（静态文档在 [0]）
    assert "deepseek-flash" in sysmsg and "运行，推理档 high" in sysmsg
    a.handle("/effort low", user="u1", member="Jim")
    a.handle("你现在用什么模型", user="u1", member="Jim")
    sysmsg = seen[1][1]["content"]
    assert "deepseek-flash" in sysmsg and "运行，推理档 low" in sysmsg


def test_command_extra_args_shows_usage(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model pro 怎么样", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[0] == "deepseek-flash"


def test_command_case_insensitive_and_models_fallthrough(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/EFFORT LOW", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "low"
    # /models 不是命令 → 走 LLM 路径（无 key → 提示未配置）
    assert a.handle("/models", user="u1", member="Jim") == "未配置 DEEPSEEK_API_KEY。"


def test_persist_failure_warns_but_applies(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)

    def boom(_):
        raise OSError("disk full")

    monkeypatch.setattr(agent_core, "_save_llm_overrides", boom)
    r = a.handle("/effort low", user="u1", member="Jim")
    assert "重启后可能失效" in r
    assert a._llm_settings("u1")[1] == "low"  # 内存仍生效


def test_reset_persist_failure_warns(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/effort low", user="u1", member="Jim")

    def boom(_):
        raise OSError("disk full")

    monkeypatch.setattr(agent_core, "_save_llm_overrides", boom)
    r = a.handle("/effort reset", user="u1", member="Jim")
    assert "重启后可能恢复" in r  # 旧覆盖还在盘上，重启会复活——必须告知


def test_chat_omits_tools_key_when_empty(monkeypatch):
    import io
    import json
    import urllib.request
    import llm_client
    sent = {}

    def fake_urlopen(req, timeout=0):
        sent.update(json.loads(req.data))
        return io.BytesIO(json.dumps(
            {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert llm_client.chat([{"role": "user", "content": "hi"}], [], "m", "low")["content"] == "ok"
    assert "tools" not in sent
    llm_client.chat([{"role": "user", "content": "hi"}], [{"type": "function"}], "m", "low")
    assert sent["tools"] == [{"type": "function"}]


def test_reply_badge_sums_tokens_and_strips_usage(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    replies = iter([
        {"content": "", "tool_calls": [{"id": "1", "function": {"name": "nope", "arguments": "{}"}}],
         "_usage": {"prompt_tokens": 1000, "completion_tokens": 20}},
        {"content": "好", "_usage": {"prompt_tokens": 1200, "completion_tokens": 5}},
    ])
    seen = []
    a._call_llm = lambda msgs, user="": seen.append([dict(m) for m in msgs]) or next(replies)
    r = a.handle("hi", user="u1", member="Jim")
    assert r.splitlines()[0] == "⚙️ nope · 🪙 2,200 in / 25 out"
    assert all("_usage" not in m for m in seen[1])   # 私有键不回传 API


def test_truncated_tool_calls_not_executed(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    ran = []
    monkeypatch.setitem(agent_core._TOOL_MAP, "boom", lambda args: ran.append(args) or "ok")
    replies = iter([
        {"content": "", "_finish": "length",
         "tool_calls": [{"id": "1", "function": {"name": "boom", "arguments": '{"a": 1'}}]},
        {"content": "好"},
    ])
    seen = []
    a._call_llm = lambda msgs, user="": seen.append([dict(m) for m in msgs]) or next(replies)
    assert a.handle("hi", user="u1", member="Jim") == "好"
    assert ran == []
    assert seen[1][-1] == {"role": "user", "content": agent_core.TRUNCATED_TOOLS_NOTE}
    assert not any(m.get("tool_calls") for m in a.history["u1"])


def test_truncated_reply_marked_and_not_saved(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    a._call_llm = lambda msgs, user="": {"content": "半句话", "_finish": "length"}
    r = a.handle("hi", user="u1", member="Jim")
    assert r == f"半句话\n{agent_core.TRUNCATED_REPLY_MARK}"
    assert a.history["u1"][-1] == {"role": "assistant", "content": agent_core.TRUNCATED_REPLY_MARK}
    a._call_llm = lambda msgs, user="": {"content": "", "_finish": "length"}
    assert a.handle("hi", user="u1", member="Jim") == agent_core.TRUNCATED_REPLY_MARK
