# tests/test_members.py — 成员注册表（Agent_Runtime/members.py）测试。
import json
from pathlib import Path

import pytest
import members as mm


@pytest.fixture
def cfg(tmp_path):
    """临时 config.json，含两个成员。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "members": {
            "爸爸": {"telegram": ["111"], "wechat": ["wx_a"]},
            "妈妈": {"wechat": ["wx_b"]},
        }
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_resolve_known_ids(cfg):
    assert mm.resolve("telegram", "111", cfg) == "爸爸"
    assert mm.resolve("wechat", "wx_a", cfg) == "爸爸"
    assert mm.resolve("wechat", "wx_b", cfg) == "妈妈"


def test_resolve_accepts_int_id(cfg):
    assert mm.resolve("telegram", 111, cfg) == "爸爸"


def test_resolve_unknown_returns_none(cfg):
    assert mm.resolve("telegram", "999", cfg) is None
    assert mm.resolve("wechat", "wx_zzz", cfg) is None
    assert mm.resolve("telegram", "", cfg) is None


def test_resolve_missing_members_section_is_lockdown(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    assert mm.resolve("telegram", "111", p) is None


def test_resolve_corrupt_config_is_lockdown(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not json", encoding="utf-8")
    assert mm.resolve("telegram", "111", p) is None


def test_member_names(cfg):
    assert mm.member_names(cfg) == ["爸爸", "妈妈"]


def test_add_member_new(cfg):
    mm.add_member("娃", telegram=["333"], config_path=cfg)
    assert mm.resolve("telegram", "333", cfg) == "娃"
    # 其他成员不受影响
    assert mm.resolve("telegram", "111", cfg) == "爸爸"


def test_add_member_appends_ids_to_existing(cfg):
    mm.add_member("妈妈", telegram=["222"], config_path=cfg)
    assert mm.resolve("telegram", "222", cfg) == "妈妈"
    assert mm.resolve("wechat", "wx_b", cfg) == "妈妈"


def test_add_member_rejects_id_bound_to_other_member(cfg):
    with pytest.raises(ValueError):
        mm.add_member("娃", telegram=["111"], config_path=cfg)


def test_add_member_same_id_same_member_is_noop(cfg):
    mm.add_member("爸爸", telegram=["111"], config_path=cfg)
    assert mm.load_members(cfg)["爸爸"]["telegram"] == ["111"]


def test_add_member_empty_name_rejected(cfg):
    with pytest.raises(ValueError):
        mm.add_member("", telegram=["444"], config_path=cfg)


def test_add_member_preserves_other_config_keys(cfg):
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    raw["base_currency"] = "USD"
    cfg.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    mm.add_member("娃", wechat=["wx_c"], config_path=cfg)
    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["base_currency"] == "USD"


def test_remove_member(cfg):
    assert mm.remove_member("妈妈", cfg) is True
    assert mm.resolve("wechat", "wx_b", cfg) is None
    assert mm.remove_member("不存在", cfg) is False
