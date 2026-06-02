"""
Family Assistant — Expense Tracker 数据模型定义

SQLite 数据库表结构，用于本地个人/家庭记账。
支持多币种、定期存款、多国报税记录。
"""

# 交易类型
TRANSACTION_TYPES = ("expense", "income", "investment", "savings")

# 默认分类（可按需在 config.json 扩展）
DEFAULT_CATEGORIES = {
    "expense": ["餐饮", "交通", "购物", "住房", "医疗", "娱乐", "教育", "通讯", "日用", "其他"],
    "income": ["工资", "奖金", "投资收益", "副业", "礼金", "其他"],
    "investment": ["股票", "基金", "定期存款", "理财", "其他"],
    "savings": ["活期", "定期", "应急金", "其他"],
}

# 支持的货币
SUPPORTED_CURRENCIES = ("CNY", "USD", "CAD")

# 基准货币（所有汇总折算到此）
BASE_CURRENCY = "CNY"

# 报税国家
TAX_COUNTRIES = ("US", "CA")

# ---------- SQL DDL ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT    NOT NULL CHECK(type IN ('expense','income','investment','savings')),
    amount          REAL    NOT NULL,
    currency        TEXT    NOT NULL DEFAULT 'CNY',
    category        TEXT    DEFAULT '',
    description     TEXT    DEFAULT '',
    date            TEXT    NOT NULL,
    receipt_path    TEXT    DEFAULT NULL,
    notes           TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS deposits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    amount          REAL    NOT NULL,
    currency        TEXT    NOT NULL,
    bank            TEXT    DEFAULT '',
    term_months     INTEGER DEFAULT 0,
    rate            REAL    DEFAULT 0.0,
    start_date      TEXT    NOT NULL,
    maturity_date   TEXT    DEFAULT NULL,
    receipt_path    TEXT    DEFAULT NULL,
    notes           TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS tax_filings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            INTEGER NOT NULL,
    country         TEXT    NOT NULL,
    filing_date     TEXT    DEFAULT NULL,
    data            TEXT    NOT NULL DEFAULT '{}',
    receipt_path    TEXT    DEFAULT NULL,
    notes           TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_currency   TEXT    NOT NULL,
    to_currency     TEXT    NOT NULL,
    rate            REAL    NOT NULL,
    date            TEXT    NOT NULL,
    source          TEXT    DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_txn_type    ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_txn_date    ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_cat     ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_dep_date    ON deposits(start_date);
CREATE INDEX IF NOT EXISTS idx_dep_maturity ON deposits(maturity_date);
CREATE INDEX IF NOT EXISTS idx_tax_year    ON tax_filings(year);
CREATE INDEX IF NOT EXISTS idx_tax_country ON tax_filings(country);
CREATE INDEX IF NOT EXISTS idx_fx_date     ON exchange_rates(date);
"""
