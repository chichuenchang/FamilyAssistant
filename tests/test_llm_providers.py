# tests/test_llm_providers.py — 模型表 + 提供商翻译层（openai_compat / anthropic）。
import io
import json
import urllib.error
import urllib.request

import pytest
from types import SimpleNamespace

import llm_client
import llm_providers as prov

DS = llm_client._BUILTIN_MODELS["deepseek-flash"]
CLAUDE = {"provider": "anthropic", "api_model": "claude-opus-5", "api_key_env": "ANTHROPIC_API_KEY",
          "base_url": "https://api.anthropic.com", "base_url_env": "ANTHROPIC_BASE_URL"}


def sse(*chunks) -> bytes:
    """OpenAI 流式回包：每块一行 data:，末尾 [DONE]。str 块原样作一行（模拟坏行/注释行）。"""
    lines = [c if isinstance(c, str) else "data: " + json.dumps(c) for c in chunks]
    return "\n\n".join([*lines, "data: [DONE]", ""]).encode()


def delta(content=None, tool_calls=None, finish=None, usage=None) -> dict:
    d = {k: v for k, v in (("content", content), ("tool_calls", tool_calls)) if v is not None}
    chunk = {"choices": [{"delta": d, "finish_reason": finish}]}
    if usage is not None:
        chunk["usage"] = usage
    return chunk


@pytest.fixture
def capture(monkeypatch):
    """替换 urlopen：记录请求，回放 reply（dict = 非流式 JSON，bytes = 原始流）；Exception 则抛。"""
    box = {"reply": {}}

    def fake(req, timeout=0):
        box["url"] = req.full_url
        box["headers"] = {k.lower(): v for k, v in req.header_items()}
        box["body"] = json.loads(req.data)
        box["timeout"] = timeout
        r = box["reply"]
        if isinstance(r, Exception):
            raise r
        resp = io.BytesIO(r if isinstance(r, bytes) else json.dumps(r).encode())
        sock = SimpleNamespace(settimeout=lambda s: box.__setitem__("silence", s))
        resp.fp = SimpleNamespace(raw=SimpleNamespace(_sock=sock))   # 模拟 HTTPResponse 底层 socket
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return box


# ── openai_compat ───────────────────────────────────────────

def test_openai_compat_payload_and_private_key_stripping(capture, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-1")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    capture["reply"] = sse(delta("o"), delta("k", finish="stop"),
                           {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})
    msgs = [{"role": "assistant", "content": "x", "_blocks": [1], "_usage": {}},
            {"role": "user", "content": "hi"}]
    out = prov.openai_compat(DS, msgs, [{"type": "function"}], "max", temperature=0, max_tokens=9,
                             timeout=7)
    assert out == {"role": "assistant", "content": "ok", "_finish": "stop",
                   "_usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    assert capture["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert capture["headers"]["authorization"] == "Bearer sk-1"
    assert capture["timeout"] == 7
    b = capture["body"]
    assert b["messages"] == [{"role": "assistant", "content": "x"}, {"role": "user", "content": "hi"}]
    assert (b["model"], b["reasoning_effort"], b["temperature"], b["max_tokens"]) == \
        ("deepseek-flash", "max", 0, 9)
    assert b["tools"] == [{"type": "function"}]
    assert b["stream"] is True and b["stream_options"] == {"include_usage": True}


def test_sse_merges_tool_call_deltas_by_index(capture):
    capture["reply"] = sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "add", "arguments": '{"a"'}}]),
        delta(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "list", "arguments": "{}"}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": ": 1}"}}]),
        delta(finish="tool_calls"))
    out = prov.openai_compat(DS, [], [{"type": "function"}], "high")
    assert out["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"a": 1}'}},
        {"id": "c2", "type": "function", "function": {"name": "list", "arguments": "{}"}}]
    assert out["content"] == "" and out["_finish"] == "tool_calls" and out["_usage"] == {}


def test_sse_tool_call_deltas_without_index(capture):
    """无 index 变体：带新 id 的块开新槽，无 id / 同 id 的块续上一槽，不得全塌进槽 0。"""
    capture["reply"] = sse(
        delta(tool_calls=[{"id": "c1", "function": {"name": "add", "arguments": '{"a"'}}]),
        delta(tool_calls=[{"function": {"arguments": ": 1}"}}]),
        delta(tool_calls=[{"id": "c1", "function": {"arguments": ""}}]),
        delta(tool_calls=[{"id": "c2", "function": {"name": "list", "arguments": '{"b": 2}'}}]),
        delta(finish="tool_calls"))
    out = prov.openai_compat(DS, [], [{"type": "function"}], "high")
    assert out["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"a": 1}'}},
        {"id": "c2", "type": "function", "function": {"name": "list", "arguments": '{"b": 2}'}}]


def test_sse_keeps_reasoning_skips_comment_lines(capture):
    """reasoning_content 拼起来留在消息里（同轮工具调用须回传 DeepSeek）；无则不带键。"""
    capture["reply"] = sse(": keep-alive", {"choices": [{"delta": {"reasoning_content": "思"}}]},
                           {"choices": [{"delta": {"reasoning_content": "考"}}]},
                           delta("答", finish="stop", usage={"completion_tokens": 2}))
    out = prov.openai_compat(DS, [], None, "high")
    assert out["content"] == "答" and out["_usage"] == {"completion_tokens": 2}
    assert out["reasoning_content"] == "思考"
    capture["reply"] = sse(delta("答", finish="stop"))
    assert "reasoning_content" not in prov.openai_compat(DS, [], None, "high")


def test_glm_spec_knobs(capture, monkeypatch):
    """chat_path 替换 /v1 前缀；effort_map 改档；stream_usage=false 不发 stream_options；
    调用方 timeout 管建连+首块，spec.timeout 只在首块后换成 socket 静默上限。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "z")
    monkeypatch.delenv("ZHIPU_BASE_URL", raising=False)
    capture["reply"] = sse(delta("ok", finish="stop"))
    glm = llm_client.MODELS["glm-5.3-flash"]
    prov.openai_compat({**glm, "timeout": 30}, [], None, "medium", timeout=90)
    assert capture["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert capture["headers"]["authorization"] == "Bearer z"
    assert capture["body"]["model"] == "glm-5.3-flash"
    assert capture["body"]["reasoning_effort"] == "high"
    assert "stream_options" not in capture["body"]
    assert capture["timeout"] == 90 and capture["silence"] == 30
    capture.pop("silence")
    prov.openai_compat(glm, [], None, "high", timeout=90)
    assert capture["body"]["reasoning_effort"] == "high" and capture["timeout"] == 90
    assert "silence" not in capture   # 无 spec.timeout：socket 超时不动


def test_openai_compat_merges_leading_system_messages(capture):
    capture["reply"] = sse(delta("", finish="stop"))
    prov.openai_compat(DS, [{"role": "system", "content": "静态"}, {"role": "system", "content": "## 当前时间"},
                            {"role": "user", "content": "hi"}], None, "high")
    assert capture["body"]["messages"] == [{"role": "system", "content": "静态\n\n## 当前时间"},
                                           {"role": "user", "content": "hi"}]


def test_anthropic_cache_breakpoint_on_second_to_last_system():
    three = [{"role": "system", "content": s} for s in ("A", "B", "C")] + [{"role": "user", "content": "q"}]
    system, _ = prov._to_anthropic(three)
    assert [("cache_control" in b) for b in system] == [False, True, False]
    one, _ = prov._to_anthropic([{"role": "system", "content": "A"}, {"role": "user", "content": "q"}])
    assert one == [{"type": "text", "text": "A"}]


def test_openai_compat_base_url_env_and_effort_off(capture, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/")
    capture["reply"] = sse(delta("", finish="stop"))
    prov.openai_compat({**DS, "effort": False}, [], None, "high")
    assert capture["url"] == "http://localhost:11434/v1/chat/completions"
    assert "reasoning_effort" not in capture["body"] and "tools" not in capture["body"]


def test_http_error_body_logged_returns_none(capture, caplog):
    capture["reply"] = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))
    assert prov.openai_compat(DS, [], None, "high") is None
    assert "bad key" in caplog.text


@pytest.mark.parametrize("reply", [
    sse({"error": {"message": "overloaded"}}),        # 流里的错误块
    sse(),                                            # 建连后空关：无 finish 无内容
    b"",                                              # 连 [DONE] 都没有
    sse(delta("半截")),                                # 有内容但无 finish_reason 就 [DONE]：半截
    b"data: " + json.dumps(delta("half")).encode() + b"\n\n",   # 半截后 EOF
    sse("data: {not json"),                           # 坏行
    sse({"choices": [{"delta": "text"}]}),            # delta 非 dict
    sse(delta("x"), {"choices": "nope"}, delta(finish="stop")),
])
def test_openai_compat_malformed_stream_returns_none(capture, caplog, reply):
    capture["reply"] = reply
    assert prov.openai_compat(DS, [], None, "high") is None
    assert "调用失败" in caplog.text


def test_silence_timeout_mid_stream_returns_none(monkeypatch, caplog):
    """流中途静默超时（socket.timeout 从迭代抛出）→ None，交给 llm_client 走 fallback。"""
    import socket

    class Hang(io.BytesIO):
        def __iter__(self):
            yield b"data: " + json.dumps(delta("half")).encode() + b"\n"
            raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: Hang())
    assert prov.openai_compat(DS, [], None, "high") is None
    assert "timed out" in caplog.text


def test_stream_stops_reading_once_finish_and_usage_arrived(monkeypatch):
    """finish_reason + usage 都到手就不再读：末块后挂着不发 [DONE] 的代理不能把完整回复拖成超时。"""
    import socket

    class HangAfterEnd(io.BytesIO):
        def __iter__(self):
            yield b"data: " + json.dumps(delta("done", finish="stop")).encode() + b"\n"
            yield b"data: " + json.dumps({"choices": [], "usage": {"completion_tokens": 1}}).encode() + b"\n"
            raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: HangAfterEnd())
    out = prov.openai_compat(DS, [], None, "high")
    assert out["content"] == "done" and out["_finish"] == "stop" and out["_usage"] == {"completion_tokens": 1}


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
    assert system == [{"type": "text", "text": "S1", "cache_control": {"type": "ephemeral"}},
                      {"type": "text", "text": "S2"}]
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
    assert b["system"] == [{"type": "text", "text": "sys"}]   # 单条：无断点
    assert b["messages"] == [{"role": "user", "content": "hi"}]
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
    assert capture["timeout"] == 600   # 非流式 + thinking：默认超时须远大于 openai_compat 的 120


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


def test_config_ships_glm_entry_as_deepseek_fallback():
    glm = llm_client.MODELS["glm-5.3-flash"]
    assert glm["provider"] == "openai_compat" and glm["api_key_env"] == "ZHIPU_API_KEY"
    assert glm["api_model"] == "glm-5.3-flash"
    assert llm_client.canon_model("glm") == llm_client.canon_model("ZHIPU") == "glm-5.3-flash"
    ds = llm_client.MODELS["deepseek-flash"]
    assert ds["fallback"] == "glm-5.3-flash" and ds["timeout"] == 30
    assert ds["api_key_env"] == "DEEPSEEK_API_KEY"   # 内置字段保留


# ── fallback ────────────────────────────────────────────────

@pytest.fixture
def two_models(monkeypatch):
    """主 m 回 calls["m_reply"]（默认 None=失败），备 fb 记录被调参数。"""
    llm_client.load_models({"llm": {"models": {
        "m": {"provider": "openai_compat", "api_key_env": "K1", "base_url": "u", "fallback": "fb"},
        "fb": {"provider": "anthropic", "api_key_env": "K2", "base_url": "u"}}}})
    monkeypatch.setenv("K1", "1")
    monkeypatch.setenv("K2", "2")
    calls = {"m": 0, "fb": [], "m_reply": None}

    def primary(spec, *a, **o):
        calls["m"] += 1
        return calls["m_reply"]

    def backup(spec, m, t, e, **o):
        calls["fb"].append((spec["api_model"], m, t, e, o))
        return {"content": "备"}

    monkeypatch.setitem(prov.PROVIDERS, "openai_compat", primary)
    monkeypatch.setitem(prov.PROVIDERS, "anthropic", backup)
    yield calls
    llm_client.load_models()


def test_chat_falls_back_when_primary_returns_none(two_models, caplog):
    out = llm_client.chat([{"role": "user", "content": "q"}], [{"t": 1}], "m", "low", timeout=5)
    assert out == {"content": "备", "_fallback": "fb"}
    assert two_models["fb"] == [("fb", [{"role": "user", "content": "q"}], [{"t": 1}], "low",
                                 {"timeout": 5})]
    assert "改用 fb" in caplog.text


def test_chat_no_fallback_when_primary_ok(two_models):
    two_models["m_reply"] = {"content": "主"}
    assert llm_client.chat([], None, "m", "low") == {"content": "主"}
    assert two_models["fb"] == []


def test_chat_fallback_skipped_without_key(two_models, monkeypatch):
    monkeypatch.delenv("K2")
    assert llm_client.fallback_of("m") == ""
    assert llm_client.chat([], None, "m", "low") is None
    assert two_models["fb"] == []


def test_fallback_of_ignores_self_and_unknown():
    llm_client.load_models({"llm": {"models": {
        "a": {"provider": "openai_compat", "api_key_env": "K", "base_url": "u", "fallback": "a"},
        "b": {"provider": "openai_compat", "api_key_env": "K", "base_url": "u", "fallback": "ghost"}}}})
    try:
        assert llm_client.fallback_of("a") == "" and llm_client.fallback_of("b") == ""
        assert llm_client.fallback_of("claude-opus-5") == ""
    finally:
        llm_client.load_models()
