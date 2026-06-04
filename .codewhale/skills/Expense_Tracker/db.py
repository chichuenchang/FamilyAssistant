"""
Family Assistant — Expense Tracker 数据库操作层

提供 SQLite CRUD 和查询接口。Agent / CLI / 脚本统一走这个模块。
"""

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 models
# 分类/币种/基准的值全部来自 config.json，由 models 统一读取（单一事实来源）。
from models import (
    SCHEMA, TRANSACTION_TYPES, BASE_CURRENCY, SUPPORTED_CURRENCIES, CATEGORIES, DB_PATH,
)
# DB_PATH 来自 config.json db_path（经 models）。


# ── 合法值访问器（薄封装 models 常量，供 cli / 校验复用） ──────

def get_categories(type_: str) -> list[str]:
    """某交易类型的合法分类（来自 config.json，经 models）。"""
    return list(CATEGORIES.get(type_, []))


def get_supported_currencies() -> tuple[str, ...]:
    """合法币种（来自 config.json，经 models）。"""
    return tuple(SUPPORTED_CURRENCIES)


def get_base_currency() -> str:
    """基准币种（来自 config.json，经 models）。"""
    return BASE_CURRENCY


def get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，自动启用 WAL 和 foreign keys。"""
    path = db_path or str(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """已有表缺列时补加（迁移）。幂等。"""
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db(db_path: Optional[str] = None) -> None:
    """初始化数据库：建表 + 索引 + 迁移。幂等。"""
    conn = get_db(db_path)
    conn.executescript(SCHEMA)
    # 迁移：为既有库补加新列
    _ensure_column(conn, "deposits", "account", "TEXT DEFAULT ''")
    conn.commit()
    conn.close()


# ---------- Transactions ----------

def find_duplicates(
    type_: str,
    amount: float,
    currency: str,
    date_: str,
    description: str = "",
    category: str = "",
    db_path: Optional[str] = None,
) -> list[dict]:
    """查找疑似重复交易：同日 + 同金额 + 同币种 + 描述相近。"""
    conn = get_db(db_path)
    desc_clean = description.strip().lower()
    rows = conn.execute(
        """SELECT * FROM transactions
           WHERE type = ? AND date = ? AND amount = ? AND currency = ?
           ORDER BY id""",
        (type_, date_, amount, currency),
    ).fetchall()
    conn.close()
    dupes = []
    for r in rows:
        existing = dict(r)
        existing_desc = (existing["description"] or "").strip().lower()
        # 如果描述存在且相近，或描述都为空，视为疑似重复
        if not desc_clean or not existing_desc or desc_clean == existing_desc:
            dupes.append(existing)
        elif desc_clean in existing_desc or existing_desc in desc_clean:
            dupes.append(existing)
    return dupes


def add_transaction(
    type_: str,
    amount: float,
    currency: str,
    date_: str,
    category: str = "",
    description: str = "",
    receipt_path: str = "",
    notes: str = "",
    skip_dup_check: bool = False,
    db_path: Optional[str] = None,
) -> tuple[int, list[dict]]:
    """添加一条流水，返回 (新记录 id, 疑似重复列表)。

    默认检查重复：同日 + 同金额 + 同币种 + 描述相近即报警。
    设 skip_dup_check=True 跳过检查直接写入。
    """
    assert type_ in TRANSACTION_TYPES, f"Invalid type: {type_}"
    assert amount > 0, "Amount must be positive"
    allowed_cur = get_supported_currencies()
    if currency not in allowed_cur:
        raise ValueError(f"不支持的币种 '{currency}'。可选: {', '.join(allowed_cur)}")
    if category:
        allowed_cat = get_categories(type_)
        if category not in allowed_cat:
            raise ValueError(
                f"无效分类 '{category}'（类型 {type_}）。可选: {', '.join(allowed_cat)}")

    dupes = []
    if not skip_dup_check:
        dupes = find_duplicates(type_, amount, currency, date_, description, category, db_path)
    if dupes:
        return 0, dupes

    conn = get_db(db_path)
    cur = conn.execute(
        """INSERT INTO transactions (type, amount, currency, category, description, date, receipt_path, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (type_, amount, currency, category, description, date_, receipt_path, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id, dupes


def get_transactions(
    type_: Optional[str] = None,
    category: Optional[str] = None,
    currency: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 200,
    db_path: Optional[str] = None,
) -> list[dict]:
    """查询交易，支持多条件筛选。"""
    conn = get_db(db_path)
    sql = "SELECT * FROM transactions WHERE 1=1"
    params: list[Any] = []

    if type_:
        sql += " AND type = ?"
        params.append(type_)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if currency:
        sql += " AND currency = ?"
        params.append(currency)
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)

    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_transaction(txn_id: int, db_path: Optional[str] = None) -> bool:
    """删除一条交易。"""
    conn = get_db(db_path)
    cur = conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def summarize_by_category(
    type_: str = "expense",
    year: Optional[int] = None,
    month: Optional[int] = None,
    db_path: Optional[str] = None,
) -> dict[str, dict[str, float]]:
    """按币种 + 分类汇总金额。不跨币种相加。

    返回 {currency: {category: total}}。
    """
    conn = get_db(db_path)
    sql = "SELECT currency, category, SUM(amount) AS total FROM transactions WHERE type = ?"
    params: list[Any] = [type_]

    if year:
        sql += " AND strftime('%Y', date) = ?"
        params.append(str(year))
    if month:
        sql += " AND strftime('%m', date) = ?"
        params.append(f"{month:02d}")

    sql += " GROUP BY currency, category ORDER BY currency, total DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["currency"], {})[r["category"] or "未分类"] = round(r["total"], 2)
    return out


def monthly_summary(
    type_: str = "expense",
    year: Optional[int] = None,
    db_path: Optional[str] = None,
) -> dict[str, dict[str, float]]:
    """按币种 + 月份汇总金额。不跨币种相加。

    返回 {currency: {month: total}}。
    """
    conn = get_db(db_path)
    sql = "SELECT currency, strftime('%Y-%m', date) AS mon, SUM(amount) AS total FROM transactions WHERE type = ?"
    params: list[Any] = [type_]

    if year:
        sql += " AND strftime('%Y', date) = ?"
        params.append(str(year))

    sql += " GROUP BY currency, mon ORDER BY currency, mon"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["currency"], {})[r["mon"]] = round(r["total"], 2)
    return out


# ---------- Deposits ----------

def add_deposit(
    amount: float,
    currency: str,
    start_date: str,
    bank: str = "",
    account: str = "",
    term_months: int = 0,
    rate: float = 0.0,
    maturity_date: str = "",
    receipt_path: str = "",
    notes: str = "",
    db_path: Optional[str] = None,
) -> int:
    """添加一笔定期存款记录。"""
    allowed_cur = get_supported_currencies()
    if currency not in allowed_cur:
        raise ValueError(f"不支持的币种 '{currency}'。可选: {', '.join(allowed_cur)}")
    conn = get_db(db_path)
    cur = conn.execute(
        """INSERT INTO deposits (amount, currency, bank, account, term_months, rate, start_date, maturity_date, receipt_path, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (amount, currency, bank, account, term_months, rate, start_date, maturity_date or "", receipt_path, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_deposits(
    currency: Optional[str] = None,
    active_only: bool = False,
    db_path: Optional[str] = None,
) -> list[dict]:
    """查询定期存款。active_only=True 时只返回未到期的。"""
    conn = get_db(db_path)
    sql = "SELECT * FROM deposits WHERE 1=1"
    params: list[Any] = []

    if currency:
        sql += " AND currency = ?"
        params.append(currency)
    if active_only:
        sql += " AND (maturity_date = '' OR maturity_date >= date('now'))"

    sql += " ORDER BY start_date DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Transfers（资金划转/换汇溯源） ----------

def add_transfer(
    from_amount: float,
    from_currency: str,
    to_amount: float,
    to_currency: str,
    from_desc: str = "",
    from_type: str = "",
    from_deposit_id: Optional[int] = None,
    rate: Optional[float] = None,
    exchange_date: str = "",
    to_bank: str = "",
    to_account: str = "",
    to_type: str = "",
    transfer_date: str = "",
    to_term: int = 0,
    to_rate: float = 0.0,
    to_maturity: str = "",
    notes: str = "",
    db_path: Optional[str] = None,
) -> dict:
    """记录一笔资金划转/换汇。目标为定期时自动建 deposits 行并链接。

    返回 {"transfer_id": int, "to_deposit_id": int|None}。
    """
    allowed_cur = get_supported_currencies()
    for c in (from_currency, to_currency):
        if c not in allowed_cur:
            raise ValueError(f"不支持的币种 '{c}'。可选: {', '.join(allowed_cur)}")
    if rate is None:
        rate = round(to_amount / from_amount, 6) if from_amount else 0.0

    # 目标为定期 → 自动建定期存款记录（复用 add_deposit），再链接
    to_deposit_id: Optional[int] = None
    if "定期" in (to_type or ""):
        to_deposit_id = add_deposit(
            amount=to_amount,
            currency=to_currency,
            bank=to_bank,
            account=to_account,
            term_months=to_term,
            rate=to_rate,
            start_date=transfer_date or exchange_date,
            maturity_date=to_maturity,
            notes="来自划转" + (f"：{notes}" if notes else ""),
            db_path=db_path,
        )

    conn = get_db(db_path)
    cur = conn.execute(
        """INSERT INTO transfers
           (from_desc, from_type, from_deposit_id, from_amount, from_currency,
            to_amount, to_currency, rate, exchange_date, to_bank, to_account,
            to_type, transfer_date, to_deposit_id, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (from_desc, from_type, from_deposit_id, from_amount, from_currency,
         to_amount, to_currency, rate, exchange_date, to_bank, to_account,
         to_type, transfer_date, to_deposit_id, notes),
    )
    conn.commit()
    transfer_id = cur.lastrowid
    conn.close()
    return {"transfer_id": transfer_id, "to_deposit_id": to_deposit_id}


def get_transfers(
    currency: Optional[str] = None,
    to_bank: Optional[str] = None,
    type_: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    to_deposit_id: Optional[int] = None,
    from_deposit_id: Optional[int] = None,
    trace: Optional[str] = None,
    limit: int = 200,
    db_path: Optional[str] = None,
) -> list[dict]:
    """查询划转记录。currency/type_ 匹配源或目标；trace 模糊匹配描述/银行/账号/备注。"""
    conn = get_db(db_path)
    sql = "SELECT * FROM transfers WHERE 1=1"
    params: list[Any] = []

    if currency:
        sql += " AND (from_currency = ? OR to_currency = ?)"
        params += [currency, currency]
    if to_bank:
        sql += " AND to_bank = ?"
        params.append(to_bank)
    if type_:
        sql += " AND (from_type = ? OR to_type = ?)"
        params += [type_, type_]
    if start:
        sql += " AND transfer_date >= ?"
        params.append(start)
    if end:
        sql += " AND transfer_date <= ?"
        params.append(end)
    if to_deposit_id is not None:
        sql += " AND to_deposit_id = ?"
        params.append(to_deposit_id)
    if from_deposit_id is not None:
        sql += " AND from_deposit_id = ?"
        params.append(from_deposit_id)
    if trace:
        kw = f"%{trace}%"
        sql += " AND (from_desc LIKE ? OR to_bank LIKE ? OR to_account LIKE ? OR notes LIKE ?)"
        params += [kw, kw, kw, kw]

    sql += " ORDER BY transfer_date DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Tax Filings ----------

def add_tax_filing(
    year: int,
    country: str,
    data: dict,
    filing_date: str = "",
    receipt_path: str = "",
    notes: str = "",
    db_path: Optional[str] = None,
) -> int:
    """添加一条报税记录。data 为灵活 JSON。"""
    conn = get_db(db_path)
    cur = conn.execute(
        """INSERT INTO tax_filings (year, country, filing_date, data, receipt_path, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (year, country, filing_date, json.dumps(data, ensure_ascii=False), receipt_path, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_tax_filings(
    year: Optional[int] = None,
    country: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict]:
    """查询报税记录。data 字段自动解析为 dict。"""
    conn = get_db(db_path)
    sql = "SELECT * FROM tax_filings WHERE 1=1"
    params: list[Any] = []

    if year:
        sql += " AND year = ?"
        params.append(year)
    if country:
        sql += " AND country = ?"
        params.append(country)

    sql += " ORDER BY year DESC, country"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    for r in result:
        r["data"] = json.loads(r["data"])
    return result


# ---------- Exchange Rates ----------

def set_exchange_rate(
    from_currency: str,
    to_currency: str,
    rate: float,
    source: str = "manual",
    db_path: Optional[str] = None,
) -> None:
    """写入或更新当日汇率。"""
    today = date.today().isoformat()
    conn = get_db(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO exchange_rates (from_currency, to_currency, rate, date, source)
           VALUES (?, ?, ?, ?, ?)""",
        (from_currency, to_currency, rate, today, source),
    )
    conn.commit()
    conn.close()


def get_latest_rate(
    from_currency: str,
    to_currency: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[float]:
    """获取最近一条汇率。to_currency 默认基准币种（config.json）。"""
    if to_currency is None:
        to_currency = get_base_currency()
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT rate FROM exchange_rates WHERE from_currency = ? AND to_currency = ? ORDER BY date DESC LIMIT 1",
        (from_currency, to_currency),
    ).fetchone()
    conn.close()
    return row["rate"] if row else None


def convert_to_base(
    amount: float,
    from_currency: str,
    db_path: Optional[str] = None,
) -> float:
    """将金额转换为基准货币（config.json base_currency）。无汇率时返回特征值 -1。"""
    base = get_base_currency()
    if from_currency == base:
        return amount
    rate = get_latest_rate(from_currency, base, db_path)
    if rate is None:
        return -1.0
    return round(amount * rate, 2)
