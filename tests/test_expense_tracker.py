# tests/test_expense_tracker.py — pytest suite for the Expense_Tracker db.py layer.
#
# Every test receives the ``db`` fixture (a temp database path) and passes
# ``db_path=db`` to *every* db.py function so the real data/ledger.db is
# never touched.

import pytest

# The fixture is named ``db`` (the temp-db *path string*) so we alias the
# db.py *module* as ``dbm`` to avoid shadowing inside test functions.
import db as dbm


def test_default_db_is_family_ledger():
    """Default ledger lives under data/Family, receipts under data/Family/receipts."""
    import models
    assert models.DB_PATH.as_posix().endswith("data/Family/ledger.db")
    assert models.RECEIPTS_DIR.as_posix().endswith("data/Family/receipts")


def test_store_receipt_returns_family_rel(tmp_path, monkeypatch):
    """A stored receipt path is recorded relative to data_root (Family/receipts/...).

    Uses ``expense_cli`` (loaded by path below) — bare ``import cli`` is ambiguous
    across skills and resolves to whichever cli.py was imported first.
    """
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(expense_cli, "RECEIPTS_DIR", tmp_path / "data" / "Family" / "receipts")
    monkeypatch.setattr(expense_cli, "ROOT", tmp_path)
    src = tmp_path / "r.jpg"
    src.write_bytes(b"img")
    rel = expense_cli._store_receipt(str(src), "2026-06-19", "expense_lunch")
    assert rel.startswith("Family/receipts/2026-06/")
    assert rel.endswith(".jpg")


# ═══════════════════════════════════════════════════════════════════════
# 1. init_db is idempotent
# ═══════════════════════════════════════════════════════════════════════

def test_init_db_idempotent(db):
    """Calling init_db twice on the same database must not raise."""
    dbm.init_db(db_path=db)  # first call done by fixture; second here
    dbm.init_db(db_path=db)  # third call — truly idempotent


# ═══════════════════════════════════════════════════════════════════════
# 2. add_transaction happy path
# ═══════════════════════════════════════════════════════════════════════

def test_add_transaction_happy_path(db):
    """A valid transaction is inserted and returned by get_transactions."""
    txn_id, dupes = dbm.add_transaction(
        type_="expense",
        amount=42.50,
        currency="CNY",
        date_="2025-06-01",
        category="餐饮",
        description="午餐",
        notes="test",
        db_path=db,
    )
    assert txn_id > 0
    assert dupes == []

    rows = dbm.get_transactions(db_path=db)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == txn_id
    assert r["type"] == "expense"
    assert r["amount"] == 42.50
    assert r["currency"] == "CNY"
    assert r["date"] == "2025-06-01"
    assert r["category"] == "餐饮"
    assert r["description"] == "午餐"
    assert r["notes"] == "test"


# ═══════════════════════════════════════════════════════════════════════
# 3. add_transaction invalid type → AssertionError
# ═══════════════════════════════════════════════════════════════════════

def test_add_transaction_invalid_type(db):
    """An unknown transaction type raises AssertionError."""
    with pytest.raises(AssertionError):
        dbm.add_transaction(
            type_="nonexistent_type",
            amount=10,
            currency="CNY",
            date_="2025-06-01",
            db_path=db,
        )


# ═══════════════════════════════════════════════════════════════════════
# 4. add_transaction unsupported currency → ValueError
# ═══════════════════════════════════════════════════════════════════════

def test_add_transaction_unsupported_currency(db):
    """A currency not listed in config.json raises ValueError."""
    with pytest.raises(ValueError, match="不支持的币种"):
        dbm.add_transaction(
            type_="expense",
            amount=10,
            currency="EUR",
            date_="2025-06-01",
            db_path=db,
        )


# ═══════════════════════════════════════════════════════════════════════
# 5. add_transaction invalid category → ValueError
# ═══════════════════════════════════════════════════════════════════════

def test_add_transaction_invalid_category(db):
    """A category not in the type's category list raises ValueError."""
    with pytest.raises(ValueError, match="无效分类"):
        dbm.add_transaction(
            type_="expense",
            amount=10,
            currency="CNY",
            date_="2025-06-01",
            category="不存在的分类",
            db_path=db,
        )


# ═══════════════════════════════════════════════════════════════════════
# 6. Duplicate detection
# ═══════════════════════════════════════════════════════════════════════

def test_duplicate_detection(db):
    """Second identical tx returns (0, [dupe]) and is NOT written.
    With skip_dup_check=True the duplicate IS written."""
    common = dict(
        type_="expense", amount=12.00, currency="CNY",
        date_="2025-06-10", description="咖啡",
        db_path=db,
    )

    # First insert — succeeds
    txn_id, dupes = dbm.add_transaction(**common)
    assert txn_id > 0
    assert dupes == []

    # Second identical — detected as duplicate, NOT written
    txn_id2, dupes2 = dbm.add_transaction(**common)
    assert txn_id2 == 0
    assert len(dupes2) == 1
    assert dupes2[0]["description"] == "咖啡"

    rows = dbm.get_transactions(db_path=db)
    assert len(rows) == 1

    # With skip_dup_check=True — IS written
    common["skip_dup_check"] = True
    txn_id3, dupes3 = dbm.add_transaction(**common)
    assert txn_id3 > 0
    assert dupes3 == []

    rows = dbm.get_transactions(db_path=db)
    assert len(rows) == 2


# ═══════════════════════════════════════════════════════════════════════
# 7. Duplicate detection — substring match
# ═══════════════════════════════════════════════════════════════════════

def test_duplicate_substring_match(db):
    """描述 "午餐" vs "午餐 麦当劳" 同日同金额同币种 → 视为疑似重复。"""
    common = dict(
        type_="expense", amount=35.00, currency="CNY",
        date_="2025-06-15", db_path=db,
    )

    txn_id, _ = dbm.add_transaction(description="午餐", **common)
    assert txn_id > 0

    txn_id2, dupes = dbm.add_transaction(description="午餐 麦当劳", **common)
    assert txn_id2 == 0
    assert len(dupes) == 1
    assert dupes[0]["description"] == "午餐"


def test_statement_distinct_rows_not_flagged(db):
    """账单逐行记账的基石：同日同额同币种但描述互不为子串的两行，均应写入、不误判重复。

    支撑银行/支付流水截图批量记账——例如同一天两笔 ¥20（不同商家/时间）须各记一笔；
    desc 带区分信息即可绕过 find_duplicates 的相似判定，而重复发同一张截图（desc 全等）仍会被拦。
    """
    common = dict(
        type_="expense", amount=20.00, currency="CNY",
        date_="2025-06-20", category="餐饮", db_path=db,
    )

    id1, d1 = dbm.add_transaction(description="星巴克 09:12", **common)
    id2, d2 = dbm.add_transaction(description="瑞幸 14:30", **common)
    assert id1 > 0 and id2 > 0          # 两笔都写入
    assert d1 == [] and d2 == []        # 都不算重复
    assert id1 != id2

    # 重复发同一张截图（同一行 desc 全等）→ 仍被重复检查拦下
    id3, d3 = dbm.add_transaction(description="星巴克 09:12", **common)
    assert id3 == 0 and len(d3) == 1

    assert len(dbm.get_transactions(db_path=db)) == 2


# ═══════════════════════════════════════════════════════════════════════
# 8. get_transactions filters
# ═══════════════════════════════════════════════════════════════════════

def test_get_transactions_filters(db):
    """Filter by type, date range, and limit."""
    # Seed data
    for i in range(5):
        dbm.add_transaction(
            type_="expense", amount=10 + i, currency="CNY",
            date_=f"2025-0{i+1}-01", category="餐饮",
            db_path=db, skip_dup_check=True,
        )
    for i in range(3):
        dbm.add_transaction(
            type_="income", amount=100 + i, currency="USD",
            date_=f"2025-0{i+1}-15", category="收入",
            db_path=db, skip_dup_check=True,
        )

    # Filter by type
    income_rows = dbm.get_transactions(type_="income", db_path=db)
    assert len(income_rows) == 3
    assert all(r["type"] == "income" for r in income_rows)

    # Filter by date range (inclusive)
    ranged = dbm.get_transactions(
        start_date="2025-02-01", end_date="2025-04-30", db_path=db,
    )
    # Months 02, 03, 04 → 3 expense + 2 income = 5
    assert len(ranged) == 5

    # Filter by limit
    limited = dbm.get_transactions(limit=3, db_path=db)
    assert len(limited) == 3


# ═══════════════════════════════════════════════════════════════════════
# 9. delete_transaction
# ═══════════════════════════════════════════════════════════════════════

def test_delete_transaction(db):
    """delete_transaction returns True for existing id, False for missing."""
    txn_id, _ = dbm.add_transaction(
        type_="expense", amount=5, currency="CNY",
        date_="2025-06-01", db_path=db,
    )
    assert txn_id > 0

    assert dbm.delete_transaction(txn_id, db_path=db) is True
    assert dbm.delete_transaction(txn_id, db_path=db) is False
    assert dbm.delete_transaction(99999, db_path=db) is False


# ═══════════════════════════════════════════════════════════════════════
# 10. summarize_by_category
# ═══════════════════════════════════════════════════════════════════════

def test_summarize_by_category(db):
    """Returns {currency: {category: total}}, does not mix currencies,
    rounds to 2 decimals."""
    # Two currencies, two categories each
    dbm.add_transaction(
        type_="expense", amount=10.50, currency="CNY",
        date_="2025-06-01", category="餐饮", db_path=db, skip_dup_check=True,
    )
    dbm.add_transaction(
        type_="expense", amount=20.00, currency="CNY",
        date_="2025-06-02", category="餐饮", db_path=db, skip_dup_check=True,
    )
    dbm.add_transaction(
        type_="expense", amount=7.25, currency="CNY",
        date_="2025-06-03", category="其他", db_path=db, skip_dup_check=True,
    )
    dbm.add_transaction(
        type_="expense", amount=100.00, currency="USD",
        date_="2025-06-01", category="购物", db_path=db, skip_dup_check=True,
    )

    summary = dbm.summarize_by_category(type_="expense", db_path=db)

    # Structure: dict of currency → dict of category → float
    assert isinstance(summary, dict)
    assert "CNY" in summary
    assert "USD" in summary
    assert summary["CNY"]["餐饮"] == 30.50
    assert summary["CNY"]["其他"] == 7.25
    assert summary["USD"]["购物"] == 100.00
    # Currencies are NOT mixed
    assert "购物" not in summary.get("CNY", {})
    assert "餐饮" not in summary.get("USD", {})


# ═══════════════════════════════════════════════════════════════════════
# 11. monthly_summary
# ═══════════════════════════════════════════════════════════════════════

def test_monthly_summary(db):
    """Returns {currency: {'YYYY-MM': total}} grouped correctly."""
    dbm.add_transaction(
        type_="expense", amount=30, currency="CNY",
        date_="2025-03-15", category="餐饮", db_path=db, skip_dup_check=True,
    )
    dbm.add_transaction(
        type_="expense", amount=70, currency="CNY",
        date_="2025-03-20", category="其他", db_path=db, skip_dup_check=True,
    )
    dbm.add_transaction(
        type_="expense", amount=200, currency="CNY",
        date_="2025-04-01", category="购物", db_path=db, skip_dup_check=True,
    )

    ms = dbm.monthly_summary(type_="expense", db_path=db)

    assert isinstance(ms, dict)
    assert "CNY" in ms
    assert ms["CNY"]["2025-03"] == 100.00
    assert ms["CNY"]["2025-04"] == 200.00


# ═══════════════════════════════════════════════════════════════════════
# 12. add_deposit + get_deposits roundtrip; active_only filter
# ═══════════════════════════════════════════════════════════════════════

def test_add_deposit_roundtrip_and_active_only(db):
    """get_deposits returns inserted deposit; active_only=True excludes
    past maturity_date but includes empty maturity_date."""
    from datetime import date, timedelta

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    next_year = (date.today() + timedelta(days=365)).isoformat()

    # Active deposit — maturity_date far in the future
    d1 = dbm.add_deposit(
        amount=10000, currency="CNY", start_date="2025-01-01",
        bank="TestBank", account="6222", term_months=12, rate=0.02,
        maturity_date=next_year, notes="active", db_path=db,
    )
    assert d1 > 0

    # Matured deposit — maturity_date in the past
    d2 = dbm.add_deposit(
        amount=5000, currency="CNY", start_date="2024-01-01",
        bank="OldBank", account="6333", term_months=6, rate=0.015,
        maturity_date=yesterday, notes="matured", db_path=db,
    )
    assert d2 > 0

    # Deposit with empty maturity_date (no maturity set → treated as active)
    d3 = dbm.add_deposit(
        amount=3000, currency="USD", start_date="2025-03-01",
        bank="OtherBank", term_months=0, rate=0.0,
        maturity_date="", notes="no maturity", db_path=db,
    )
    assert d3 > 0

    # Full list
    all_deps = dbm.get_deposits(db_path=db)
    assert len(all_deps) == 3

    # active_only=True: excludes d2 (past maturity), includes d1 + d3
    active = dbm.get_deposits(active_only=True, db_path=db)
    active_ids = {d["id"] for d in active}
    assert d1 in active_ids
    assert d2 not in active_ids
    assert d3 in active_ids

    # Verify fields of d1
    d1_row = next(d for d in all_deps if d["id"] == d1)
    assert d1_row["amount"] == 10000
    assert d1_row["currency"] == "CNY"
    assert d1_row["bank"] == "TestBank"
    assert d1_row["account"] == "6222"
    assert d1_row["notes"] == "active"


# ═══════════════════════════════════════════════════════════════════════
# 13. add_transfer — to_type NOT containing "定期"
# ═══════════════════════════════════════════════════════════════════════

def test_add_transfer_non_dingqi(db):
    """to_type="活期" → transfer_id > 0, to_deposit_id is None;
    no deposit row is created."""
    result = dbm.add_transfer(
        from_amount=5000, from_currency="CNY",
        to_amount=5000, to_currency="CNY",
        from_desc="工资卡", from_type="活期",
        to_bank="TestBank", to_account="acc1", to_type="活期",
        db_path=db,
    )
    assert result["transfer_id"] > 0
    assert result["to_deposit_id"] is None

    # Verify no deposit was auto-created
    deps = dbm.get_deposits(db_path=db)
    assert len(deps) == 0


# ═══════════════════════════════════════════════════════════════════════
# 14. add_transfer — to_type "定期" auto-creates deposit
# ═══════════════════════════════════════════════════════════════════════

def test_add_transfer_dingqi(db):
    """to_type="定期" → auto-creates a deposit; notes start with '来自划转'."""
    result = dbm.add_transfer(
        from_amount=100000, from_currency="CNY",
        to_amount=100000, to_currency="CNY",
        from_desc="工资卡", from_type="活期",
        to_bank="ProdBank", to_account="acc_dq", to_type="定期",
        to_term=12, to_rate=0.025, to_maturity="2026-12-31",
        transfer_date="2025-12-31", notes="年终存定期",
        db_path=db,
    )
    assert result["transfer_id"] > 0
    assert result["to_deposit_id"] is not None

    deps = dbm.get_deposits(db_path=db)
    assert len(deps) == 1
    dep = deps[0]
    assert dep["id"] == result["to_deposit_id"]
    assert dep["amount"] == 100000
    assert dep["currency"] == "CNY"
    assert dep["bank"] == "ProdBank"
    assert dep["account"] == "acc_dq"
    assert dep["notes"].startswith("来自划转")


# ═══════════════════════════════════════════════════════════════════════
# 15. add_transfer — rate auto-calculation
# ═══════════════════════════════════════════════════════════════════════

def test_add_transfer_rate_auto_calc(db):
    """Omit rate; from_amount=350000 CNY → to_amount=50000 USD.
    Stored rate equals round(50000/350000, 6)."""
    result = dbm.add_transfer(
        from_amount=350000, from_currency="CNY",
        to_amount=50000, to_currency="USD",
        # rate omitted
        db_path=db,
    )
    assert result["transfer_id"] > 0

    transfers = dbm.get_transfers(db_path=db)
    assert len(transfers) == 1
    expected_rate = round(50000 / 350000, 6)
    assert transfers[0]["rate"] == expected_rate


# ═══════════════════════════════════════════════════════════════════════
# 16. add_transfer unsupported currency → ValueError
# ═══════════════════════════════════════════════════════════════════════

def test_add_transfer_unsupported_currency(db):
    """An unsupported from_currency or to_currency raises ValueError."""
    with pytest.raises(ValueError, match="不支持的币种"):
        dbm.add_transfer(
            from_amount=100, from_currency="GBP",
            to_amount=100, to_currency="CNY",
            db_path=db,
        )

    with pytest.raises(ValueError, match="不支持的币种"):
        dbm.add_transfer(
            from_amount=100, from_currency="CNY",
            to_amount=100, to_currency="JPY",
            db_path=db,
        )


# ═══════════════════════════════════════════════════════════════════════
# 17. get_transfers filters
# ═══════════════════════════════════════════════════════════════════════

def test_get_transfers_filters(db):
    """Filter by to_deposit_id returns only the linked transfer;
    filter by trace keyword matches from_desc/to_bank/to_account/notes."""
    # Transfer 1: to 定期 → creates deposit A
    r1 = dbm.add_transfer(
        from_amount=50000, from_currency="CNY",
        to_amount=50000, to_currency="CNY",
        from_desc="工资转定期", to_bank="BankAlpha",
        to_account="1111", to_type="定期",
        notes="第一笔定期", db_path=db,
    )
    # Transfer 2: to 定期 → creates deposit B
    r2 = dbm.add_transfer(
        from_amount=30000, from_currency="CNY",
        to_amount=30000, to_currency="CNY",
        from_desc="奖金转定期", to_bank="BankBeta",
        to_account="2222", to_type="定期",
        notes="第二笔定期", db_path=db,
    )

    # Filter by to_deposit_id
    by_dep = dbm.get_transfers(to_deposit_id=r1["to_deposit_id"], db_path=db)
    assert len(by_dep) == 1
    assert by_dep[0]["id"] == r1["transfer_id"]

    # Filter by trace — matches from_desc / to_bank / to_account / notes
    by_trace = dbm.get_transfers(trace="BankBeta", db_path=db)
    assert len(by_trace) == 1
    assert by_trace[0]["id"] == r2["transfer_id"]

    # Trace matches notes
    by_trace2 = dbm.get_transfers(trace="第一笔", db_path=db)
    assert len(by_trace2) == 1
    assert by_trace2[0]["id"] == r1["transfer_id"]


# ═══════════════════════════════════════════════════════════════════════
# 18. add_tax_filing + get_tax_filings roundtrip
# ═══════════════════════════════════════════════════════════════════════

def test_add_tax_filing_roundtrip(db):
    """Data dict survives JSON roundtrip (returned as dict, not string);
    filter by year and country works."""
    payload = {"income": 85000, "tax_paid": 12000, "deductions": ["401k", "ira"]}

    fid = dbm.add_tax_filing(
        year=2024, country="US", data=payload,
        filing_date="2025-04-15", notes="test filing",
        db_path=db,
    )
    assert fid > 0

    # Another filing for coverage of filters
    fid2 = dbm.add_tax_filing(
        year=2023, country="CA", data={"income": 60000},
        db_path=db,
    )
    assert fid2 > 0

    # Retrieve all
    all_filings = dbm.get_tax_filings(db_path=db)
    assert len(all_filings) == 2

    # The data field must be a dict, not a JSON string
    f1 = next(f for f in all_filings if f["id"] == fid)
    assert isinstance(f1["data"], dict)
    assert f1["data"]["income"] == 85000
    assert f1["data"]["deductions"] == ["401k", "ira"]

    # Filter by year
    y2024 = dbm.get_tax_filings(year=2024, db_path=db)
    assert len(y2024) == 1
    assert y2024[0]["year"] == 2024

    # Filter by country
    ca = dbm.get_tax_filings(country="CA", db_path=db)
    assert len(ca) == 1
    assert ca[0]["country"] == "CA"


# ═══════════════════════════════════════════════════════════════════════
# 19. set_exchange_rate + get_latest_rate roundtrip
# ═══════════════════════════════════════════════════════════════════════

def test_exchange_rate_roundtrip(db):
    """set_exchange_rate stores; get_latest_rate retrieves it.
    get_latest_rate returns None for unknown pair."""
    dbm.set_exchange_rate("CNY", "USD", 0.14, db_path=db)

    rate = dbm.get_latest_rate("CNY", "USD", db_path=db)
    assert rate == 0.14

    # Default to_currency = base (USD per config)
    rate_default = dbm.get_latest_rate("CNY", db_path=db)
    assert rate_default == 0.14

    # Unknown pair
    assert dbm.get_latest_rate("CAD", "CNY", db_path=db) is None


# ═══════════════════════════════════════════════════════════════════════
# 20. convert_to_base
# ═══════════════════════════════════════════════════════════════════════

def test_convert_to_base(db):
    """Same currency: amount unchanged. With rate: round(amount*rate, 2).
    Missing rate: None."""
    # USD is the base currency (config.json)
    assert dbm.convert_to_base(100.0, "USD", db_path=db) == 100.0

    # Set a rate and convert
    dbm.set_exchange_rate("CNY", "USD", 0.14, db_path=db)
    converted = dbm.convert_to_base(350000, "CNY", db_path=db)
    assert converted == round(350000 * 0.14, 2)

    # No rate set for CAD → USD
    assert dbm.convert_to_base(1000, "CAD", db_path=db) is None


# ═══════════════════════════════════════════════════════════════════════
# 21. _store_receipt — 票据归档约定（cli 层，与 Document_Keeper 同模式）
# ═══════════════════════════════════════════════════════════════════════

import importlib.util as _ilu  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

# 多个 skill 都有 cli.py，裸 `import cli` 名字冲突 → 按路径精确加载 Expense_Tracker 的。
_CLI_PATH = (_Path(__file__).resolve().parent.parent
             / ".codewhale" / "skills" / "Expense_Tracker" / "cli.py")
_spec = _ilu.spec_from_file_location("expense_cli", _CLI_PATH)
expense_cli = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(expense_cli)


def _patch_receipts(monkeypatch, tmp_path):
    """把 cli 的 ROOT / RECEIPTS_DIR 与 data_root 指向临时目录，避免碰真实数据。

    票据现归 data/Family/receipts/，存储链接相对 data_root（Family/receipts/...）。
    """
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(expense_cli, "ROOT", tmp_path)
    monkeypatch.setattr(expense_cli, "RECEIPTS_DIR", tmp_path / "data" / "Family" / "receipts")


def test_store_receipt_archives_external_file(monkeypatch, tmp_path):
    """receipts/ 外的文件 → 复制进 Family/receipts/YYYY-MM/，规范命名、后缀小写、原件保留。"""
    _patch_receipts(monkeypatch, tmp_path)
    src = tmp_path / "inbox" / "photo.JPG"
    src.parent.mkdir()
    src.write_bytes(b"img")

    rel = expense_cli._store_receipt(str(src), "2026-06-01", "expense_午餐")

    assert rel == "Family/receipts/2026-06/2026-06-01_expense_午餐.jpg"
    assert (tmp_path / "data" / rel).read_bytes() == b"img"   # 已复制到目标
    assert src.exists()                                       # copy 非 move，原件还在


def test_store_receipt_keeps_file_already_in_receipts(monkeypatch, tmp_path):
    """已在 receipts/ 内的文件（入站照片）原样返回，不改名、不产生副本。"""
    _patch_receipts(monkeypatch, tmp_path)
    inbound = (tmp_path / "data" / "Family" / "receipts" / "2026-06"
               / "20260601_222413_wechat.jpg")
    inbound.parent.mkdir(parents=True)
    inbound.write_bytes(b"x")

    rel = expense_cli._store_receipt(str(inbound), "2026-06-01", "expense_午餐")

    assert rel == "Family/receipts/2026-06/20260601_222413_wechat.jpg"
    assert len(list(inbound.parent.iterdir())) == 1


def test_store_receipt_missing_file_raises(monkeypatch, tmp_path):
    """文件不存在 → ValueError（main 捕获为干净报错退出 1）。"""
    _patch_receipts(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        expense_cli._store_receipt(str(tmp_path / "nope.jpg"), "2026-06-01", "expense_x")


def test_store_receipt_collision_suffixes(monkeypatch, tmp_path):
    """同名归档冲突 → 追加 _1。"""
    _patch_receipts(monkeypatch, tmp_path)
    src = tmp_path / "a.png"
    src.write_bytes(b"1")

    r1 = expense_cli._store_receipt(str(src), "2026-06-01", "expense_lunch")
    r2 = expense_cli._store_receipt(str(src), "2026-06-01", "expense_lunch")

    assert r1 == "Family/receipts/2026-06/2026-06-01_expense_lunch.png"
    assert r2 == "Family/receipts/2026-06/2026-06-01_expense_lunch_1.png"


def test_store_receipt_invalid_date_falls_back_to_today(monkeypatch, tmp_path):
    """when 为空/非法 → 用今天的年月与日期命名。"""
    from datetime import date as _date
    _patch_receipts(monkeypatch, tmp_path)
    src = tmp_path / "b.jpg"
    src.write_bytes(b"1")

    rel = expense_cli._store_receipt(str(src), "", "expense_x")

    today = _date.today()
    assert rel == f"Family/receipts/{today.strftime('%Y-%m')}/{today.isoformat()}_expense_x.jpg"
