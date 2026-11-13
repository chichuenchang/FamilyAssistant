# tests/test_migrate_storage.py — 单库 ledger.db → 分库布局迁移。
import json
import sqlite3
from pathlib import Path

import pytest

import migrate_storage
import paths
import members
import note_db


_OLD_SCHEMA = """
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT NOT NULL, content TEXT NOT NULL,
    source_image TEXT DEFAULT '', pinned INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE schedule_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, uid TEXT DEFAULT '',
    title TEXT NOT NULL, start_at TEXT DEFAULT '', end_at TEXT DEFAULT '',
    all_day INTEGER DEFAULT 0, location TEXT DEFAULT '', notes TEXT DEFAULT '',
    member TEXT DEFAULT '', status TEXT DEFAULT 'active', origin TEXT DEFAULT 'local',
    synced INTEGER DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY', category TEXT DEFAULT '', description TEXT DEFAULT '',
    date TEXT NOT NULL, receipt_path TEXT DEFAULT NULL, member TEXT NOT NULL DEFAULT '',
    notes TEXT DEFAULT '', created_at TEXT DEFAULT '');
"""


def _make_old_ledger(p: Path):
    conn = sqlite3.connect(str(p))
    conn.executescript(_OLD_SCHEMA)
    conn.execute("INSERT INTO notes (member,content,source_image,pinned,created_at) "
                 "VALUES (?,?,?,?,?)",
                 ("Alex Lee", "wifi pw 1234", "", 0, "2026-06-01T10:00:00"))
    # 旧日程行：远程日历直连后不再迁移
    sched = [
        ("event", "ev-remote", "RemoteEvent", "2026-06-20T10:00", "", 0, "", "",
         "", "active", "remote", 1),
        ("task", "t-alex", "AlexTask", "2026-06-21", "", 0, "", "",
         "Alex Lee", "active", "local", 1),
        ("event", "ev-robin", "RobinMeet", "2026-06-22T09:00", "", 0, "", "",
         "Robin", "active", "local", 1),
        ("event", "ev-legacy", "LegacyEvent", "2026-06-23T09:00", "", 0, "", "",
         "爸爸", "active", "local", 1),
    ]
    for s in sched:
        conn.execute(
            "INSERT INTO schedule_items (kind,uid,title,start_at,end_at,all_day,location,"
            "notes,member,status,origin,synced,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*s, "2026-06-01T00:00:00",
                                                     "2026-06-01T00:00:00"))
    conn.commit()
    conn.close()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """受控 members.json（Alex/Sam/Robin）+ DATA_ROOT=tmp。"""
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({
        "Alex Lee": {"dir": "Alex",
                      "sync": {"schedule": {"provider": "google_calendar", "enabled": True},
                               "tasks": {"provider": "google_tasks", "enabled": True}}},
        "Sam Lee": {"dir": "Sam"},
        "Robin": {"dir": "Robin"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(members, "MEMBERS_PATH", mp)
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    old = tmp_path / "ledger.db"
    _make_old_ledger(old)
    return tmp_path, old


def test_notes_go_to_member_store(setup):
    tmp, old = setup
    migrate_storage.migrate(old)
    notes = note_db.list_notes(member="Alex Lee",
                               db_path=str(paths.member_store("Alex Lee", "notes")))
    assert [n["content"] for n in notes] == ["wifi pw 1234"]


def test_family_ledger_created_empty(setup):
    tmp, old = setup
    rep = migrate_storage.migrate(old)
    fam = str(paths.family_ledger())
    conn = sqlite3.connect(fam)
    tabs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"transactions", "documents", "exchange_rates"} <= tabs
    assert rep["family"]["transactions"] == 0


def test_snapshot_and_rename(setup):
    tmp, old = setup
    rep = migrate_storage.migrate(old)
    assert not old.exists()                              # 原库改名
    assert (tmp / "ledger.db.premigration.bak").exists()
    assert rep["snapshot"]


def test_idempotent_rerun_on_bak(setup):
    tmp, old = setup
    migrate_storage.migrate(old)
    bak = tmp / "ledger.db.premigration.bak"
    # 二次对 .bak 跑：不应重复插入
    migrate_storage.migrate(bak)
    notes = note_db.list_notes("Alex Lee", db_path=str(paths.member_store("Alex Lee", "notes")))
    assert len(notes) == 1                               # 无重复


def test_schedule_items_ignored(setup):
    tmp, old = setup
    rep = migrate_storage.migrate(old)
    assert not (tmp / "data" / "Alex" / "schedule").exists()
    assert not (tmp / "data" / "Alex" / "tasks").exists()
    assert "events" not in rep and "calendar_owner" not in rep
