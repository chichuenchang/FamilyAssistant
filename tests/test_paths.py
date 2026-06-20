# tests/test_paths.py — 磁盘布局解析（Agent_Runtime/paths.py）测试。
import json
from pathlib import Path

import pytest

import members
import paths


@pytest.fixture
def env(tmp_path, monkeypatch):
    """临时 members.json（含 dir）+ DATA_ROOT 指向 tmp。"""
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({
        "Alex Lee": {"dir": "Alex",
                      "sync": {"schedule": {"provider": "google_calendar", "enabled": True}}},
        "Sam Lee": {"dir": "Sam"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(members, "MEMBERS_PATH", mp)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    return tmp_path


def test_family_ledger(env):
    assert paths.family_ledger().as_posix().endswith("data/Family/ledger.db")


def test_member_schedule_store(env):
    assert paths.member_store("Alex Lee", "schedule").as_posix().endswith(
        "data/Alex/schedule/schedule.db")


def test_member_tasks_store(env):
    assert paths.member_store("Alex Lee", "tasks").as_posix().endswith(
        "data/Alex/tasks/tasks.db")


def test_member_notes_store(env):
    assert paths.member_store("Alex Lee", "notes").as_posix().endswith(
        "data/Alex/notes/notes.db")


def test_member_store_bad_domain(env):
    with pytest.raises(ValueError):
        paths.member_store("Alex Lee", "bogus")


def test_rel_roundtrip(env):
    p = paths.resolve_rel("Family/receipts/2026-06/x.jpg")
    assert paths.to_rel(p) == "Family/receipts/2026-06/x.jpg"


def test_to_rel_passthrough_relative(env):
    # 已是 data_root 相对形式 → 原样 posix 返回
    assert paths.to_rel("Alex/notes/2026-06/y.jpg") == "Alex/notes/2026-06/y.jpg"


def test_unknown_member_slug(env):
    assert paths.member_dir("New Person").as_posix().endswith("data/new")


def test_sync_state_path(env):
    assert paths.member_sync_state("Alex Lee", "tasks").as_posix().endswith(
        "data/Alex/tasks/.sync_state.json")


def test_notes_image_dir_created(env):
    from datetime import date
    d = paths.member_notes_image_dir("Alex Lee", date(2026, 6, 1))
    assert d.exists() and d.as_posix().endswith("data/Alex/notes/2026-06")
