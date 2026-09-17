# tests/test_pdf_agent.py — PDF_Editor 的 agent_core 接线（取代 Form_Filler）。
import agent_core

PDF_TOOLS = {"inspect_pdf", "edit_pdf", "pdf_edit_list"}


def test_commands_allowed_and_routed():
    for c in ("pdf-inspect", "pdf-edit", "pdf-list"):
        assert c in agent_core.ALLOWED_COMMANDS
        assert agent_core._cli_path(c).parent.name == "PDF_Editor"


def test_tools_registered_with_schema():
    names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
    assert PDF_TOOLS <= names
    assert PDF_TOOLS <= set(agent_core._TOOL_MAP)


def test_form_filler_is_gone():
    names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
    assert not any(n.startswith("fill_form") for n in names)
    assert "form-scan" not in agent_core.ALLOWED_COMMANDS


def test_member_always_injected():
    for tool in PDF_TOOLS:
        out = agent_core._apply_member(tool, {"member": "Wenliang", "session": "x"}, "Jim")
        assert out["member"] == "Jim"


def test_edit_is_doc_tool_and_outputs_are_fenced():
    assert "edit_pdf" in agent_core._DOC_TOOLS
    import skill_registry
    reg = skill_registry.load()
    assert {"inspect_pdf", "edit_pdf"} <= reg.untrusted_tools


def test_long_timeouts():
    assert agent_core._CLI_TIMEOUTS["pdf-inspect"] >= 120
    assert agent_core._CLI_TIMEOUTS["pdf-edit"] >= 300


def test_doc_sentinel_takes_first_line_only():
    reply = "⚙️ edit_pdf\n\n好了\n\x01DOC:jim/pdf_edits/x/form_edited.pdf"
    text, imgs, docs = agent_core.split_reply(reply)
    assert docs == ["jim/pdf_edits/x/form_edited.pdf"]


def test_system_prompt_describes_one_shot_workflow():
    p = agent_core._build_system_prompt()
    assert "edit_pdf" in p and "inspect_pdf" in p
    assert "一条消息里把缺的值一起问" in p
    assert "一条消息只问一个" not in p
