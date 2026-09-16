"""skill_registry：manifest 契约 + 发现/合并。"""
import textwrap

import bootstrap
import pytest
import skill_registry
import tool_runtime as rt


def test_every_tool_has_schema_and_vice_versa():
    reg = skill_registry.load()
    schema_names = [t["function"]["name"] for t in reg.schemas]
    assert len(schema_names) == len(set(schema_names))          # schema 不重名
    assert set(schema_names) == set(reg.tool_map)               # 一一对应


def test_passthrough_commands_are_allowed_and_routed():
    reg = skill_registry.load()
    for skill, m in reg.modules.items():
        for name, impl in getattr(m, "TOOLS", {}).items():
            if isinstance(impl, str):
                assert impl in rt.ALLOWED, (skill, name, impl)
                assert rt.cli_path(impl).parent.name == skill, (skill, name, impl)


def test_policy_sets_reference_real_tools():
    reg = skill_registry.load()
    names = set(reg.tool_map)
    for s in (reg.member_locked, reg.context_tools, reg.image_tools, reg.doc_tools):
        assert s <= names, s - names


def test_local_only_commands_never_allowed():
    skill_registry.load()
    for cmd in ("doc-remove", "backup-restore", "backup-reorg",
                "member-add", "member-remove"):
        assert cmd not in rt.ALLOWED, cmd


def test_new_skill_dir_is_discovered_without_hub_change(tmp_path, monkeypatch):
    skill = tmp_path / "Toy_Skill"
    skill.mkdir()
    (skill / "cli.py").write_text("", encoding="utf-8")
    (skill / "agent_tools.py").write_text(textwrap.dedent('''
        from tool_runtime import fn, s
        ORDER = 5
        COMMANDS = {"toy-ping"}
        TOOLS = {"toy_ping": "toy-ping"}
        MEMBER_LOCKED = {"toy_ping"}
        SCHEMAS = [fn("toy_ping", "ping", {"x": s("x")})]
        PROMPT_SECTIONS = ["## 玩具\\n- 说 ping 就调 toy_ping"]
        PROMPT_RULES = ["toy 规则"]
        CONTEXT_FNS = [lambda member: f"\\n\\n## toy {member}"]
        IMAGE_ROUTES = ["toy 图路由"]
    '''), encoding="utf-8")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))   # 不读真实 data/
    real = bootstrap.skill_dirs()
    monkeypatch.setattr(bootstrap, "skill_dirs", lambda: [skill, *real])
    reg = skill_registry.load()
    assert "toy_ping" in reg.tool_map
    assert "toy-ping" in rt.ALLOWED and rt.cli_path("toy-ping") == skill / "cli.py"
    assert reg.prompt_sections[0].startswith("## 玩具")       # ORDER=5 排最前
    assert "toy 规则" in reg.prompt_rules
    assert "toy_ping" in reg.member_locked
    assert reg.context("Alex").startswith("\n\n## toy Alex")   # 注入块同样按 ORDER
    assert reg.image_routes[0] == "toy 图路由"
    monkeypatch.setattr(bootstrap, "skill_dirs", lambda: real)
    skill_registry.load()                                    # 幂等：恢复真实注册表
    assert "toy-ping" not in rt.ALLOWED


def test_duplicate_tool_name_rejected(tmp_path, monkeypatch):
    skill = tmp_path / "Dup_Skill"
    skill.mkdir()
    (skill / "agent_tools.py").write_text(
        'TOOLS = {"save_note": "note-add"}\n', encoding="utf-8")
    real = bootstrap.skill_dirs()
    monkeypatch.setattr(bootstrap, "skill_dirs", lambda: [*real, skill])
    try:
        skill_registry.load()
        assert False, "应当拒绝重名工具"
    except RuntimeError as e:
        assert "save_note" in str(e)
    finally:
        monkeypatch.setattr(bootstrap, "skill_dirs", lambda: real)
        skill_registry.load()


def test_context_swallows_failing_injector():
    reg = skill_registry.Registry()

    def boom(member):
        raise RuntimeError("x")

    reg.context_fns = [boom, lambda m: "ok"]
    assert reg.context("A") == "ok"


def test_duplicate_module_name_across_skills_rejected(tmp_path):
    a, b = tmp_path / "A_Skill", tmp_path / "B_Skill"
    a.mkdir(); b.mkdir()
    (a / "cli.py").write_text("", encoding="utf-8")       # 允许重名
    (b / "cli.py").write_text("", encoding="utf-8")
    bootstrap.check_unique_modules([a, b])
    (a / "helper.py").write_text("", encoding="utf-8")
    (b / "helper.py").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="helper.py"):
        bootstrap.check_unique_modules([a, b])
