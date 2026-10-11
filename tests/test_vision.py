# tests/test_vision.py — view_image 工具（识图模型看图）。
import pytest

import llm_client
import skill_registry

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture
def vision(tmp_path, monkeypatch):
    """数据根隔离 + 假 chat：记录发出的消息与参数。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    box = {"reply": {"content": "一只橘猫趴在沙发上"}, "models": []}

    def fake_chat(msgs, tools, model, effort, **kw):
        box.update(msgs=msgs, model=model, kw=kw)
        box["models"].append(model)
        r = box["reply"]
        return r(model) if callable(r) else r

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    (tmp_path / "cat.png").write_bytes(PNG)
    (tmp_path / "dog.png").write_bytes(PNG)
    (tmp_path / "form.pdf").write_bytes(b"%PDF-1.7")
    box["dir"] = tmp_path
    box["tool"] = skill_registry.load().tool_map["view_image"]
    return box


def test_view_image_sends_question_and_images_without_fallback(vision):
    d = vision["dir"]
    out = vision["tool"]({"paths": [str(d / "cat.png"), str(d / "dog.png")], "question": "哪只是猫"})
    assert out == "一只橘猫趴在沙发上"
    content = vision["msgs"][-1]["content"]
    assert content[0] == {"type": "text", "text": "哪只是猫"}
    assert [p["type"] for p in content[1:]] == ["image_url", "image_url"]
    assert vision["model"] == "deepseek-flash"
    assert vision["kw"] == {"fallback": False}           # 带图不顶替给不识图的模型


def test_view_image_accepts_single_string_path_and_default_question(vision):
    vision["tool"]({"paths": str(vision["dir"] / "cat.png")})
    assert vision["msgs"][-1]["content"][0]["text"] == "详细描述这张图的内容。"


def test_view_image_rejects_pdf_outside_and_empty(vision, tmp_path_factory):
    tool = vision["tool"]
    assert "ocr_read" in tool({"paths": [str(vision["dir"] / "form.pdf")]})
    outside = tmp_path_factory.mktemp("outside") / "x.png"
    outside.write_bytes(PNG)
    assert tool({"paths": [str(outside)]}).startswith("[错误] 只允许")
    assert tool({}).startswith("[错误]")
    assert "msgs" not in vision                           # 都没发请求


def test_view_image_without_vision_model_or_reply(vision, monkeypatch):
    p = {"paths": [str(vision["dir"] / "cat.png")]}
    vision["reply"] = None
    assert vision["tool"](p).startswith("[看图失败]")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert vision["tool"](p).startswith("[看图不可用]")


def test_view_image_result_is_fenced_and_rule_in_prompt():
    reg = skill_registry.load()
    assert "view_image" in reg.untrusted_tools           # 图中文字可能是注入
    assert any("view_image" in r and "ocr_read" in r for r in reg.prompt_rules)


@pytest.fixture
def two_vision(monkeypatch):
    llm_client.load_models({"llm": {"models": {"qwen-vl": {
        "provider": "openai_compat", "api_key_env": "QWEN_KEY", "base_url": "u", "vision": True}}}})
    monkeypatch.setenv("QWEN_KEY", "q")
    yield
    llm_client.load_models()


def test_vision_models_config_first_then_table_order(two_vision, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm_client._rt, "CONFIG", {"llm": {"vision_model": "qwen-vl"}})
    assert llm_client.vision_models() == ["qwen-vl", "deepseek-flash"]
    monkeypatch.setattr(llm_client._rt, "CONFIG", {"llm": {"vision_model": "glm"}})
    assert llm_client.vision_models() == ["deepseek-flash", "qwen-vl"]   # glm 不识图：忽略
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.delenv("QWEN_KEY")
    assert llm_client.vision_models() == []


def test_view_image_tries_next_vision_model_when_one_fails(vision, two_vision):
    vision["reply"] = lambda m: None if m == "deepseek-flash" else {"content": "猫"}
    assert vision["tool"]({"paths": [str(vision["dir"] / "cat.png")]}) == "猫"
    assert vision["models"] == ["deepseek-flash", "qwen-vl"]
    vision["reply"] = None
    out = vision["tool"]({"paths": [str(vision["dir"] / "cat.png")]})
    assert out == "[看图失败] 识图模型（deepseek-flash、qwen-vl）都没回答"


def test_prompt_tells_agent_to_switch_tool_on_failure():
    rules = "\n".join(skill_registry.load().prompt_rules)
    for marker in ("[OCR 未配置]", "[OCR 失败]", "[未识别到文字]", "[看图不可用]", "[看图失败]"):
        assert marker in rules, marker
