"""只读 SQL（query_ledger）：模型自由查询，库绝不被改。"""
import sqlite3

import pytest

import db as dbm
import tool_runtime as rt


@pytest.fixture
def ledger(db):
    for i, (amt, cat) in enumerate([(10, "餐饮"), (20, "餐饮"), (5, "购物")], 1):
        dbm.add_transaction(type_="expense", amount=amt, currency="USD",
                            date_=f"2026-05-0{i}", category=cat, member="爸爸", db_path=db)
    return db


def test_select_returns_header_and_rows(ledger):
    out = rt.ro_query(ledger, "SELECT category, SUM(amount) AS total FROM transactions "
                              "GROUP BY category ORDER BY total DESC")
    lines = out.splitlines()
    assert lines[0] == "category | total"
    assert lines[1] == "餐饮 | 30.0" and lines[2] == "购物 | 5.0"


def test_cte_allowed(ledger):
    out = rt.ro_query(ledger, "WITH m AS (SELECT amount FROM transactions) SELECT COUNT(*) FROM m")
    assert out.splitlines()[1] == "3"


@pytest.mark.parametrize("sql", [
    "DELETE FROM transactions",
    "UPDATE transactions SET amount = 0",
    "INSERT INTO transactions (type, amount, date) VALUES ('expense', 1, '2026-01-01')",
    "DROP TABLE transactions",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA journal_mode=DELETE",
    "SELECT 1; DELETE FROM transactions",
])
def test_writes_rejected(ledger, sql):
    out = rt.ro_query(ledger, sql)
    assert out.startswith("[错误]")
    n = sqlite3.connect(ledger).execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert n == 3


def test_row_cap_says_more(ledger):
    out = rt.ro_query(ledger, "SELECT id FROM transactions", max_rows=2)
    assert out.splitlines()[1:3] == ["1", "2"]
    assert "只显示前 2 行" in out


def test_missing_db(tmp_path):
    assert rt.ro_query(str(tmp_path / "nope.db"), "SELECT 1").startswith("[错误]")


def test_empty_result(ledger):
    assert "0 行" in rt.ro_query(ledger, "SELECT * FROM transactions WHERE amount > 999")


def test_query_ledger_tool_reads_family_ledger(ledger, monkeypatch):
    import models
    import skill_registry
    monkeypatch.setattr(models, "DB_PATH", ledger)
    tool = skill_registry.load().tool_map["query_ledger"]
    assert tool({"sql": "SELECT COUNT(*) AS n FROM transactions"}) == "n\n3"
