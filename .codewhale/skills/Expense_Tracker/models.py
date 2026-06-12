"""
Family Assistant — Expense Tracker 数据模型定义

SQLite 数据库表结构，用于本地个人/家庭记账。
支持多币种、定期存款、多国报税记录。

分类 / 币种 / 基准币种的值 **全部来自项目根 config.json**（单一事实来源）。
本模块导入时读取一次 config.json 并暴露为常量；改这些值只改 config.json，
无需动代码（改后重启进程生效）。config.json 缺失/损坏时用下方应急回退值。
"""

import json
from pathlib import Path

# 交易类型（结构性，固定，不放 config.json）
TRANSACTION_TYPES = ("expense", "income", "investment", "savings")

# 报税国家（结构性，固定）
TAX_COUNTRIES = ("US", "CA")

# ---------- config.json 读取（单一事实来源） ----------

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"


def _load_config() -> dict:
    """读取项目根 config.json；缺失或损坏时返回空 dict（回退到下方应急默认）。"""
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_cfg = _load_config()

# 应急回退（仅 config.json 缺失/损坏时使用；正常运行值来自 config.json）
_FALLBACK_CATEGORIES = {t: ["其他"] for t in TRANSACTION_TYPES}
_FALLBACK_CURRENCIES = ("USD", "CNY", "CAD")
_FALLBACK_BASE = "USD"

CATEGORIES = _cfg.get("categories") or _FALLBACK_CATEGORIES
SUPPORTED_CURRENCIES = tuple(_cfg.get("supported_currencies") or _FALLBACK_CURRENCIES)
BASE_CURRENCY = _cfg.get("base_currency") or _FALLBACK_BASE

# 数据库路径：config.json db_path（相对项目根）；缺失回退 data/ledger.db
_ROOT = _CONFIG_PATH.parent
DB_PATH = _ROOT / (_cfg.get("db_path") or "data/ledger.db")

# 票据目录：config.json receipts_dir（相对项目根）；缺失回退 receipts
RECEIPTS_DIR = _ROOT / (_cfg.get("receipts_dir") or "receipts")

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
    member          TEXT    NOT NULL DEFAULT '',
    notes           TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS deposits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    amount          REAL    NOT NULL,
    currency        TEXT    NOT NULL,
    bank            TEXT    DEFAULT '',
    account         TEXT    DEFAULT '',
    term_months     INTEGER DEFAULT 0,
    rate            REAL    DEFAULT 0.0,
    start_date      TEXT    NOT NULL,
    maturity_date   TEXT    DEFAULT NULL,
    receipt_path    TEXT    DEFAULT NULL,
    member          TEXT    NOT NULL DEFAULT '',
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
    member          TEXT    NOT NULL DEFAULT '',
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

-- 资金划转/换汇溯源：源账户 → 换汇 → 目标账户。纯记录，不改余额。
CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_desc       TEXT    DEFAULT '',
    from_type       TEXT    DEFAULT '',
    from_deposit_id INTEGER DEFAULT NULL,
    from_amount     REAL    NOT NULL,
    from_currency   TEXT    NOT NULL,
    to_amount       REAL    NOT NULL,
    to_currency     TEXT    NOT NULL,
    rate            REAL    DEFAULT 0.0,
    exchange_date   TEXT    DEFAULT '',
    to_bank         TEXT    DEFAULT '',
    to_account      TEXT    DEFAULT '',
    to_type         TEXT    DEFAULT '',
    transfer_date   TEXT    DEFAULT '',
    to_deposit_id   INTEGER DEFAULT NULL,
    member          TEXT    NOT NULL DEFAULT '',
    notes           TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_txn_type    ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_txn_date    ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_cat     ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_dep_date    ON deposits(start_date);
CREATE INDEX IF NOT EXISTS idx_dep_maturity ON deposits(maturity_date);
CREATE INDEX IF NOT EXISTS idx_tax_year    ON tax_filings(year);
CREATE INDEX IF NOT EXISTS idx_tax_country ON tax_filings(country);
CREATE INDEX IF NOT EXISTS idx_fx_date     ON exchange_rates(date);
CREATE INDEX IF NOT EXISTS idx_xfer_todep   ON transfers(to_deposit_id);
CREATE INDEX IF NOT EXISTS idx_xfer_fromdep ON transfers(from_deposit_id);
CREATE INDEX IF NOT EXISTS idx_xfer_date    ON transfers(transfer_date);
CREATE INDEX IF NOT EXISTS idx_xfer_tocur   ON transfers(to_currency);
"""
