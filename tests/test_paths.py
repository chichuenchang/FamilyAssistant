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


def test_member_forms_dir_created(env):
    d = paths.member_forms_dir("Alex Lee")
    assert d.is_dir() and d.as_posix().endswith("data/Alex/forms")


def test_family_documents_db(env):
    assert paths.family_documents_db().as_posix().endswith("data/Family/documents.db")


def test_state_file_adopts_legacy_root_file(env):
    legacy = paths.data_root() / ".telegram_offset"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("42", encoding="utf-8")
    p = paths.state_file(".telegram_offset")
    assert paths.to_rel(p) == ".state/.telegram_offset"
    assert p.read_text(encoding="utf-8") == "42" and not legacy.exists()


def test_state_file_keeps_existing_over_legacy(env):
    paths.state_file("x.json").write_text("new", encoding="utf-8")
    (paths.data_root() / "x.json").write_text("old", encoding="utf-8")
    assert paths.state_file("x.json").read_text(encoding="utf-8") == "new"


def test_member_cache_dir_adopts_legacy_dir(env):
    name = members.member_names()[0]
    legacy = paths.member_dir(name) / "charts"
    legacy.mkdir(parents=True)
    (legacy / "a.png").write_bytes(b"x")
    d = paths.member_cache_dir(name, "charts")
    assert d == paths.member_dir(name) / "cache" / "charts"
    assert (d / "a.png").exists() and not legacy.exists()


def test_member_pdf_edits_dir_created(env):
    d = paths.member_pdf_edits_dir("Alex Lee")
    assert d.is_dir() and d.as_posix().endswith("data/Alex/pdf_edits")
