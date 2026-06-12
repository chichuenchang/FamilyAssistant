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


def test_add_transaction_with_member(db):
    tid, _ = dbm.add_transaction("expense", 10.0, "USD", "2026-06-01",
                                 member="爸爸", db_path=db)
    rows = dbm.get_transactions(db_path=db)
    assert rows[0]["member"] == "爸爸"


def test_get_transactions_member_filter(db):
    dbm.add_transaction("expense", 10.0, "USD", "2026-06-01",
                        description="a", member="爸爸", db_path=db)
    dbm.add_transaction("expense", 20.0, "USD", "2026-06-02",
                        description="b", member="妈妈", db_path=db)
    rows = dbm.get_transactions(member="爸爸", db_path=db)
    assert len(rows) == 1 and rows[0]["member"] == "爸爸"
    # 不传 member 返回全部
    assert len(dbm.get_transactions(db_path=db)) == 2


def test_summary_member_filter(db):
    dbm.add_transaction("expense", 10.0, "USD", "2026-06-01",
                        category="其他", description="a", member="爸爸", db_path=db)
    dbm.add_transaction("expense", 20.0, "USD", "2026-06-02",
                        category="其他", description="b", member="妈妈", db_path=db)
    out = dbm.summarize_by_category("expense", member="爸爸", db_path=db)
    assert out == {"USD": {"其他": 10.0}}


def test_monthly_member_filter(db):
    dbm.add_transaction("expense", 10.0, "USD", "2026-06-01",
                        description="a", member="爸爸", db_path=db)
    dbm.add_transaction("expense", 20.0, "USD", "2026-06-02",
                        description="b", member="妈妈", db_path=db)
    out = dbm.monthly_summary("expense", member="妈妈", db_path=db)
    assert out == {"USD": {"2026-06": 20.0}}


def test_summarize_by_member(db):
    dbm.add_transaction("expense", 10.0, "USD", "2026-06-01",
                        description="a", member="爸爸", db_path=db)
    dbm.add_transaction("expense", 20.0, "USD", "2026-06-02",
                        description="b", member="爸爸", db_path=db)
    dbm.add_transaction("expense", 5.0, "USD", "2026-06-03",
                        description="c", db_path=db)  # 家庭级
    out = dbm.summarize_by_member("expense", db_path=db)
    assert out == {"USD": {"爸爸": 30.0, "家庭": 5.0}}


def test_deposit_tax_transfer_member(db):
    did = dbm.add_deposit(100.0, "USD", "2026-06-01", member="爸爸", db_path=db)
    assert dbm.get_deposits(db_path=db)[0]["member"] == "爸爸"

    dbm.add_tax_filing(2025, "US", {}, member="妈妈", db_path=db)
    assert dbm.get_tax_filings(db_path=db)[0]["member"] == "妈妈"

    res = dbm.add_transfer(100.0, "USD", 100.0, "USD", to_type="定期",
                           transfer_date="2026-06-01", member="爸爸", db_path=db)
    transfers = dbm.get_transfers(db_path=db)
    assert transfers[0]["member"] == "爸爸"
    # 自动创建的定期存款也归属同一成员
    auto_dep = [d for d in dbm.get_deposits(db_path=db) if d["id"] == res["to_deposit_id"]]
    assert auto_dep[0]["member"] == "爸爸"
