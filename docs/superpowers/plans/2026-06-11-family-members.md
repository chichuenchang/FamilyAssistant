# Family Members Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Registered family members are identified by channel id, their ledger entries are attributed to them, and unregistered senders are silently dropped.

**Architecture:** A `members` registry in config.json (single source of truth) is read by a new `Agent_Runtime/members.py` module. Transports gate every message through `members.resolve()` before the LLM sees it. The four ledger tables gain a `member` column; `agent_core` injects the resolved member into CLI write calls (LLM-supplied attribution is stripped). Registry writes happen only via local CLI commands excluded from the agent whitelist.

**Tech Stack:** Python 3.10+ stdlib only, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-family-members-design.md`

---

## File structure

| File | Change | Responsibility |
|------|--------|----------------|
| `.codewhale/skills/Agent_Runtime/members.py` | create | Registry read/resolve/add/remove, atomic config.json write |
| `.codewhale/skills/Expense_Tracker/models.py` | modify | `member` column in SCHEMA (4 tables) |
| `.codewhale/skills/Expense_Tracker/db.py` | modify | Migration, `member` write param, read filters, `summarize_by_member` |
| `.codewhale/skills/Expense_Tracker/cli.py` | modify | `--member` flags, `member-add/list/remove` commands |
| `.codewhale/skills/Agent_Runtime/agent_core.py` | modify | `member` param + gate, `_apply_member` anti-spoof, schemas |
| `.codewhale/skills/Agent_Runtime/telegram_bot.py` | modify | Transport gate |
| `.codewhale/skills/Agent_Runtime/wechat_ilink.py` | modify | Transport gate |
| `config.json` | modify | Empty `members` section (lockdown default) |
| `tests/conftest.py` | modify | Add Agent_Runtime to sys.path |
| `tests/test_members.py` | create | Registry tests |
| `tests/test_member_db.py` | create | Migration + member column tests |
| `tests/test_agent_member.py` | create | Gate + anti-spoof tests |
| `FamilyAssistant.md`, both SKILL.md | modify | Docs |

Conventions: Chinese docstrings/comments/CLI output like surrounding code. All db functions take optional `db_path`; registry functions take optional `config_path` so tests never touch real files.

---

### Task 1: Member registry module

**Files:**
- Create: `.codewhale/skills/Agent_Runtime/members.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_members.py`

- [ ] **Step 1.1: Extend conftest sys.path**

In `tests/conftest.py`, after the existing `sys.path.insert(0, str(SKILL_DIR))` add:

```python
AGENT_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Agent_Runtime"
)
sys.path.insert(0, str(AGENT_DIR))
```

- [ ] **Step 1.2: Write failing tests**

Create `tests/test_members.py`:

```python
# tests/test_members.py — 成员注册表（Agent_Runtime/members.py）测试。
import json
from pathlib import Path

import pytest
import members as mm


@pytest.fixture
def cfg(tmp_path):
    """临时 config.json，含两个成员。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "members": {
            "爸爸": {"telegram": ["111"], "wechat": ["wx_a"]},
            "妈妈": {"wechat": ["wx_b"]},
        }
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_resolve_known_ids(cfg):
    assert mm.resolve("telegram", "111", cfg) == "爸爸"
    assert mm.resolve("wechat", "wx_a", cfg) == "爸爸"
    assert mm.resolve("wechat", "wx_b", cfg) == "妈妈"


def test_resolve_accepts_int_id(cfg):
    assert mm.resolve("telegram", 111, cfg) == "爸爸"


def test_resolve_unknown_returns_none(cfg):
    assert mm.resolve("telegram", "999", cfg) is None
    assert mm.resolve("wechat", "wx_zzz", cfg) is None
    assert mm.resolve("telegram", "", cfg) is None


def test_resolve_missing_members_section_is_lockdown(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    assert mm.resolve("telegram", "111", p) is None


def test_resolve_corrupt_config_is_lockdown(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not json", encoding="utf-8")
    assert mm.resolve("telegram", "111", p) is None


def test_member_names(cfg):
    assert mm.member_names(cfg) == ["爸爸", "妈妈"]


def test_add_member_new(cfg):
    mm.add_member("娃", telegram=["333"], config_path=cfg)
    assert mm.resolve("telegram", "333", cfg) == "娃"
    # 其他成员不受影响
    assert mm.resolve("telegram", "111", cfg) == "爸爸"


def test_add_member_appends_ids_to_existing(cfg):
    mm.add_member("妈妈", telegram=["222"], config_path=cfg)
    assert mm.resolve("telegram", "222", cfg) == "妈妈"
    assert mm.resolve("wechat", "wx_b", cfg) == "妈妈"


def test_add_member_rejects_id_bound_to_other_member(cfg):
    with pytest.raises(ValueError):
        mm.add_member("娃", telegram=["111"], config_path=cfg)


def test_add_member_same_id_same_member_is_noop(cfg):
    mm.add_member("爸爸", telegram=["111"], config_path=cfg)
    assert mm.load_members(cfg)["爸爸"]["telegram"] == ["111"]


def test_add_member_empty_name_rejected(cfg):
    with pytest.raises(ValueError):
        mm.add_member("", telegram=["444"], config_path=cfg)


def test_add_member_preserves_other_config_keys(cfg):
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    raw["base_currency"] = "USD"
    cfg.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    mm.add_member("娃", wechat=["wx_c"], config_path=cfg)
    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["base_currency"] == "USD"


def test_remove_member(cfg):
    assert mm.remove_member("妈妈", cfg) is True
    assert mm.resolve("wechat", "wx_b", cfg) is None
    assert mm.remove_member("不存在", cfg) is False
```

- [ ] **Step 1.3: Run tests, verify they fail**

Run: `python -m pytest tests/test_members.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'members'`

- [ ] **Step 1.4: Implement members.py**

Create `.codewhale/skills/Agent_Runtime/members.py`:

```python
"""
成员注册表 — 家庭成员与频道身份的映射（config.json members 段）。

格式:
    "members": {
      "爸爸": { "telegram": ["123456789"], "wechat": ["wxid_abc"] }
    }

注册表只在本机用 CLI 管理（member-add / member-list / member-remove，
不在 wechat.allowed_commands 白名单内），Agent Runtime 只读。
未注册的频道 id 一律静默丢弃；注册表缺失/损坏时全部锁定（安全默认）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# 本文件位于 .codewhale/skills/Agent_Runtime/ ，向上 3 级到项目根
ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config.json"

CHANNELS = ("telegram", "wechat")


def _load_config(config_path: Path | None = None) -> dict:
    """解析 config.json；缺失/损坏返回 {}（→ 锁定）。"""
    try:
        return json.loads((config_path or CONFIG_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_members(config_path: Path | None = None) -> dict:
    """members 段；缺失或格式不对返回 {}（→ 锁定）。"""
    members = _load_config(config_path).get("members")
    return members if isinstance(members, dict) else {}


def resolve(channel: str, channel_id, config_path: Path | None = None) -> str | None:
    """频道 id → 成员名；未注册返回 None（调用方必须静默丢弃该消息）。"""
    cid = str(channel_id or "")
    if not cid:
        return None
    for name, bindings in load_members(config_path).items():
        ids = bindings.get(channel) or [] if isinstance(bindings, dict) else []
        if cid in (str(i) for i in ids):
            return name
    return None


def member_names(config_path: Path | None = None) -> list[str]:
    """已登记成员名列表。"""
    return list(load_members(config_path).keys())


def _save_config(cfg: dict, config_path: Path | None = None) -> None:
    """原子写回 config.json（临时文件 + replace，写一半不毁原文件）。"""
    path = config_path or CONFIG_PATH
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_member(name: str, telegram=None, wechat=None,
               config_path: Path | None = None) -> None:
    """新增成员或为已有成员追加频道 id。频道 id 已绑定其他成员时报错。"""
    if not name:
        raise ValueError("成员名不能为空")
    new_ids = {"telegram": [str(i) for i in (telegram or [])],
               "wechat": [str(i) for i in (wechat or [])]}
    for ch in CHANNELS:
        for cid in new_ids[ch]:
            owner = resolve(ch, cid, config_path)
            if owner and owner != name:
                raise ValueError(f"{ch} id {cid} 已绑定成员 {owner}")
    cfg = _load_config(config_path)
    members = cfg.get("members")
    if not isinstance(members, dict):
        members = {}
    entry = members.setdefault(name, {})
    for ch in CHANNELS:
        ids = [str(i) for i in (entry.get(ch) or [])]
        for cid in new_ids[ch]:
            if cid not in ids:
                ids.append(cid)
        if ids:
            entry[ch] = ids
    cfg["members"] = members
    _save_config(cfg, config_path)


def remove_member(name: str, config_path: Path | None = None) -> bool:
    """删除成员（其历史账目仍保留成员名字符串）。"""
    cfg = _load_config(config_path)
    members = cfg.get("members")
    if not isinstance(members, dict) or name not in members:
        return False
    del members[name]
    cfg["members"] = members
    _save_config(cfg, config_path)
    return True
```

- [ ] **Step 1.5: Run tests, verify pass**

Run: `python -m pytest tests/test_members.py -v`
Expected: 13 passed

- [ ] **Step 1.6: Full suite + commit**

Run: `python -m pytest -q` — expected: 33 passed (20 old + 13 new)

```bash
git add .codewhale/skills/Agent_Runtime/members.py tests/test_members.py tests/conftest.py
git commit -m "feat: member registry module (config.json members section)"
```

---

### Task 2: DB schema + migration

**Files:**
- Modify: `.codewhale/skills/Expense_Tracker/models.py` (SCHEMA)
- Modify: `.codewhale/skills/Expense_Tracker/db.py:58-65` (`init_db`)
- Test: `tests/test_member_db.py`

- [ ] **Step 2.1: Write failing tests**

Create `tests/test_member_db.py`:

```python
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
```

- [ ] **Step 2.2: Run tests, verify fail**

Run: `python -m pytest tests/test_member_db.py -v`
Expected: both FAIL (`member` not in columns)

- [ ] **Step 2.3: Add member column to SCHEMA**

In `models.py` SCHEMA, add the line `member          TEXT    NOT NULL DEFAULT '',` directly above `notes` in each of the four tables (`transactions`, `deposits`, `tax_filings`, `transfers`). Example for transactions:

```sql
    receipt_path    TEXT    DEFAULT NULL,
    member          TEXT    NOT NULL DEFAULT '',
    notes           TEXT    DEFAULT '',
```

Do NOT add the member index to SCHEMA — on a legacy DB the index statement would run before migration and fail.

- [ ] **Step 2.4: Migrate in init_db**

In `db.py`, replace `init_db` with:

```python
def init_db(db_path: Optional[str] = None) -> None:
    """初始化数据库：建表 + 索引 + 迁移。幂等。"""
    conn = get_db(db_path)
    conn.executescript(SCHEMA)
    # 迁移：为既有库补加新列
    _ensure_column(conn, "deposits", "account", "TEXT DEFAULT ''")
    for table in ("transactions", "deposits", "transfers", "tax_filings"):
        _ensure_column(conn, table, "member", "TEXT NOT NULL DEFAULT ''")
    # member 索引必须在迁移后建（SCHEMA 里建会在旧库上先于迁移执行而失败）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_txn_member ON transactions(member)")
    conn.commit()
    conn.close()
```

- [ ] **Step 2.5: Run tests, verify pass; full suite; commit**

Run: `python -m pytest tests/test_member_db.py -v` — expected: 2 passed
Run: `python -m pytest -q` — expected: 35 passed

```bash
git add .codewhale/skills/Expense_Tracker/models.py .codewhale/skills/Expense_Tracker/db.py tests/test_member_db.py
git commit -m "feat: member column on ledger tables with idempotent migration"
```

---

### Task 3: db.py member writes, filters, per-member summary

**Files:**
- Modify: `.codewhale/skills/Expense_Tracker/db.py`
- Test: `tests/test_member_db.py` (append)

- [ ] **Step 3.1: Write failing tests**

Append to `tests/test_member_db.py`:

```python
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
```

- [ ] **Step 3.2: Run tests, verify fail**

Run: `python -m pytest tests/test_member_db.py -v`
Expected: new tests FAIL with `TypeError: ... unexpected keyword argument 'member'`

- [ ] **Step 3.3: Implement db.py changes**

1. `add_transaction`: add parameter `member: str = ""` (after `notes`); INSERT becomes:

```python
    cur = conn.execute(
        """INSERT INTO transactions (type, amount, currency, category, description, date, receipt_path, member, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (type_, amount, currency, category, description, date_, receipt_path, member, notes),
    )
```

2. `get_transactions`: add parameter `member: Optional[str] = None`; add filter after the `end_date` block:

```python
    if member:
        sql += " AND member = ?"
        params.append(member)
```

3. `summarize_by_category` and `monthly_summary`: add parameter `member: Optional[str] = None`; add the same two-line filter right after the existing `month`/`year` filters (before `GROUP BY`).

4. New function after `monthly_summary`:

```python
def summarize_by_member(
    type_: str = "expense",
    year: Optional[int] = None,
    month: Optional[int] = None,
    db_path: Optional[str] = None,
) -> dict[str, dict[str, float]]:
    """按币种 + 成员汇总金额。member 为空的旧记录归"家庭"。不跨币种相加。

    返回 {currency: {member: total}}。
    """
    conn = get_db(db_path)
    sql = "SELECT currency, member, SUM(amount) AS total FROM transactions WHERE type = ?"
    params: list[Any] = [type_]
    if year:
        sql += " AND strftime('%Y', date) = ?"
        params.append(str(year))
    if month:
        sql += " AND strftime('%m', date) = ?"
        params.append(f"{month:02d}")
    sql += " GROUP BY currency, member ORDER BY currency, total DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["currency"], {})[r["member"] or "家庭"] = round(r["total"], 2)
    return out
```

5. `add_deposit`: add parameter `member: str = ""`; INSERT becomes:

```python
    cur = conn.execute(
        """INSERT INTO deposits (amount, currency, bank, account, term_months, rate, start_date, maturity_date, receipt_path, member, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (amount, currency, bank, account, term_months, rate, start_date, maturity_date or "", receipt_path, member, notes),
    )
```

6. `add_transfer`: add parameter `member: str = ""`; pass `member=member` into the internal `add_deposit(...)` call; INSERT becomes:

```python
    cur = conn.execute(
        """INSERT INTO transfers
           (from_desc, from_type, from_deposit_id, from_amount, from_currency,
            to_amount, to_currency, rate, exchange_date, to_bank, to_account,
            to_type, transfer_date, to_deposit_id, member, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (from_desc, from_type, from_deposit_id, from_amount, from_currency,
         to_amount, to_currency, rate, exchange_date, to_bank, to_account,
         to_type, transfer_date, to_deposit_id, member, notes),
    )
```

7. `add_tax_filing`: add parameter `member: str = ""`; INSERT becomes:

```python
    cur = conn.execute(
        """INSERT INTO tax_filings (year, country, filing_date, data, receipt_path, member, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (year, country, filing_date, json.dumps(data, ensure_ascii=False), receipt_path, member, notes),
    )
```

- [ ] **Step 3.4: Run tests, verify pass; full suite; commit**

Run: `python -m pytest tests/test_member_db.py -v` — expected: 8 passed
Run: `python -m pytest -q` — expected: 41 passed

```bash
git add .codewhale/skills/Expense_Tracker/db.py tests/test_member_db.py
git commit -m "feat: member attribution on writes, member filters and per-member summary"
```

---

### Task 4: CLI member flags + registry commands

**Files:**
- Modify: `.codewhale/skills/Expense_Tracker/cli.py`

No new pytest here (db and registry layers are covered; CLI is thin wiring per existing test convention). Verification is by smoke commands.

- [ ] **Step 4.1: Import registry**

In `cli.py` after the existing `sys.path.insert(0, str(Path(__file__).resolve().parent))` add:

```python
# 成员注册表（Agent_Runtime skill；跨 skill 经 sys.path，与 agent_core 引 OCR 同模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import members as members_registry
```

And in the `from db import (...)` block add `summarize_by_member,`.

- [ ] **Step 4.2: Member validation helper + write-command wiring**

Add after the import block:

```python
def _validate_member(name: str) -> str:
    """非空成员名必须已登记；返回原值或抛 ValueError。空值放行（家庭级）。"""
    if not name:
        return ""
    known = members_registry.member_names()
    if name not in known:
        raise ValueError(
            f"未知成员 '{name}'。已登记: {', '.join(known) or '（无）'}。用 member-add 添加。")
    return name
```

Wire `member=_validate_member(args.member or "")` into the four write handlers:
- `cmd_add`: add `member=_validate_member(args.member or "")` to the `add_transaction(...)` call
- `cmd_deposit_add`: same for `add_deposit(...)`
- `cmd_transfer_add`: same for `add_transfer(...)`
- `cmd_tax_add`: same for `add_tax_filing(...)`

- [ ] **Step 4.3: Read filters + by-member summary**

- `cmd_list`: pass `member=args.member` to `get_transactions(...)`
- `cmd_monthly`: pass `member=args.member` to `monthly_summary(...)`
- `cmd_summary`: replace body head with:

```python
def cmd_summary(args):
    if args.by_member:
        result = summarize_by_member(type_=args.type or "expense",
                                     year=args.year, month=args.month)
        if not result:
            print("没有数据。")
            return
        for i, (cur, members) in enumerate(result.items()):
            if i:
                print()
            print(f"【{cur}】")
            for name, total in members.items():
                print(f"{name}: {total:.2f} {cur}")
        return
    result = summarize_by_category(
        type_=args.type or "expense",
        year=args.year,
        month=args.month,
        member=args.member,
    )
    # ……以下原样保留
```

- [ ] **Step 4.4: Registry command handlers**

Add after `cmd_fx_get`:

```python
def cmd_member_add(args):
    if not args.telegram and not args.wechat:
        raise ValueError("至少提供一个 --telegram 或 --wechat 频道 id")
    members_registry.add_member(args.name, telegram=args.telegram, wechat=args.wechat)
    print(f"已登记成员 {args.name}")
    cmd_member_list(args)


def cmd_member_list(_args):
    members = members_registry.load_members()
    if not members:
        print("没有已登记成员。用 member-add 添加。")
        return
    for name, b in members.items():
        tg = ",".join(b.get("telegram") or []) or "-"
        wx = ",".join(b.get("wechat") or []) or "-"
        print(f"{name}: telegram={tg} wechat={wx}")


def cmd_member_remove(args):
    ok = members_registry.remove_member(args.name)
    print(f"{'已删除成员' if ok else '未找到成员'} {args.name}")
```

- [ ] **Step 4.5: argparse wiring**

Add `p.add_argument("--member", help="归属成员（须已登记）")` to the `add`, `deposit-add`, `transfer-add`, `tax-add` parsers.
Add `p.add_argument("--member", help="按成员过滤")` to the `list`, `summary`, `monthly` parsers.
Add `p.add_argument("--by-member", action="store_true", help="按成员汇总")` to the `summary` parser.

Add new subparsers before `# fx set`:

```python
    # member 管理（仅本机使用；不在 wechat.allowed_commands 白名单内，Agent 调不到）
    p = sub.add_parser("member-add", help="登记成员并绑定频道 id（仅本机）")
    p.add_argument("name")
    p.add_argument("--telegram", action="append", help="Telegram chat id，可多次")
    p.add_argument("--wechat", action="append", help="微信用户 id，可多次")

    sub.add_parser("member-list", help="列出已登记成员")

    p = sub.add_parser("member-remove", help="删除成员（账目保留成员名）")
    p.add_argument("name")
```

Extend `dispatch`:

```python
        "member-add": cmd_member_add,
        "member-list": cmd_member_list,
        "member-remove": cmd_member_remove,
```

Do NOT touch `config.json wechat.allowed_commands` — member commands stay off the whitelist.

- [ ] **Step 4.6: Smoke verification**

```powershell
python .codewhale/skills/Expense_Tracker/cli.py member-list
# 期望: 没有已登记成员。用 member-add 添加。
python .codewhale/skills/Expense_Tracker/cli.py add --type expense --amount 1 --date 2026-06-11 --member 不存在
# 期望: 错误: 未知成员 '不存在' …  exit code 1
python -m pytest -q
# 期望: 41 passed
```

- [ ] **Step 4.7: Commit**

```bash
git add .codewhale/skills/Expense_Tracker/cli.py
git commit -m "feat: --member flags and local-only member-add/list/remove CLI commands"
```

---

### Task 5: agent_core gate + anti-spoof injection

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_agent_member.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/test_agent_member.py`:

```python
# tests/test_agent_member.py — agent_core 成员闸门与防冒名注入。
import agent_core


def test_apply_member_forces_resolved_member_on_writes():
    out = agent_core._apply_member("add_transaction",
                                   {"member": "妈妈", "amount": 5}, "爸爸")
    assert out["member"] == "爸爸"


def test_apply_member_strips_llm_member_when_no_resolved_member():
    out = agent_core._apply_member("add_transaction", {"member": "妈妈"}, "")
    assert "member" not in out


def test_apply_member_covers_all_write_tools():
    for tool in ("add_transaction", "add_deposit", "add_transfer", "add_tax"):
        out = agent_core._apply_member(tool, {}, "爸爸")
        assert out["member"] == "爸爸", tool


def test_apply_member_keeps_llm_member_on_reads():
    out = agent_core._apply_member("list_transactions", {"member": "妈妈"}, "爸爸")
    assert out["member"] == "妈妈"


def test_handle_returns_empty_without_member():
    agent = agent_core.Agent()
    assert agent.handle("记账 午餐45", user="x") == ""
    assert agent.handle("记账 午餐45", user="x", member="") == ""


def test_handle_image_returns_empty_without_member(tmp_path):
    agent = agent_core.Agent()
    assert agent.handle_image(str(tmp_path / "x.jpg"), user="x", member="") == ""
```

- [ ] **Step 5.2: Run tests, verify fail**

Run: `python -m pytest tests/test_agent_member.py -v`
Expected: FAIL — `AttributeError: module 'agent_core' has no attribute '_apply_member'`

- [ ] **Step 5.3: Implement agent_core changes**

1. After `_TOOL_MAP` definition add:

```python
# 写工具集合：归属强制由代码注入（防 LLM 冒名记到别人头上）
_MEMBER_WRITE_TOOLS = {"add_transaction", "add_deposit", "add_transfer", "add_tax"}


def _apply_member(tool_name: str, targs: dict, member: str) -> dict:
    """写工具：剥离 LLM 给的 member，注入解析出的成员名。读工具原样放行。"""
    if tool_name in _MEMBER_WRITE_TOOLS:
        targs = {k: v for k, v in targs.items() if k.lstrip("-") != "member"}
        if member:
            targs["member"] = member
    return targs
```

2. `Agent.handle` signature → `def handle(self, text: str, user: str = "default", member: str = "") -> str:` and insert as the FIRST lines of the body (before the empty-text check):

```python
        # 防御纵深：传输层闸门漏掉的未注册来源，这里二次拦截，不碰 LLM
        if not member:
            return ""
```

3. In `handle`, system message gains the member line — replace `msgs = [{"role": "system", "content": self.system_prompt}]` with:

```python
        member_note = (f"\n\n## 当前对话成员\n{member} —— 写入类操作自动归到该成员名下；"
                       f"查询类工具可用 member 参数按成员过滤。")
        msgs = [{"role": "system", "content": self.system_prompt + member_note}]
```

4. In the tool-execution loop, before `result = fn(targs) if fn else ...` insert:

```python
                targs = _apply_member(name, targs, member)
```

5. `Agent.handle_image` signature → `def handle_image(self, image_path: str, user: str = "default", member: str = "") -> str:`; first body lines:

```python
        if not member:
            return ""
```

and the inner call becomes `return self.handle(prompt, user=user, member=member)`.

6. Read schemas: in `TOOL_SCHEMAS`, add `"member": _s("按成员过滤，如只看某个家庭成员的账")` to the properties of `list_transactions`, `get_summary`, and `get_monthly`. Add `"by-member": {"type": "boolean", "description": "按成员汇总（谁花了多少）"}` to `get_summary`.

7. `__main__` test loop: `print(agent.handle(msg))` → `print(agent.handle(msg, member="本地测试"))`.

- [ ] **Step 5.4: Run tests, verify pass; full suite; commit**

Run: `python -m pytest tests/test_agent_member.py -v` — expected: 6 passed
Run: `python -m pytest -q` — expected: 47 passed

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_agent_member.py
git commit -m "feat: agent member gate and code-enforced write attribution"
```

---

### Task 6: Transport gates (Telegram + WeChat)

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py`
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py`

Gate behavior is already unit-tested via `members.resolve` (Task 1) and the agent gate (Task 5); transports are wiring.

- [ ] **Step 6.1: telegram_bot.py**

1. Import: `from agent_core import Agent, receipt_month_dir` → add `from members import resolve` on the next line.
2. In `run()`, directly after `chat_id = msg["chat"]["id"]` (before `user_name`/`text` and before the `/start` handling) insert:

```python
            # 成员闸门：未注册 id 静默丢弃（不回复、不进 LLM），本地留一行日志
            member = resolve("telegram", str(chat_id))
            if member is None:
                print(f"[tg] 忽略未注册来源 chat_id={chat_id}")
                offset = max(offset, update_id)
                continue
```

3. `agent.handle_image(str(dest), user=str(chat_id))` → `agent.handle_image(str(dest), user=str(chat_id), member=member)`
4. `agent.handle(text, user=str(chat_id))` → `agent.handle(text, user=str(chat_id), member=member)`

Note: `/start` now only answers registered members — intended (no information leak to strangers).

- [ ] **Step 6.2: wechat_ilink.py**

1. Import: add `from members import resolve` after the `agent_core` import.
2. `handle_text` becomes:

```python
    @bot.on_text
    def handle_text(msg):
        member = resolve("wechat", msg.from_user)
        if member is None:
            print(f"[wx] 忽略未注册来源 {msg.from_user}")
            return
        print(f"[wx] 文字消息 from {msg.from_user}({member}): {msg.text[:60]}")
        try:
            reply = agent.handle(msg.text, user=msg.from_user, member=member)
            msg.reply_text(reply)
        except Exception as e:
            msg.reply_text(f"处理出错: {e}")
```

3. `handle_image` gains the same gate at the top:

```python
    @bot.on_image
    def handle_image(msg):
        member = resolve("wechat", msg.from_user)
        if member is None:
            print(f"[wx] 忽略未注册来源 {msg.from_user}")
            return
        print(f"[wx] 图片消息 from {msg.from_user}({member})")
        try:
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            img_path = receipt_month_dir(now) / f"{ts}_wechat.jpg"
            msg.save(str(img_path))
            reply = agent.handle_image(str(img_path), user=msg.from_user, member=member)
            msg.reply_text(reply)
        except Exception as e:
            msg.reply_text(f"图片处理出错: {e}")
```

4. The `on_voice` / `on_file` / `on_video` handlers also reply to strangers today. Add the same two-line gate (resolve + return) at the top of each, without the print to keep noise down:

```python
        if resolve("wechat", msg.from_user) is None:
            return
```

- [ ] **Step 6.3: Verify + commit**

```powershell
python -m py_compile .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Agent_Runtime/wechat_ilink.py
python -m pytest -q
# 期望: 47 passed
```

```bash
git add .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Agent_Runtime/wechat_ilink.py
git commit -m "feat: transport member gate, unregistered senders silently dropped"
```

---

### Task 7: config.json, docs, final verification

**Files:**
- Modify: `config.json`, `FamilyAssistant.md`, `.codewhale/skills/Agent_Runtime/SKILL.md`, `.codewhale/skills/Expense_Tracker/SKILL.md`

- [ ] **Step 7.1: config.json**

Add after `"receipts_dir": "receipts",`:

```json
  "members": {},
```

Empty = lockdown: bots answer no one until the owner runs `member-add`. Intentional.

- [ ] **Step 7.2: FamilyAssistant.md**

In the config table add a row:

```markdown
| `members` | `Agent_Runtime/members.py`（只读：resolve）、`cli.py member-*`（本机写入） |
```

- [ ] **Step 7.3: Agent_Runtime/SKILL.md**

- Channel contract (`要点` list): change the contract lines to mention member:

```markdown
- 每条消息先过成员闸门：`members.resolve(频道, 频道id)` 返回 None → 静默丢弃（不回复、不进 LLM）。
- 用频道内唯一 id 作 `user`（隔离对话历史），解析出的成员名作 `member` 传给 `agent.handle(text, user, member)` / `agent.handle_image(path, user, member)`。
```

- Security section (`## 安全`) add:

```markdown
- **成员注册表**：`config.json` `members` 只在本机用 `cli.py member-add/list/remove` 管理（不在命令白名单内，Agent 调不到）。未注册频道 id 一律静默丢弃；写入类账目的归属由 `agent_core` 注入解析出的成员名，LLM 给的 member 一律剥离（防冒名）。
```

- [ ] **Step 7.4: Expense_Tracker/SKILL.md**

Add a short section after the transfers section:

```markdown
## 家庭成员

四张账目表都有 `member` 列（空 = 家庭级，旧数据自动归"家庭"）。

- 写入：`add` / `deposit-add` / `transfer-add` / `tax-add` 支持 `--member <名>`（须已登记）。
- 查询：`list` / `summary` / `monthly` 支持 `--member` 过滤；`summary --by-member` 按成员汇总。
- 登记（仅本机，Agent 白名单外）：
  `member-add 爸爸 --telegram 123456789 --wechat wxid_abc` / `member-list` / `member-remove 爸爸`
- 注册表存 `config.json` `members` 段；改后重启机器人生效。
```

- [ ] **Step 7.5: Final verification**

```powershell
python -m pytest -q                       # 期望: 47 passed
python -m py_compile .codewhale/skills/Agent_Runtime/agent_core.py .codewhale/skills/Agent_Runtime/members.py .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Agent_Runtime/wechat_ilink.py .codewhale/skills/Expense_Tracker/cli.py .codewhale/skills/Expense_Tracker/db.py .codewhale/skills/Expense_Tracker/models.py
python .codewhale/skills/Expense_Tracker/cli.py member-list   # 期望: 没有已登记成员
python .codewhale/skills/Expense_Tracker/cli.py init          # 迁移真实 ledger.db（幂等）
```

- [ ] **Step 7.6: Commit**

```bash
git add config.json FamilyAssistant.md .codewhale/skills/Agent_Runtime/SKILL.md .codewhale/skills/Expense_Tracker/SKILL.md
git commit -m "feat: members config section and docs; lockdown by default"
```

---

## Post-implementation (owner action, not in code)

Register real members on this machine, then restart bots:

```powershell
python .codewhale/skills/Expense_Tracker/cli.py member-add 爸爸 --telegram <你的chat_id> --wechat <你的wxid>
```

Until then, both bots silently ignore everyone (lockdown default).
