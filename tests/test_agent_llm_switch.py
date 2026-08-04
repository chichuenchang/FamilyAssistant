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
