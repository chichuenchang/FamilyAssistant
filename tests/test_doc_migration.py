# tests/test_doc_migration.py — documents 表从 ledger.db 搬家到 documents.db。
import sqlite3

import pytest

import doc_db
import doc_models
import paths


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    (tmp_path / "data" / "Family").mkdir(parents=True)
    return tmp_path / "data"


def _make_legacy_ledger(data_root, rows=2):
    ledger = data_root / "Family" / "ledger.db"
    conn = sqlite3.connect(ledger)
    conn.executescript(doc_models.SCHEMA)  # 造出旧世界：documents 表在账本里
    for i in range(rows):
        conn.execute(
            "INSERT INTO documents (doc_type, title) VALUES (?, ?)",
            ("lease", f"旧文档{i}"))
    conn.commit()
    conn.close()
    return ledger


def test_migrates_rows_and_renames_legacy(env):
    _make_legacy_ledger(env, rows=2)
    conn = doc_db.get_db()          # 默认路径 → 触发迁移
    titles = [r["title"] for r in conn.execute(
        "SELECT title FROM documents ORDER BY id")]
    conn.close()
    assert titles == ["旧文档0", "旧文档1"]
    lg = sqlite3.connect(env / "Family" / "ledger.db")
    names = {r[0] for r in lg.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    lg.close()
    assert "documents_legacy" in names and "documents" not in names


def test_migration_idempotent(env):
    _make_legacy_ledger(env, rows=1)
    doc_db.get_db().close()
    doc_db.get_db().close()          # 第二次连接不得重复搬运
    conn = doc_db.get_db()
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    conn.close()
    assert n == 1


def test_fresh_install_no_ledger_table(env):
    # 账本存在但没有 documents 表（纯财务新世界）
    lg = sqlite3.connect(env / "Family" / "ledger.db")
    lg.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY)")
    lg.commit()
    lg.close()
    conn = doc_db.get_db()
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    conn.close()
    assert n == 0


def test_no_ledger_file_at_all(env):
    conn = doc_db.get_db()
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    conn.close()


def test_explicit_db_path_skips_migration(env, tmp_path):
    _make_legacy_ledger(env, rows=3)
    p = str(tmp_path / "elsewhere.db")
    conn = doc_db.get_db(p)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    conn.close()
    lg = sqlite3.connect(env / "Family" / "ledger.db")
    names = {r[0] for r in lg.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    lg.close()
    assert "documents" in names     # 原地未动
