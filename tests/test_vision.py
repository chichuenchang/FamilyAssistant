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
    box = {"reply": {"content": "一只橘猫趴在沙发上"}}

    def fake_chat(msgs, tools, model, effort, **kw):
        box.update(msgs=msgs, model=model, kw=kw)
        return box["reply"]

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


def test_vision_model_prefers_config_then_first_capable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm_client._rt, "CONFIG", {"llm": {"vision_model": "glm"}})
    assert llm_client.vision_model() == "deepseek-flash"   # glm 不识图：退到第一个识图模型
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert llm_client.vision_model() == ""
