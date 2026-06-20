# tests/test_members.py — 成员注册表（Agent_Runtime/members.py）测试。
import json
from pathlib import Path

import pytest
import members as mm


@pytest.fixture
def cfg(tmp_path):
    """临时 members.json，含两个成员。"""
    p = tmp_path / "members.json"
    p.write_text(json.dumps({
        "爸爸": {"telegram": ["111"], "wechat": ["wx_a"]},
        "妈妈": {"wechat": ["wx_b"]},
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


def test_resolve_missing_file_is_lockdown(tmp_path):
    assert mm.resolve("telegram", "111", tmp_path / "members.json") is None


def test_resolve_corrupt_file_is_lockdown(tmp_path):
    p = tmp_path / "members.json"
    p.write_text("{ not json", encoding="utf-8")
    assert mm.resolve("telegram", "111", p) is None


def test_member_names(cfg):
    assert mm.member_names(cfg) == ["爸爸", "妈妈"]


def test_add_member_new(cfg):
    mm.add_member("娃", telegram=["333"], members_path=cfg)
    assert mm.resolve("telegram", "333", cfg) == "娃"
    # 其他成员不受影响
    assert mm.resolve("telegram", "111", cfg) == "爸爸"


def test_add_member_appends_ids_to_existing(cfg):
    mm.add_member("妈妈", telegram=["222"], members_path=cfg)
    assert mm.resolve("telegram", "222", cfg) == "妈妈"
    assert mm.resolve("wechat", "wx_b", cfg) == "妈妈"


def test_add_member_rejects_id_bound_to_other_member(cfg):
    with pytest.raises(ValueError):
        mm.add_member("娃", telegram=["111"], members_path=cfg)


def test_add_member_same_id_same_member_is_noop(cfg):
    mm.add_member("爸爸", telegram=["111"], members_path=cfg)
    assert mm.load_members(cfg)["爸爸"]["telegram"] == ["111"]


def test_add_member_empty_name_rejected(cfg):
    with pytest.raises(ValueError):
        mm.add_member("", telegram=["444"], members_path=cfg)


def test_add_member_creates_file_when_missing(tmp_path):
    p = tmp_path / "members.json"
    mm.add_member("娃", wechat=["wx_c"], members_path=p)
    assert mm.resolve("wechat", "wx_c", p) == "娃"


def test_remove_member(cfg):
    assert mm.remove_member("妈妈", cfg) is True
    assert mm.resolve("wechat", "wx_b", cfg) is None
    assert mm.remove_member("不存在", cfg) is False


# ── 别名/法定名（aliases） ──────────────────────────────────

def test_add_member_with_aliases_only(cfg):
    """仅别名也可登记（如还没手机的孩子）；不影响频道闸门。"""
    mm.add_member("娃", aliases=["Legal Name", "法定名"], members_path=cfg)
    assert mm.load_members(cfg)["娃"]["aliases"] == ["Legal Name", "法定名"]
    assert "telegram" not in mm.load_members(cfg)["娃"]
    # 别名不参与 resolve（不是频道 id）
    assert mm.resolve("telegram", "Legal Name", cfg) is None


def test_add_member_appends_aliases_dedup(cfg):
    mm.add_member("爸爸", aliases=["法定名A"], members_path=cfg)
    mm.add_member("爸爸", aliases=["法定名A", "法定名B"], members_path=cfg)
    assert mm.load_members(cfg)["爸爸"]["aliases"] == ["法定名A", "法定名B"]


def test_add_member_rejects_alias_owned_by_other(cfg):
    mm.add_member("爸爸", aliases=["法定名A"], members_path=cfg)
    with pytest.raises(ValueError):
        mm.add_member("妈妈", aliases=["法定名A"], members_path=cfg)


def test_add_member_rejects_alias_equal_to_other_member_name(cfg):
    with pytest.raises(ValueError):
        mm.add_member("妈妈", aliases=["爸爸"], members_path=cfg)


def test_add_member_blank_aliases_ignored(cfg):
    mm.add_member("爸爸", aliases=["  ", ""], members_path=cfg)
    assert "aliases" not in mm.load_members(cfg)["爸爸"]


# ── 成员目录名 + 同步偏好（dir / sync 字段） ──────────────────

@pytest.fixture
def cfg_dirsync(tmp_path):
    """临时 members.json，含 dir 目录名与 sync 同步偏好。"""
    p = tmp_path / "members.json"
    p.write_text(json.dumps({
        "Alex Lee": {"dir": "Alex", "wechat": ["wx_j"],
                      "sync": {"schedule": {"provider": "google_calendar", "enabled": True},
                               "tasks": {"provider": "google_tasks", "enabled": True}}},
        "Sam Lee": {"dir": "Sam"},
        "Robin": {},
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_member_dir_name_explicit(cfg_dirsync):
    assert mm.member_dir_name("Alex Lee", cfg_dirsync) == "Alex"
    assert mm.member_dir_name("Sam Lee", cfg_dirsync) == "Sam"


def test_member_dir_name_slug_fallback(cfg_dirsync):
    # 无 dir 字段 → 取首词小写 slug
    assert mm.member_dir_name("Robin", cfg_dirsync) == "robin"
    # 完全未登记的成员
    assert mm.member_dir_name("New Person", cfg_dirsync) == "new"


def test_sync_pref(cfg_dirsync):
    assert mm.sync_pref("Alex Lee", "schedule", cfg_dirsync) == {
        "provider": "google_calendar", "enabled": True}
    assert mm.sync_pref("Alex Lee", "tasks", cfg_dirsync) == {
        "provider": "google_tasks", "enabled": True}


def test_sync_pref_absent_is_none(cfg_dirsync):
    assert mm.sync_pref("Sam Lee", "schedule", cfg_dirsync) is None
    assert mm.sync_pref("Robin", "tasks", cfg_dirsync) is None
    assert mm.sync_pref("New Person", "schedule", cfg_dirsync) is None


def test_backup_pref_absent_is_none(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text('{"Alex Lee": {"dir": "Alex"}}', encoding="utf-8")
    assert members.backup_pref("Alex Lee", members_path=mp) is None


def test_backup_pref_unknown_member_is_none(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text('{}', encoding="utf-8")
    assert members.backup_pref("Nobody", members_path=mp) is None


def test_backup_pref_normalizes_and_defaults(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({"Alex Lee": {"dir": "Alex", "backup": {
        "provider": "google_drive", "enabled": True,
        "scopes": ["Alex", "Family"]}}}), encoding="utf-8")
    p = members.backup_pref("Alex Lee", members_path=mp)
    assert p["provider"] == "google_drive"
    assert p["cred_prefix"] == "GDRIVE"          # default
    assert p["remote_root"] == "Alex"             # default = dir name
    assert p["enabled"] is True
    assert p["scopes"] == ["Alex", "Family"]


def test_backup_pref_explicit_prefix_and_root(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({"Sam Lee": {"dir": "Sam", "backup": {
        "provider": "google_drive", "cred_prefix": "WLI_GDRIVE",
        "remote_root": "SamBackup", "enabled": False, "scopes": []}}}),
        encoding="utf-8")
    p = members.backup_pref("Sam Lee", members_path=mp)
    assert p["cred_prefix"] == "WLI_GDRIVE"
    assert p["remote_root"] == "SamBackup"
    assert p["enabled"] is False
