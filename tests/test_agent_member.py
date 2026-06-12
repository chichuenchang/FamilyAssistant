# tests/test_agent_member.py — agent_core 成员闸门与防冒名注入。
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


def test_apply_member_keeps_llm_member_on_reads():
    out = agent_core._apply_member("list_transactions", {"member": "妈妈"}, "爸爸")
    assert out["member"] == "妈妈"


def test_handle_returns_empty_without_member():
    agent = agent_core.Agent()
    assert agent.handle("记账 午餐45", user="x") == ""
    assert agent.handle("记账 午餐45", user="x", member="") == ""


def test_handle_image_returns_empty_without_member(tmp_path):
    agent = agent_core.Agent()
    assert agent.handle_image(str(tmp_path / "x.jpg"), user="x", member="") == ""


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
