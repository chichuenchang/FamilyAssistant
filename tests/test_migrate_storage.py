# tests/test_migrate_storage.py — 单库 ledger.db → 分库布局迁移。
import json
import sqlite3
from pathlib import Path

import pytest

import migrate_storage
import paths
import members
import cal_db
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
    # schedule: remote event, local task (Alex), Robin event, legacy 爸爸 event — all synced
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
    """受控 members.json（Alex/Sam/Robin + Alex 同步）+ DATA_ROOT=tmp。"""
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
    state = tmp_path / ".calendar_state.json"
    state.write_text(json.dumps({"last_refresh": "2026-06-12T09:00:00",
                                 "last_error": None}), encoding="utf-8")
    return tmp_path, old, state


def test_detect_calendar_owner(setup):
    assert migrate_storage._detect_calendar_owner() == "Alex Lee"


def test_notes_go_to_member_store(setup):
    tmp, old, state = setup
    migrate_storage.migrate(old, state)
    notes = note_db.list_notes(member="Alex Lee",
                               db_path=str(paths.member_store("Alex Lee", "notes")))
    assert [n["content"] for n in notes] == ["wifi pw 1234"]


def test_events_and_tasks_split_to_owner(setup):
    tmp, old, state = setup
    migrate_storage.migrate(old, state)
    sdb = str(paths.member_store("Alex Lee", "schedule"))
    tdb = str(paths.member_store("Alex Lee", "tasks"))
    events = cal_db.list_upcoming(days=3650, today=__import__("datetime").date(2026, 6, 1),
                                  include_closed=True, db_path=sdb)
    tasks = cal_db.list_upcoming(days=3650, today=__import__("datetime").date(2026, 6, 1),
                                 include_closed=True, db_path=tdb)
    assert {e["uid"] for e in events} == {"ev-remote", "ev-robin", "ev-legacy"}
    assert {t["uid"] for t in tasks} == {"t-alex"}


def test_uid_and_synced_preserved(setup):
    tmp, old, state = setup
    migrate_storage.migrate(old, state)
    sdb = str(paths.member_store("Alex Lee", "schedule"))
    rows = cal_db.list_upcoming(days=3650, today=__import__("datetime").date(2026, 6, 1),
                                include_closed=True, db_path=sdb)
    for r in rows:
        assert r["synced"] == 1 and r["uid"]            # remote mapping intact


def test_legacy_member_relabeled_registered_kept(setup):
    tmp, old, state = setup
    migrate_storage.migrate(old, state)
    sdb = str(paths.member_store("Alex Lee", "schedule"))
    rows = {r["uid"]: r for r in cal_db.list_upcoming(
        days=3650, today=__import__("datetime").date(2026, 6, 1),
        include_closed=True, db_path=sdb)}
    assert rows["ev-legacy"]["member"] == "Alex Lee"   # 爸爸 (unregistered) -> owner
    assert rows["ev-robin"]["member"] == "Robin"      # registered label kept


def test_family_ledger_created_empty(setup):
    tmp, old, state = setup
    rep = migrate_storage.migrate(old, state)
    fam = str(paths.family_ledger())
    conn = sqlite3.connect(fam)
    tabs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"transactions", "documents", "exchange_rates"} <= tabs
    assert rep["family"]["transactions"] == 0


def test_sync_state_seeded(setup):
    tmp, old, state = setup
    migrate_storage.migrate(old, state)
    for domain in ("schedule", "tasks"):
        sp = paths.member_sync_state("Alex Lee", domain)
        st = json.loads(sp.read_text(encoding="utf-8"))
        assert st["last_refresh"] == "2026-06-12T09:00:00"


def test_snapshot_and_rename(setup):
    tmp, old, state = setup
    rep = migrate_storage.migrate(old, state)
    assert not old.exists()                              # 原库改名
    assert (tmp / "ledger.db.premigration.bak").exists()
    assert rep["snapshot"]


def test_idempotent_rerun_on_bak(setup):
    tmp, old, state = setup
    migrate_storage.migrate(old, state)
    bak = tmp / "ledger.db.premigration.bak"
    # 二次对 .bak 跑：不应重复插入
    migrate_storage.migrate(bak, state)
    sdb = str(paths.member_store("Alex Lee", "schedule"))
    rows = cal_db.list_upcoming(days=3650, today=__import__("datetime").date(2026, 6, 1),
                                include_closed=True, db_path=sdb)
    assert len(rows) == 3                                # 仍 3 个事件，无重复
