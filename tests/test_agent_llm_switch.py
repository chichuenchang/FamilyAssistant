# tests/test_agent_llm_switch.py — /model /effort 运行时切换（每用户覆盖 + 持久化）。
import json

import agent_core


def test_load_llm_overrides_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert agent_core._load_llm_overrides() == {}


def test_load_llm_overrides_validates_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / ".llm_overrides.json").write_text(json.dumps({
        "u1": {"model": "deepseek-v4-pro", "effort": "high"},
        "u2": {"model": "gpt-99", "effort": "ludicrous"},   # 非法值整条丢弃
        "u3": "not-a-dict",
        "u4": {"model": "deepseek-v4-flash"},                # 单键也合法
    }), encoding="utf-8")
    assert agent_core._load_llm_overrides() == {
        "u1": {"model": "deepseek-v4-pro", "effort": "high"},
        "u4": {"model": "deepseek-v4-flash"},
    }


def test_load_llm_overrides_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / ".llm_overrides.json").write_text("{broken", encoding="utf-8")
    assert agent_core._load_llm_overrides() == {}


def test_save_llm_overrides_roundtrip_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    data = {"u1": {"model": "deepseek-v4-pro", "effort": "max"}}
    agent_core._save_llm_overrides(data)
    assert agent_core._load_llm_overrides() == data
    assert not (tmp_path / ".llm_overrides.json.tmp").exists()  # 临时文件已 replace


def _agent(tmp_path, monkeypatch):
    """干净环境下的 Agent：数据根隔离，LLM 相关环境变量清空。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    for v in ("DEEPSEEK_MODEL", "DEEPSEEK_REASONING_EFFORT", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return agent_core.Agent(idle_clear_hours=0)


def test_llm_settings_defaults(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    assert a._llm_settings("u1") == ("deepseek-v4-flash", "max")


def test_llm_settings_env_beats_default(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "high")
    assert a._llm_settings("u1") == ("deepseek-v4-pro", "high")


def test_llm_settings_override_beats_env(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    a._llm_overrides["u1"] = {"model": "deepseek-v4-pro", "effort": "low"}
    assert a._llm_settings("u1") == ("deepseek-v4-pro", "low")
    assert a._llm_settings("u2") == ("deepseek-v4-flash", "max")  # 不影响其他用户


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


def test_model_command_set_show_reset(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model pro", user="u1", member="Jim")
    assert "deepseek-v4-pro" in r and a._llm_settings("u1")[0] == "deepseek-v4-pro"
    r = a.handle("/model", user="u1", member="Jim")
    assert "deepseek-v4-pro" in r and "覆盖" in r
    r = a.handle("/model reset", user="u1", member="Jim")
    assert "✅" in r and a._llm_settings("u1") == ("deepseek-v4-flash", "max")


def test_effort_command_set_show_reset(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/effort low", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "low"
    assert "low" in a.handle("/effort", user="u1", member="Jim")
    a.handle("/effort reset", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "max"


def test_commands_accept_full_id_and_alias(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/model deepseek-v4-pro", user="u1", member="Jim")
    assert a._llm_settings("u1")[0] == "deepseek-v4-pro"
    a.handle("/model flash", user="u1", member="Jim")
    assert a._llm_settings("u1")[0] == "deepseek-v4-flash"


def test_command_invalid_arg_shows_usage_no_state_change(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model turbo", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[0] == "deepseek-v4-flash"
    r = a.handle("/effort xhigh", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[1] == "max"


def test_commands_need_no_api_key_and_no_llm_call(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)  # fixture 已删 DEEPSEEK_API_KEY
    called = []
    a._call_llm = lambda *args, **kw: called.append(1)
    a.handle("/model pro", user="u1", member="Jim")
    a.handle("/effort high", user="u1", member="Jim")
    assert not called


def test_overrides_persist_across_instances(tmp_path, monkeypatch):
    a1 = _agent(tmp_path, monkeypatch)
    a1.handle("/model pro", user="u1", member="Jim")
    a1.handle("/effort low", user="u1", member="Jim")
    a2 = agent_core.Agent(idle_clear_hours=0)  # 同 DATA_ROOT 新实例 = 模拟重启
    assert a2._llm_settings("u1") == ("deepseek-v4-pro", "low")


def test_persist_merges_other_process_writes(tmp_path, monkeypatch):
    a1 = _agent(tmp_path, monkeypatch)
    a1.handle("/model pro", user="u1", member="Jim")
    # 另一进程（另一个 Agent 实例）同时给 u2 写入覆盖
    a2 = agent_core.Agent(idle_clear_hours=0)
    a2.handle("/effort high", user="u2", member="Jim")
    # a1 再切换 u1 时不得丢掉 u2 的覆盖
    a1.handle("/effort max", user="u1", member="Jim")
    disk = agent_core._load_llm_overrides()
    assert disk["u2"] == {"effort": "high"}
    assert disk["u1"] == {"model": "deepseek-v4-pro", "effort": "max"}


def test_state_file_excluded_from_backup():
    import backup_sync
    assert ".llm_overrides.json" in backup_sync._HARD_EXCLUDE_NAMES


def test_system_prompt_documents_slash_commands(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    sp = a.system_prompt
    # 用户迷茫时 Agent 要能从 system prompt 里查到用法并转述
    assert "/model flash" in sp and "/model pro" in sp and "/model reset" in sp
    assert "/effort low|medium|high|max" in sp and "/effort reset" in sp
