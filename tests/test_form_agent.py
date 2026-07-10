# tests/test_form_agent.py — Form_Filler 的 agent_core 接线。
import agent_core


FORM_TOOLS = {"fill_form_scan", "fill_form_define_fields", "fill_form_next",
              "fill_form_set_answer", "fill_form_render", "fill_form_list",
              "fill_form_cancel"}


def test_commands_allowed_and_routed():
    for c in ("form-scan", "form-define", "form-next", "form-set",
              "form-render", "form-list", "form-cancel"):
        assert c in agent_core.ALLOWED_COMMANDS
        assert agent_core._cli_path(c).parent.name == "Form_Filler"


def test_tools_registered_with_schema():
    names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
    assert FORM_TOOLS <= names
    assert FORM_TOOLS <= set(agent_core._TOOL_MAP)


def test_member_always_injected():
    for tool in FORM_TOOLS:
        out = agent_core._apply_member(tool, {"member": "Wenliang", "session": "x"}, "Jim")
        assert out["member"] == "Jim"


def test_render_is_doc_tool():
    assert "fill_form_render" in agent_core._DOC_TOOLS


def test_scan_render_get_long_timeout():
    assert agent_core._CLI_TIMEOUTS["form-scan"] >= 120
    assert agent_core._CLI_TIMEOUTS["form-render"] >= 120


def test_doc_sentinel_takes_first_line_only():
    # form-render 第一行是路径，后续可能有"警告:"行 —— 哨兵只取第一行
    reply = "⚙️ fill_form_render\n\n好了\n\x01DOC:jim/forms/x_filled.pdf"
    text, imgs, docs = agent_core.split_reply(reply)
    assert docs == ["jim/forms/x_filled.pdf"]


def test_system_prompt_mentions_form_workflow():
    p = agent_core._build_system_prompt()
    assert "fill_form_scan" in p
    assert "一条消息只问一个" in p
