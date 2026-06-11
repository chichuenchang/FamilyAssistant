# tests/test_member_db.py — member 列：建表、迁移、写入与过滤。
import sqlite3

import db as dbm


def _cols(db_path, table):
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


def test_fresh_db_has_member_columns(db):
    for table in ("transactions", "deposits", "transfers", "tax_filings"):
        assert "member" in _cols(db, table), table


def test_legacy_db_migrates_idempotently(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    # 旧版 transactions 表（无 member 列）
    conn.execute("""CREATE TABLE transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL, amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'CNY',
        category TEXT DEFAULT '', description TEXT DEFAULT '',
        date TEXT NOT NULL, receipt_path TEXT DEFAULT NULL,
        notes TEXT DEFAULT '', created_at TEXT)""")
    conn.execute("""INSERT INTO transactions (type, amount, currency, date)
                    VALUES ('expense', 1.0, 'USD', '2026-01-01')""")
    conn.commit()
    conn.close()

    dbm.init_db(db_path=db_path)
    dbm.init_db(db_path=db_path)  # 第二次必须无副作用

    assert "member" in _cols(db_path, "transactions")
    rows = dbm.get_transactions(db_path=db_path)
    assert rows[0]["member"] == ""  # 旧数据 = 家庭级
