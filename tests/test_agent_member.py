# tests/test_agent_member.py — agent_core 成员闸门与防冒名注入。
import pytest
import agent_core


def test_apply_member_forces_resolved_member_on_writes():
    out = agent_core._apply_member("add_transaction",
                                   {"member": "妈妈", "amount": 5}, "爸爸")
    assert out["member"] == "爸爸"


def test_apply_member_strips_llm_member_when_no_resolved_member():
    out = agent_core._apply_member("add_transaction", {"member": "妈妈"}, "")
    assert "member" not in out


def test_apply_member_covers_all_write_tools():
    for tool in ("add_transaction", "add_deposit", "add_transfer", "add_tax"):
        out = agent_core._apply_member(tool, {}, "爸爸")
        assert out["member"] == "爸爸", tool


def test_apply_member_forces_sheet_and_send_tools():
    # 工作表 + 发文档/文件按成员私有：强制注入发送者，剥离 LLM 冒名 member。
    for tool in ("create_worksheet", "show_worksheet", "add_worksheet_row",
                 "visualize_data", "send_document", "send_file"):
        out = agent_core._apply_member(tool, {"member": "妈妈"}, "爸爸")
        assert out["member"] == "爸爸", tool


def test_apply_member_keeps_llm_member_on_reads():
    out = agent_core._apply_member("list_transactions", {"member": "妈妈"}, "爸爸")
    assert out["member"] == "妈妈"


def test_handle_returns_empty_without_member():
    agent = agent_core.Agent()
    assert agent.handle("记账 午餐45", user="x") == ""
    assert agent.handle("记账 午餐45", user="x", member="") == ""


def test_handle_media_returns_empty_without_member(tmp_path):
    agent = agent_core.Agent()
    assert agent.handle_media([str(tmp_path / "x.jpg")], "记账", user="x", member="") == ""


def test_system_prompt_includes_members_and_aliases(monkeypatch):
    monkeypatch.setattr(agent_core._members_registry, "load_members",
                        lambda members_path=None: {
                            "爸爸": {"telegram": ["1"], "aliases": ["法定名甲", "Legal A"]},
                            "妈妈": {"wechat": ["wx"]},
                        })
    prompt = agent_core._build_system_prompt()
    assert "## 家庭成员" in prompt
    assert "法定名甲" in prompt and "Legal A" in prompt
    assert "妈妈" in prompt


def test_system_prompt_omits_member_block_when_registry_empty(monkeypatch):
    monkeypatch.setattr(agent_core._members_registry, "load_members",
                        lambda members_path=None: {})
    prompt = agent_core._build_system_prompt()
    assert "## 家庭成员" not in prompt


def test_handle_media_ocr_and_instruction_drive_handle(monkeypatch):
    # 图片/PDF 同一入口；多份材料 + 用户文字指令合成一轮
    import ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_image", lambda path: "CONSENT FORM TEXT")
    agent = agent_core.Agent()
    cap = {}
    monkeypatch.setattr(agent, "handle",
                        lambda prompt, user="default", member="", said=None: cap.update(p=prompt, s=said) or "ok")
    out = agent.handle_media(["data/Alex/inbox/2026-06/x.pdf", "data/Alex/inbox/2026-06/y.jpg"],
                             "签好发给学校", user="u", member="Alex Lee")
    assert out == "ok"
    assert "x.pdf" in cap["p"] and "y.jpg" in cap["p"] and "CONSENT FORM TEXT" in cap["p"]
    assert "2 份材料" in cap["p"] and "签好发给学校" in cap["p"]
    assert cap["s"] == "签好发给学校"
    # 分流条目来自各 skill 的 IMAGE_ROUTES，按 ORDER 编号
    p = cap["p"]
    for tool in ("add_transaction", "add_document", "save_note", "add_event", "edit_pdf"):
        assert tool in p, tool
    assert "1) 单张消费票据" in p and "6) 用户此前" in p


def test_handle_media_without_ocr_still_follows_instruction(monkeypatch):
    # OCR 不可用 / 没识别到文字：只给路径，照样交 LLM 按用户指令办
    import ocr
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    monkeypatch.setattr(ocr, "ocr_image", lambda path: pytest.fail("OCR 不可用不该调"))
    agent = agent_core.Agent()
    cap = {}
    monkeypatch.setattr(agent, "handle",
                        lambda prompt, user="default", member="", said=None: cap.update(p=prompt) or "ok")
    assert agent.handle_media(["x.pdf"], "午餐45块", member="Alex Lee") == "ok"
    assert "x.pdf" in cap["p"] and "无 OCR 文字" in cap["p"] and "午餐45块" in cap["p"]
