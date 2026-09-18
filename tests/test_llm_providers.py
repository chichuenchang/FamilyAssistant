# tests/test_llm_providers.py — 模型表 + 提供商翻译层（openai_compat / anthropic）。
import io
import json
import urllib.error
import urllib.request

import pytest

import llm_client
import llm_providers as prov

DS = llm_client._BUILTIN_MODELS["deepseek-flash"]
CLAUDE = {"provider": "anthropic", "api_model": "claude-opus-5", "api_key_env": "ANTHROPIC_API_KEY",
          "base_url": "https://api.anthropic.com", "base_url_env": "ANTHROPIC_BASE_URL"}


@pytest.fixture
def capture(monkeypatch):
    """替换 urlopen：记录请求，回放 reply（dict）；reply 为 HTTPError 则抛。"""
    box = {"reply": {}}

    def fake(req, timeout=0):
        box["url"] = req.full_url
        box["headers"] = {k.lower(): v for k, v in req.header_items()}
        box["body"] = json.loads(req.data)
        box["timeout"] = timeout
        if isinstance(box["reply"], Exception):
            raise box["reply"]
        return io.BytesIO(json.dumps(box["reply"]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return box


# ── openai_compat ───────────────────────────────────────────

def test_openai_compat_payload_and_private_key_stripping(capture, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-1")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    capture["reply"] = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    msgs = [{"role": "assistant", "content": "x", "_blocks": [1], "_usage": {}},
            {"role": "user", "content": "hi"}]
    out = prov.openai_compat(DS, msgs, [{"type": "function"}], "max", temperature=0, max_tokens=9,
                             timeout=7)
    assert out == {"content": "ok", "_usage": {"prompt_tokens": 3, "completion_tokens": 1},
                   "_finish": "stop"}
    assert capture["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert capture["headers"]["authorization"] == "Bearer sk-1"
    assert capture["timeout"] == 7
    b = capture["body"]
    assert b["messages"] == [{"role": "assistant", "content": "x"}, {"role": "user", "content": "hi"}]
    assert (b["model"], b["reasoning_effort"], b["temperature"], b["max_tokens"]) == \
        ("deepseek-flash", "max", 0, 9)
    assert b["tools"] == [{"type": "function"}]


def test_openai_compat_base_url_env_and_effort_off(capture, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/")
    capture["reply"] = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
    prov.openai_compat({**DS, "effort": False}, [], None, "high")
    assert capture["url"] == "http://localhost:11434/v1/chat/completions"
    assert "reasoning_effort" not in capture["body"] and "tools" not in capture["body"]


def test_http_error_body_logged_returns_none(capture, caplog):
    capture["reply"] = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))
    assert prov.openai_compat(DS, [], None, "high") is None
    assert "bad key" in caplog.text


@pytest.mark.parametrize("reply", [
    {"error": "x"},
    {"choices": []},
    {"choices": [{"finish_reason": "stop"}]},          # 缺 message（代理/Ollama 变体）
    {"choices": [{"message": "text"}]},                # message 非 dict
])
def test_openai_compat_malformed_reply_returns_none(capture, caplog, reply):
    capture["reply"] = reply
    assert prov.openai_compat(DS, [], None, "high") is None
    assert "形态异常" in caplog.text


# ── anthropic ───────────────────────────────────────────────

def test_to_anthropic_translation():
    msgs = [
        {"role": "system", "content": "S1"},
        {"role": "system", "content": "S2"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "thinking aloud",
         "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}},
                        {"id": "t2", "type": "function", "function": {"name": "g", "arguments": "{bad"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r1"},
        {"role": "tool", "tool_call_id": "t2", "content": "r2"},
        {"role": "assistant", "content": "", "_blocks": [{"type": "thinking", "thinking": "", "signature": "sig"},
                                                         {"type": "text", "text": "done"}]},
        {"role": "assistant", "content": ""},   # 空消息跳过
        {"role": "user", "content": "next"},
    ]
    system, out = prov._to_anthropic(msgs)
    assert system == "S1\n\nS2"
    assert out == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "thinking aloud"},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": 1}},
            {"type": "tool_use", "id": "t2", "name": "g", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "r1"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "r2"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                                          {"type": "text", "text": "done"}]},
        {"role": "user", "content": "next"},
    ]


def test_anthropic_request_and_response(capture, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    capture["reply"] = {
        "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                    {"type": "text", "text": "调一下"},
                    {"type": "tool_use", "id": "tu1", "name": "add", "input": {"amount": 5, "desc": "咖啡"}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }
    tools = [{"type": "function", "function": {"name": "add", "description": "记账",
                                               "parameters": {"type": "object", "properties": {}}}}]
    out = prov.anthropic(CLAUDE, [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                         tools, "max", temperature=0.3, max_tokens=123)
    assert capture["url"] == "https://api.anthropic.com/v1/messages"
    assert capture["headers"]["x-api-key"] == "ak"
    assert capture["headers"]["anthropic-version"] == "2023-06-01"
    b = capture["body"]
    assert b["system"] == "sys" and b["messages"] == [{"role": "user", "content": "hi"}]
    assert b["output_config"] == {"effort": "max"} and b["max_tokens"] == 123
    assert "temperature" not in b
    assert b["tools"] == [{"name": "add", "description": "记账",
                           "input_schema": {"type": "object", "properties": {}}}]
    assert out["content"] == "调一下" and out["_finish"] == "tool_calls"
    assert out["_usage"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert out["_blocks"] == capture["reply"]["content"]
    assert out["tool_calls"] == [{"id": "tu1", "type": "function",
                                  "function": {"name": "add",
                                               "arguments": '{"amount": 5, "desc": "咖啡"}'}}]


@pytest.mark.parametrize("stop,finish", [("end_turn", "stop"), ("max_tokens", "length"),
                                         ("refusal", "refusal")])
def test_anthropic_finish_mapping(capture, stop, finish):
    capture["reply"] = {"content": [{"type": "text", "text": "t"}], "stop_reason": stop, "usage": {}}
    out = prov.anthropic(CLAUDE, [{"role": "user", "content": "hi"}], None, "high")
    assert out["_finish"] == finish and "tool_calls" not in out
    assert out["_usage"] == {"prompt_tokens": 0, "completion_tokens": 0}
    assert "tools" not in capture["body"] and "system" not in capture["body"]


@pytest.mark.parametrize("reply", [
    {"type": "error", "error": {"message": "x"}},
    {"content": "not a list"},
    {"content": [{"type": "tool_use", "input": {}}]},   # tool_use 缺 id/name
    {"content": [{"type": "text"}]},                    # text 块缺 text
])
def test_anthropic_malformed_reply_returns_none(capture, caplog, reply):
    capture["reply"] = reply
    assert prov.anthropic(CLAUDE, [{"role": "user", "content": "hi"}], None, "high") is None
    assert "形态异常" in caplog.text


# ── 模型表 ──────────────────────────────────────────────────

@pytest.fixture
def models():
    yield llm_client.load_models({"llm": {"models": {
        "kimi": {"provider": "openai_compat", "api_model": "kimi-k2", "api_key_env": "MOONSHOT_API_KEY",
                 "base_url": "https://api.moonshot.cn", "aliases": ["Kimi", "moon"]},
        "deepseek-flash": {"aliases": ["ds"]},                          # 覆盖内置字段
        "broken": {"provider": "nope", "api_key_env": "X", "base_url": "u"},   # 未知 provider
        "nokey": {"provider": "anthropic", "base_url": "u"},                    # 缺 api_key_env
        "_comment": "ignored",
    }}})
    llm_client.load_models()   # 恢复真实 config.json


def test_load_models_merges_config_and_skips_invalid(models):
    assert set(models) == {"deepseek-flash", "kimi"}
    assert models["kimi"]["api_model"] == "kimi-k2"
    assert models["deepseek-flash"]["aliases"] == ["ds"]
    assert models["deepseek-flash"]["api_key_env"] == "DEEPSEEK_API_KEY"   # 其余内置字段保留
    assert llm_client.canon_model("MOON") == "kimi"
    assert llm_client.canon_model("KIMI") == "kimi"
    assert llm_client.canon_model("ds") == "deepseek-flash"
    assert llm_client.canon_model("flash") is None       # 被覆盖掉的旧别名失效
    assert llm_client.canon_model("gpt-99") is None and llm_client.canon_model(None) is None


def test_spec_unknown_name_falls_back_to_deepseek_raw_id():
    s = llm_client.spec("deepseek-v4-pro")
    assert s["provider"] == "openai_compat" and s["api_model"] == "deepseek-v4-pro"
    assert s["api_key_env"] == "DEEPSEEK_API_KEY"


def test_spec_fallback_honours_config_override_of_default():
    llm_client.load_models({"llm": {"models": {"deepseek-flash": {"base_url": "http://proxy:8080"}}}})
    try:
        s = llm_client.spec("deepseek-v4-pro")
        assert s["base_url"] == "http://proxy:8080" and s["api_model"] == "deepseek-v4-pro"
    finally:
        llm_client.load_models()


def test_missing_key_and_ready_note(monkeypatch):
    for v in ("LLM_MODEL", "DEEPSEEK_MODEL", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    assert llm_client.missing_key("deepseek-flash") == "DEEPSEEK_API_KEY"
    assert llm_client.missing_key("claude-opus-5") == "ANTHROPIC_API_KEY"
    assert llm_client.ready_note() == "LLM: deepseek-flash 未配置 — 设置 DEEPSEEK_API_KEY"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert llm_client.missing_key("deepseek-flash") == ""
    assert llm_client.ready_note() == "LLM: deepseek-flash 已启用"


def test_chat_dispatches_by_provider(monkeypatch):
    seen = {}
    monkeypatch.setitem(prov.PROVIDERS, "anthropic",
                        lambda spec, m, t, e, **o: seen.update(spec=spec, e=e, o=o) or {"content": "c"})
    out = llm_client.chat([{"role": "user", "content": "x"}], None, "claude-opus-5", "low", timeout=5)
    assert out == {"content": "c"}
    assert seen["spec"]["api_model"] == "claude-opus-5" and seen["e"] == "low" and seen["o"] == {"timeout": 5}


def test_config_ships_claude_entry():
    assert llm_client.MODELS["claude-opus-5"]["provider"] == "anthropic"
    assert llm_client.canon_model("opus") == "claude-opus-5"
