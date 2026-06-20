# Document Keeper Implementation Plan

> ⚠️ **历史设计文档** — 描述的是当时的单库布局。存储已于 2026-06-19 重构为分库（`data/Family/` 家庭共享 + `data/<成员>/` 成员私有），权威设计见 `docs/superpowers/specs/2026-06-19-per-member-storage-layout-design.md`。下文路径/配置键（`data/ledger.db`、`db_path`/`receipts_dir`/`documents_dir`、根级 `receipts/`/`documents/`）多已过时。


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New `.codewhale/skills/Document_Keeper/` skill that ingests, indexes, and stores family documents (lease, insurance, SIN, health card), tracks expiry dates, and reminds members on-demand (`doc-due`) and via daily push through existing WeChat/Telegram transports.

**Architecture:** Self-contained skill following the Expense_Tracker pattern: config-driven constants module, SQLite layer writing a `documents` table into the shared `data/ledger.db`, argparse CLI invoked by the agent via the existing whitelisted-subprocess mechanism, plus a `reminder.py` module the transports call once per day. Original files archived under `documents/<doc_type>/`.

**Tech Stack:** Python 3.10+ stdlib only, SQLite, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-11-document-keeper-design.md`

**Naming note (deliberate deviation):** the data modules are `doc_models.py` / `doc_db.py` (not `models.py` / `db.py`) because Expense_Tracker already owns the module names `models` and `db` on `sys.path` in shared processes (pytest, and `reminder.py` imported by transports). `cli.py` keeps its conventional name — it only ever runs as a subprocess with its own `sys.path`, never imported in-process.

---

### Task 1: Config additions, documents dir, gitignore

**Files:**
- Modify: `config.json`
- Create: `documents/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Add Document Keeper keys to config.json**

In `config.json`, insert after the `"receipts_dir": "receipts",` line:

```json
  "documents_dir": "documents",
  "doc_types": ["lease", "insurance", "health", "id_document", "other"],
  "reminder_lead_days": 30,
```

And extend `wechat.allowed_commands` (note: `doc-remove` deliberately absent — local-only, same posture as `member-*`):

```json
    "allowed_commands": [
      "add", "list", "summary", "monthly", "delete",
      "deposit-add", "deposit-list",
      "transfer-add", "transfer-list",
      "tax-add", "tax-list",
      "fx-get", "fx-set", "categories",
      "doc-add", "doc-list", "doc-show", "doc-due", "doc-update", "doc-ack"
    ]
```

- [ ] **Step 2: Create documents dir and ignore its contents**

Create empty file `documents/.gitkeep`. Append to `.gitignore`:

```
# 文档存档（按类型子目录 documents/<type>/）
documents/*
!documents/.gitkeep

# 文档提醒状态
data/.doc_reminder_state
```

- [ ] **Step 3: Validate JSON parses**

Run: `python -c "import json; json.load(open('config.json', encoding='utf-8')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add config.json .gitignore documents/.gitkeep
git commit -m "feat: document keeper config keys, documents dir, command whitelist"
```

---

### Task 2: doc_models.py + conftest wiring

**Files:**
- Create: `.codewhale/skills/Document_Keeper/doc_models.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_document_keeper.py` (new)

- [ ] **Step 1: Add Document_Keeper to test path and a doc db fixture**

In `tests/conftest.py`, after the `AGENT_DIR` block, add:

```python
DOC_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Document_Keeper"
)
sys.path.insert(0, str(DOC_DIR))
```

And after the existing `db` fixture, add:

```python
@pytest.fixture
def doc_db_path(tmp_path):
    """Temporary SQLite database initialised with the documents table."""
    import doc_db as doc_dbm
    path = str(tmp_path / "docs.db")
    doc_dbm.init_db(db_path=path)
    return path
```

- [ ] **Step 2: Write the failing smoke test**

Create `tests/test_document_keeper.py`:

```python
# tests/test_document_keeper.py — Document Keeper skill tests.
import json
from datetime import date, timedelta

import pytest

import doc_models


def test_doc_models_constants():
    assert "other" in doc_models.DOC_TYPES or doc_models.DOC_TYPES
    assert doc_models.REMINDER_LEAD_DAYS >= 1
    assert doc_models.DOC_STATUSES == ("active", "expired", "archived", "superseded")
    assert "documents" in doc_models.SCHEMA


def test_doc_models_fallback_on_missing_config(tmp_path):
    assert doc_models._load_config_from(tmp_path / "nope.json") == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'doc_models'`

- [ ] **Step 4: Write doc_models.py**

Create `.codewhale/skills/Document_Keeper/doc_models.py`:

```python
"""
Family Assistant — Document Keeper 数据模型定义

家庭重要文档（租约/保险/证件等）的 SQLite 表结构。
文档类型 / 存档目录 / 提醒提前天数 全部来自项目根 config.json（单一事实来源）。
本模块导入时读取一次 config.json 并暴露为常量；改这些值只改 config.json
（改后重启进程生效）。config.json 缺失/损坏时用下方应急回退值。

模块名带 doc_ 前缀（不叫 models/db）：Expense_Tracker 已在共享进程
（pytest、传输层 import reminder）占用这两个模块名，避免冲突。
"""

import json
from pathlib import Path

# 文档状态（结构性，固定，不放 config.json）
DOC_STATUSES = ("active", "expired", "archived", "superseded")

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"


def _load_config_from(path: Path) -> dict:
    """解析指定 config.json；缺失或损坏时返回空 dict（回退到应急默认）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_config() -> dict:
    return _load_config_from(_CONFIG_PATH)


_cfg = _load_config()

# 应急回退（仅 config.json 缺失/损坏时使用；正常运行值来自 config.json）
_FALLBACK_DOC_TYPES = ["other"]

DOC_TYPES = list(_cfg.get("doc_types") or _FALLBACK_DOC_TYPES)
REMINDER_LEAD_DAYS = int(_cfg.get("reminder_lead_days") or 30)

_ROOT = _CONFIG_PATH.parent
DOCUMENTS_DIR = _ROOT / (_cfg.get("documents_dir") or "documents")
DB_PATH = _ROOT / (_cfg.get("db_path") or "data/ledger.db")

# ---------- SQL DDL ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type      TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    member        TEXT    NOT NULL DEFAULT '',
    issuer        TEXT    DEFAULT '',
    doc_number    TEXT    DEFAULT '',
    issue_date    TEXT    DEFAULT '',
    expiry_date   TEXT    DEFAULT '',
    action_note   TEXT    DEFAULT '',
    remind_days   INTEGER DEFAULT NULL,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    file_path     TEXT    DEFAULT '',
    ocr_text      TEXT    DEFAULT '',
    data          TEXT    NOT NULL DEFAULT '{}',
    status        TEXT    NOT NULL DEFAULT 'active'
                  CHECK(status IN ('active','expired','archived','superseded')),
    notes         TEXT    DEFAULT '',
    created_at    TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_doc_type   ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_expiry ON documents(expiry_date);
CREATE INDEX IF NOT EXISTS idx_doc_member ON documents(member);
CREATE INDEX IF NOT EXISTS idx_doc_status ON documents(status);
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Document_Keeper/doc_models.py tests/conftest.py tests/test_document_keeper.py
git commit -m "feat: document keeper models module and schema"
```

---

### Task 3: doc_db.py — init / add / get / show

**Files:**
- Create: `.codewhale/skills/Document_Keeper/doc_db.py`
- Test: `tests/test_document_keeper.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_document_keeper.py`:

```python
import doc_db


def _add(doc_db_path, **kw):
    args = dict(doc_type="lease", title="2026公寓租约", db_path=doc_db_path)
    args.update(kw)
    return doc_db.add_document(**args)


class TestAddGet:
    def test_add_and_get_roundtrip(self, doc_db_path):
        doc_id, dup = _add(doc_db_path, issuer="房东张三", doc_number="L-001",
                           issue_date="2026-03-01", expiry_date="2027-02-28",
                           action_note="提前60天通知房东", ocr_text="租赁合同 甲方 乙方")
        assert doc_id > 0 and dup is None
        d = doc_db.get_document(doc_id, db_path=doc_db_path)
        assert d["title"] == "2026公寓租约"
        assert d["expiry_date"] == "2027-02-28"
        assert d["status"] == "active"
        assert d["acknowledged"] == 0
        assert isinstance(d["data"], dict)

    def test_get_document_missing(self, doc_db_path):
        assert doc_db.get_document(999, db_path=doc_db_path) is None

    def test_invalid_doc_type_rejected(self, doc_db_path):
        with pytest.raises(ValueError):
            _add(doc_db_path, doc_type="nonsense")

    def test_invalid_date_rejected(self, doc_db_path):
        with pytest.raises(ValueError):
            _add(doc_db_path, expiry_date="2027/02/28")

    def test_init_idempotent_on_existing_ledger(self, db):
        # ``db`` fixture = Expense_Tracker-initialised ledger; adding the
        # documents table to it must not disturb existing tables.
        doc_db.init_db(db_path=db)
        doc_db.init_db(db_path=db)  # twice = no-op
        doc_id, _ = _add(db)
        assert doc_id > 0


class TestList:
    def test_filters_and_keyword(self, doc_db_path):
        _add(doc_db_path, title="租约A", ocr_text="甲方乙方", member="爸爸")
        _add(doc_db_path, doc_type="insurance", title="车险保单", ocr_text="保险金额")
        rows = doc_db.get_documents(doc_type="lease", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["租约A"]
        rows = doc_db.get_documents(keyword="保险", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["车险保单"]
        rows = doc_db.get_documents(member="爸爸", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["租约A"]

    def test_hidden_statuses_excluded_by_default(self, doc_db_path):
        doc_id, _ = _add(doc_db_path, title="旧租约")
        doc_db.update_document(doc_id, status="superseded", db_path=doc_db_path)
        assert doc_db.get_documents(db_path=doc_db_path) == []
        rows = doc_db.get_documents(status="superseded", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["旧租约"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'doc_db'`

- [ ] **Step 3: Write doc_db.py (including update_document used by the list test — full version now, no stub)**

Create `.codewhale/skills/Document_Keeper/doc_db.py`:

```python
"""
Family Assistant — Document Keeper 数据库操作层

documents 表的 SQLite CRUD 和到期查询。Agent / CLI / reminder 统一走这个模块。
表建在共享账本库 data/ledger.db（DB_PATH 来自 config.json db_path，经 doc_models）。
"""

import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 doc_models
from doc_models import (
    SCHEMA, DOC_TYPES, DOC_STATUSES, REMINDER_LEAD_DAYS, DB_PATH,
)

# 可经 update_document 修改的列（id / created_at / data / acknowledged 除外；
# acknowledged 由 ack_document 与到期日变更逻辑管理）
_UPDATABLE = {
    "doc_type", "title", "member", "issuer", "doc_number", "issue_date",
    "expiry_date", "action_note", "remind_days", "file_path", "ocr_text",
    "status", "notes",
}


def get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，自动启用 WAL 和 foreign keys。"""
    path = db_path or str(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """初始化 documents 表 + 索引。幂等，不动账本既有表。"""
    conn = get_db(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _check_date(value: str, label: str) -> str:
    """空值放行；非空必须是 ISO 日期 YYYY-MM-DD。"""
    if not value:
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} 需为 ISO 日期 YYYY-MM-DD，收到 '{value}'")
    return value


def _check_doc_type(value: str) -> str:
    if value not in DOC_TYPES:
        raise ValueError(f"无效文档类型 '{value}'。可选: {', '.join(DOC_TYPES)}")
    return value


def file_sha256(path: str) -> str:
    """文件内容 SHA-256（用于无编号文档的重复检测）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate(
    doc_type: str,
    doc_number: str = "",
    file_sha: str = "",
    db_path: Optional[str] = None,
) -> Optional[dict]:
    """同类型 + 同编号（或同文件哈希）的非 superseded 文档即视为重复。"""
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM documents WHERE doc_type = ? AND status != 'superseded'",
        (doc_type,),
    ).fetchall()
    conn.close()
    for r in rows:
        d = dict(r)
        if doc_number and d["doc_number"] == doc_number:
            return d
        if not doc_number and file_sha:
            try:
                existing_sha = json.loads(d["data"]).get("file_sha256", "")
            except (json.JSONDecodeError, TypeError):
                existing_sha = ""
            if existing_sha and existing_sha == file_sha:
                return d
    return None


def add_document(
    doc_type: str,
    title: str,
    member: str = "",
    issuer: str = "",
    doc_number: str = "",
    issue_date: str = "",
    expiry_date: str = "",
    action_note: str = "",
    remind_days: Optional[int] = None,
    file_path: str = "",
    ocr_text: str = "",
    data: Optional[dict] = None,
    notes: str = "",
    force: bool = False,
    db_path: Optional[str] = None,
) -> tuple[int, Optional[dict]]:
    """归档一份文档，返回 (新记录 id, 疑似重复记录|None)。

    检出重复且未 force 时不写入，返回 (0, 重复记录)。
    file_path 指向存在的文件时计算 SHA-256 存入 data.file_sha256。
    """
    _check_doc_type(doc_type)
    if not title.strip():
        raise ValueError("title 不能为空")
    _check_date(issue_date, "issue_date")
    _check_date(expiry_date, "expiry_date")

    payload = dict(data or {})
    file_sha = ""
    if file_path:
        root = Path(__file__).resolve().parents[3]
        p = Path(file_path)
        abs_p = p if p.is_absolute() else root / p
        if abs_p.exists():
            file_sha = file_sha256(str(abs_p))
            payload["file_sha256"] = file_sha

    if not force:
        dup = find_duplicate(doc_type, doc_number, file_sha, db_path)
        if dup:
            return 0, dup

    conn = get_db(db_path)
    cur = conn.execute(
        """INSERT INTO documents
           (doc_type, title, member, issuer, doc_number, issue_date, expiry_date,
            action_note, remind_days, file_path, ocr_text, data, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (doc_type, title, member, issuer, doc_number, issue_date, expiry_date,
         action_note, remind_days, file_path, ocr_text,
         json.dumps(payload, ensure_ascii=False), notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id, None


def get_documents(
    doc_type: Optional[str] = None,
    member: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
    db_path: Optional[str] = None,
) -> list[dict]:
    """查询文档。默认隐藏 archived / superseded；指定 status 则只看该状态。"""
    conn = get_db(db_path)
    sql = "SELECT * FROM documents WHERE 1=1"
    params: list[Any] = []

    if status:
        sql += " AND status = ?"
        params.append(status)
    else:
        sql += " AND status NOT IN ('archived','superseded')"
    if doc_type:
        sql += " AND doc_type = ?"
        params.append(doc_type)
    if member:
        sql += " AND member = ?"
        params.append(member)
    if keyword:
        kw = f"%{keyword}%"
        sql += " AND (title LIKE ? OR ocr_text LIKE ? OR notes LIKE ?)"
        params += [kw, kw, kw]

    sql += " ORDER BY (expiry_date = ''), expiry_date, id LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_document(doc_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """单文档详情；data 字段解析为 dict。不存在返回 None。"""
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    try:
        d["data"] = json.loads(d["data"])
    except (json.JSONDecodeError, TypeError):
        d["data"] = {}
    return d


def update_document(doc_id: int, db_path: Optional[str] = None, **fields) -> bool:
    """更新文档字段。expiry_date 变化时自动清零 acknowledged（重新进入提醒）。"""
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"不可更新的字段: {', '.join(sorted(unknown))}")
    if not fields:
        raise ValueError("没有要更新的字段")
    if "doc_type" in fields:
        _check_doc_type(fields["doc_type"])
    if "status" in fields and fields["status"] not in DOC_STATUSES:
        raise ValueError(f"无效状态 '{fields['status']}'。可选: {', '.join(DOC_STATUSES)}")
    for k in ("issue_date", "expiry_date"):
        if k in fields:
            fields[k] = _check_date(fields[k] or "", k)

    current = get_document(doc_id, db_path=db_path)
    if current is None:
        return False
    if "expiry_date" in fields and fields["expiry_date"] != current["expiry_date"]:
        fields["acknowledged"] = 0

    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db(db_path)
    conn.execute(f"UPDATE documents SET {cols} WHERE id = ?",
                 (*fields.values(), doc_id))
    conn.commit()
    conn.close()
    return True


def ack_document(doc_id: int, db_path: Optional[str] = None) -> bool:
    """确认到期提醒：每日推送跳过该文档，直到到期日被更新。"""
    conn = get_db(db_path)
    cur = conn.execute("UPDATE documents SET acknowledged = 1 WHERE id = ?", (doc_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def remove_document(
    doc_id: int,
    delete_file: bool = False,
    db_path: Optional[str] = None,
) -> bool:
    """删除文档记录；delete_file=True 时同时删除原始文件。"""
    doc = get_document(doc_id, db_path=db_path)
    if doc is None:
        return False
    conn = get_db(db_path)
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    if delete_file and doc["file_path"]:
        root = Path(__file__).resolve().parents[3]
        p = Path(doc["file_path"])
        abs_p = p if p.is_absolute() else root / p
        try:
            abs_p.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def due_documents(
    days: Optional[int] = None,
    today: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict]:
    """到期窗口内的 active 文档（含已过期），附 days_left 字段。

    提前量优先级：显式 days 参数 > 该文档 remind_days > config reminder_lead_days。
    排序：未确认在前，再按剩余天数升序。
    """
    today_d = date.fromisoformat(today) if today else date.today()
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM documents WHERE status = 'active' AND expiry_date != ''"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if days is not None:
            lead = days
        elif d["remind_days"] is not None:
            lead = d["remind_days"]
        else:
            lead = REMINDER_LEAD_DAYS
        expiry = date.fromisoformat(d["expiry_date"])
        if expiry - timedelta(days=lead) <= today_d:
            d["days_left"] = (expiry - today_d).days
            out.append(d)
    out.sort(key=lambda d: (d["acknowledged"], d["days_left"]))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: all PASSED (2 from Task 2 + 7 new)

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Document_Keeper/doc_db.py tests/test_document_keeper.py
git commit -m "feat: document keeper db layer with CRUD and keyword search"
```

---

### Task 4: doc_db.py — duplicate detection tests

(Implementation already exists from Task 3; this task locks behavior with tests.)

**Files:**
- Test: `tests/test_document_keeper.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_document_keeper.py`:

```python
class TestDuplicates:
    def test_same_number_blocked(self, doc_db_path):
        _add(doc_db_path, doc_number="P-123")
        doc_id, dup = _add(doc_db_path, title="同一份", doc_number="P-123")
        assert doc_id == 0 and dup is not None

    def test_same_number_force(self, doc_db_path):
        _add(doc_db_path, doc_number="P-123")
        doc_id, dup = _add(doc_db_path, title="同一份", doc_number="P-123", force=True)
        assert doc_id > 0 and dup is None

    def test_same_file_hash_blocked(self, doc_db_path, tmp_path):
        f = tmp_path / "lease.jpg"
        f.write_bytes(b"identical bytes")
        _add(doc_db_path, file_path=str(f))
        doc_id, dup = _add(doc_db_path, title="重发同图", file_path=str(f))
        assert doc_id == 0 and dup is not None

    def test_superseded_not_counted_as_dup(self, doc_db_path):
        old_id, _ = _add(doc_db_path, doc_number="P-123")
        doc_db.update_document(old_id, status="superseded", db_path=doc_db_path)
        doc_id, dup = _add(doc_db_path, title="续约新合同", doc_number="P-123")
        assert doc_id > 0 and dup is None
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: all PASSED. If any duplicate test fails, fix `find_duplicate` in `doc_db.py` (not the test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_document_keeper.py
git commit -m "test: document duplicate detection by number and file hash"
```

---

### Task 5: doc_db.py — update / ack / remove / due tests

(Implementation exists from Task 3; tests lock the reminder-critical behavior.)

**Files:**
- Test: `tests/test_document_keeper.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_document_keeper.py`:

```python
class TestUpdateAckRemove:
    def test_update_fields(self, doc_db_path):
        doc_id, _ = _add(doc_db_path)
        assert doc_db.update_document(doc_id, issuer="新房东", db_path=doc_db_path)
        assert doc_db.get_document(doc_id, db_path=doc_db_path)["issuer"] == "新房东"

    def test_update_unknown_field_rejected(self, doc_db_path):
        doc_id, _ = _add(doc_db_path)
        with pytest.raises(ValueError):
            doc_db.update_document(doc_id, created_at="2020-01-01", db_path=doc_db_path)

    def test_ack_then_expiry_change_resets_ack(self, doc_db_path):
        doc_id, _ = _add(doc_db_path, expiry_date="2026-07-01")
        assert doc_db.ack_document(doc_id, db_path=doc_db_path)
        assert doc_db.get_document(doc_id, db_path=doc_db_path)["acknowledged"] == 1
        doc_db.update_document(doc_id, expiry_date="2027-07-01", db_path=doc_db_path)
        assert doc_db.get_document(doc_id, db_path=doc_db_path)["acknowledged"] == 0

    def test_remove_keeps_file_by_default(self, doc_db_path, tmp_path):
        f = tmp_path / "doc.jpg"
        f.write_bytes(b"x")
        doc_id, _ = _add(doc_db_path, file_path=str(f))
        assert doc_db.remove_document(doc_id, db_path=doc_db_path)
        assert f.exists()
        assert doc_db.get_document(doc_id, db_path=doc_db_path) is None

    def test_remove_with_delete_file(self, doc_db_path, tmp_path):
        f = tmp_path / "doc.jpg"
        f.write_bytes(b"x")
        doc_id, _ = _add(doc_db_path, file_path=str(f))
        assert doc_db.remove_document(doc_id, delete_file=True, db_path=doc_db_path)
        assert not f.exists()


class TestDue:
    TODAY = "2026-06-11"

    def test_window_and_days_left(self, doc_db_path):
        _add(doc_db_path, title="月内到期", expiry_date="2026-07-01")   # 20 天后，<30 默认窗口
        _add(doc_db_path, title="远期", expiry_date="2026-12-31")       # 窗口外
        _add(doc_db_path, title="已过期", expiry_date="2026-06-01")
        due = doc_db.due_documents(today=self.TODAY, db_path=doc_db_path)
        titles = [d["title"] for d in due]
        assert "月内到期" in titles and "已过期" in titles and "远期" not in titles
        by_title = {d["title"]: d for d in due}
        assert by_title["月内到期"]["days_left"] == 20
        assert by_title["已过期"]["days_left"] == -10

    def test_per_doc_remind_days_override(self, doc_db_path):
        _add(doc_db_path, title="提前90天", expiry_date="2026-09-01", remind_days=90)
        _add(doc_db_path, title="默认窗口", expiry_date="2026-09-01")
        titles = [d["title"] for d in doc_db.due_documents(today=self.TODAY, db_path=doc_db_path)]
        assert titles == ["提前90天"]

    def test_explicit_days_param_wins(self, doc_db_path):
        _add(doc_db_path, title="远期", expiry_date="2026-12-31", remind_days=5)
        titles = [d["title"] for d in doc_db.due_documents(days=365, today=self.TODAY, db_path=doc_db_path)]
        assert titles == ["远期"]

    def test_unacknowledged_sorted_first(self, doc_db_path):
        a, _ = _add(doc_db_path, title="已确认", expiry_date="2026-06-15")
        _add(doc_db_path, title="未确认", expiry_date="2026-06-20")
        doc_db.ack_document(a, db_path=doc_db_path)
        titles = [d["title"] for d in doc_db.due_documents(today=self.TODAY, db_path=doc_db_path)]
        assert titles == ["未确认", "已确认"]

    def test_non_active_excluded(self, doc_db_path):
        doc_id, _ = _add(doc_db_path, title="已归档", expiry_date="2026-06-15")
        doc_db.update_document(doc_id, status="archived", db_path=doc_db_path)
        assert doc_db.due_documents(today=self.TODAY, db_path=doc_db_path) == []
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: all PASSED. Fix `doc_db.py` if not (tests encode the spec).

- [ ] **Step 3: Run the full suite (regression)**

Run: `python -m pytest tests/ -v`
Expected: all PASSED, existing tests untouched.

- [ ] **Step 4: Commit**

```bash
git add tests/test_document_keeper.py
git commit -m "test: due-date windows, ack reset, remove semantics"
```

---

### Task 6: cli.py — Document Keeper command-line entry

**Files:**
- Create: `.codewhale/skills/Document_Keeper/cli.py`
- Test: `tests/test_document_keeper.py`

- [ ] **Step 1: Write the failing tests (subprocess, like a real caller)**

Append to `tests/test_document_keeper.py`:

```python
import subprocess
import sys as _sys
from pathlib import Path as _Path

_CLI = str(_Path(__file__).resolve().parent.parent
           / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")


def _run_cli(*args):
    return subprocess.run(
        [_sys.executable, _CLI, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class TestCli:
    def test_doc_add_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        r = _run_cli("doc-add", "--type", "lease", "--title", "测试租约",
                     "--expiry", "2027-01-01")
        assert r.returncode == 0, r.stderr
        assert "#1" in r.stdout
        r = _run_cli("doc-list")
        assert r.returncode == 0
        assert "测试租约" in r.stdout

    def test_doc_add_bad_type_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        r = _run_cli("doc-add", "--type", "nope", "--title", "x")
        assert r.returncode == 1
        assert "无效文档类型" in r.stderr

    def test_doc_add_bad_date_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        r = _run_cli("doc-add", "--type", "lease", "--title", "x",
                     "--expiry", "01/01/2027")
        assert r.returncode == 1

    def test_doc_due_and_ack(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        _run_cli("doc-add", "--type", "lease", "--title", "快到期",
                 "--expiry", "2026-06-20")
        r = _run_cli("doc-due")
        assert "快到期" in r.stdout
        r = _run_cli("doc-ack", "--id", "1")
        assert r.returncode == 0
        r = _run_cli("doc-show", "--id", "1")
        assert "已确认" in r.stdout
```

Note: `monkeypatch.setenv` affects the subprocess because `subprocess.run` inherits the test process environment. `DOC_KEEPER_DB` is a test-only escape hatch so CLI tests never touch the real ledger — the CLI reads it as an optional `db_path` override.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_document_keeper.py::TestCli -v`
Expected: FAIL — CLI file does not exist, subprocess returncode 2.

- [ ] **Step 3: Write cli.py**

Create `.codewhale/skills/Document_Keeper/cli.py`:

```python
"""
Family Assistant — Document Keeper CLI

家庭重要文档归档/检索/到期提醒。Agent 经白名单子命令调用，输出纯文本。
用法: python .codewhale/skills/Document_Keeper/cli.py <command> [args]

测试钩子：环境变量 DOC_KEEPER_DB 可覆盖数据库路径（仅测试用）。
"""

import argparse
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 把本 skill 目录加入 sys.path（同目录 doc_db / doc_models）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 成员注册表（Agent_Runtime skill；跨 skill 经 sys.path，与 Expense_Tracker 同模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import members as members_registry

import doc_db
from doc_models import DOC_TYPES, DOC_STATUSES, DOCUMENTS_DIR, REMINDER_LEAD_DAYS

ROOT = Path(__file__).resolve().parents[3]

# 测试钩子：覆盖数据库路径，避免测试碰真实账本
_DB_OVERRIDE = os.environ.get("DOC_KEEPER_DB") or None


def _validate_member(name: str) -> str:
    """非空成员名必须已登记；返回原值或抛 ValueError。空值放行（家庭级）。"""
    if not name:
        return ""
    known = members_registry.member_names()
    if name not in known:
        raise ValueError(
            f"未知成员 '{name}'。已登记: {', '.join(known) or '（无）'}。用 member-add 添加。")
    return name


def _store_file(src: str, doc_type: str, title: str) -> str:
    """复制文件到 documents/<doc_type>/，返回相对项目根的路径（正斜杠）。

    已在文档目录内的文件不复制，原样返回相对路径。
    """
    p = Path(src)
    abs_p = (p if p.is_absolute() else ROOT / p).resolve()
    if not abs_p.exists():
        raise ValueError(f"文件不存在: {src}")
    docs_root = DOCUMENTS_DIR.resolve()
    if abs_p.is_relative_to(docs_root):
        return abs_p.relative_to(ROOT.resolve()).as_posix()
    safe_title = re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:40] or "untitled"
    dest_dir = docs_root / doc_type
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today().isoformat()}_{doc_type}_{safe_title}"
    dest = dest_dir / f"{stem}{abs_p.suffix.lower()}"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{stem}_{n}{abs_p.suffix.lower()}"
        n += 1
    shutil.copy2(abs_p, dest)
    return dest.relative_to(ROOT.resolve()).as_posix()


def _fmt_due(d: dict) -> str:
    left = d["days_left"]
    when = f"已过期 {-left} 天" if left < 0 else f"{left} 天后到期（{d['expiry_date']}）"
    ack = "" if not d["acknowledged"] else " [已确认]"
    note = f" — {d['action_note']}" if d["action_note"] else ""
    who = f" [{d['member']}]" if d["member"] else ""
    return f"#{d['id']} {d['title']}（{d['doc_type']}）{who} {when}{ack}{note}"


def cmd_doc_add(args):
    file_rel = ""
    if args.file:
        file_rel = _store_file(args.file, args.type, args.title)
    doc_id, dup = doc_db.add_document(
        doc_type=args.type,
        title=args.title,
        member=_validate_member(args.member or ""),
        issuer=args.issuer or "",
        doc_number=args.number or "",
        issue_date=args.issue_date or "",
        expiry_date=args.expiry or "",
        action_note=args.action_note or "",
        remind_days=args.remind_days,
        file_path=file_rel,
        ocr_text=args.ocr_text or "",
        notes=args.notes or "",
        force=args.force,
        db_path=_DB_OVERRIDE,
    )
    if dup:
        print(f"⚠ 疑似重复！已存在 #{dup['id']} {dup['title']}（{dup['doc_type']}，"
              f"编号 {dup['doc_number'] or '无'}）。")
        print("未写入。如确认不是重复，请加 --force 强制写入。")
        return
    expiry = f"，到期 {args.expiry}" if args.expiry else ""
    saved = f"，文件 {file_rel}" if file_rel else ""
    print(f"已归档文档 #{doc_id}: {args.title}（{args.type}）{expiry}{saved}")


def cmd_doc_list(args):
    rows = doc_db.get_documents(
        doc_type=args.type, member=args.member, keyword=args.keyword,
        status=args.status, limit=args.limit or 200, db_path=_DB_OVERRIDE,
    )
    if not rows:
        print("没有找到文档。")
        return
    for r in rows:
        who = f" [{r['member']}]" if r["member"] else ""
        expiry = f" 到期 {r['expiry_date']}" if r["expiry_date"] else " 长期有效"
        print(f"#{r['id']} [{r['status']}] {r['title']}（{r['doc_type']}）{who}"
              f" | {r['issuer'] or '-'} | 编号 {r['doc_number'] or '-'} |{expiry}")


def cmd_doc_show(args):
    d = doc_db.get_document(args.id, db_path=_DB_OVERRIDE)
    if d is None:
        print(f"未找到文档 #{args.id}")
        return
    print(f"#{d['id']} {d['title']}（{d['doc_type']}）[{d['status']}]")
    print(f"成员: {d['member'] or '家庭'} | 签发方: {d['issuer'] or '-'} | 编号: {d['doc_number'] or '-'}")
    print(f"签发: {d['issue_date'] or '-'} | 到期: {d['expiry_date'] or '长期有效'}"
          f" | 提醒提前: {d['remind_days'] if d['remind_days'] is not None else REMINDER_LEAD_DAYS} 天"
          f" | 提醒{'已确认' if d['acknowledged'] else '未确认'}")
    if d["action_note"]:
        print(f"到期动作: {d['action_note']}")
    if d["file_path"]:
        print(f"文件: {d['file_path']}")
    if d["notes"]:
        print(f"备注: {d['notes']}")
    if d["ocr_text"]:
        excerpt = d["ocr_text"][:300]
        print(f"OCR 摘录: {excerpt}{'…' if len(d['ocr_text']) > 300 else ''}")


def cmd_doc_due(args):
    rows = doc_db.due_documents(days=args.days, db_path=_DB_OVERRIDE)
    if not rows:
        print("没有即将到期的文档。")
        return
    for d in rows:
        print(_fmt_due(d))


def cmd_doc_update(args):
    fields = {}
    mapping = {
        "type": "doc_type", "title": "title", "issuer": "issuer",
        "number": "doc_number", "issue_date": "issue_date", "expiry": "expiry_date",
        "action_note": "action_note", "remind_days": "remind_days",
        "status": "status", "notes": "notes",
    }
    for arg_name, col in mapping.items():
        v = getattr(args, arg_name)
        if v is not None:
            fields[col] = v
    if args.member is not None:
        fields["member"] = _validate_member(args.member)
    ok = doc_db.update_document(args.id, db_path=_DB_OVERRIDE, **fields)
    print(f"{'已更新' if ok else '未找到'} 文档 #{args.id}")


def cmd_doc_ack(args):
    ok = doc_db.ack_document(args.id, db_path=_DB_OVERRIDE)
    print(f"{'已确认提醒' if ok else '未找到'} 文档 #{args.id}")


def cmd_doc_remove(args):
    ok = doc_db.remove_document(args.id, delete_file=args.delete_file, db_path=_DB_OVERRIDE)
    extra = "（含原始文件）" if ok and args.delete_file else ""
    print(f"{'已删除' if ok else '未找到'} 文档 #{args.id}{extra}")


def main():
    parser = argparse.ArgumentParser(description="Family Assistant — Document Keeper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doc-add", help="归档一份文档")
    p.add_argument("--type", required=True, choices=DOC_TYPES)
    p.add_argument("--title", required=True)
    p.add_argument("--member", help="归属成员（须已登记；空 = 家庭级）")
    p.add_argument("--issuer", help="签发方：房东/保险公司/政府机构")
    p.add_argument("--number", help="编号：保单号/证件号")
    p.add_argument("--issue-date", help="签发日期 YYYY-MM-DD")
    p.add_argument("--expiry", help="到期日期 YYYY-MM-DD；长期有效不填")
    p.add_argument("--action-note", help="到期要做什么，如 提前60天通知房东")
    p.add_argument("--remind-days", type=int, help=f"提前几天提醒（默认 {REMINDER_LEAD_DAYS}）")
    p.add_argument("--file", help="原始文件路径；自动复制到文档目录")
    p.add_argument("--ocr-text", help="OCR 全文，用于关键词检索")
    p.add_argument("--notes")
    p.add_argument("--force", action="store_true", help="跳过重复检查，强制写入")

    p = sub.add_parser("doc-list", help="查询文档")
    p.add_argument("--type", choices=DOC_TYPES)
    p.add_argument("--member")
    p.add_argument("--keyword", help="匹配 标题/全文/备注")
    p.add_argument("--status", choices=list(DOC_STATUSES))
    p.add_argument("--limit", type=int)

    p = sub.add_parser("doc-show", help="查看文档详情")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("doc-due", help="即将到期/已过期的文档")
    p.add_argument("--days", type=int, help="查看几天内到期（默认按各文档提前量）")

    p = sub.add_parser("doc-update", help="更新文档（续约改到期日、归档等）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--type", choices=DOC_TYPES)
    p.add_argument("--title")
    p.add_argument("--member")
    p.add_argument("--issuer")
    p.add_argument("--number")
    p.add_argument("--issue-date")
    p.add_argument("--expiry", help="新到期日（会重新进入提醒）")
    p.add_argument("--action-note")
    p.add_argument("--remind-days", type=int)
    p.add_argument("--status", choices=list(DOC_STATUSES))
    p.add_argument("--notes")

    p = sub.add_parser("doc-ack", help="确认到期提醒（每日推送跳过）")
    p.add_argument("--id", type=int, required=True)

    # 仅本机使用；不在 wechat.allowed_commands 白名单内，Agent 调不到
    p = sub.add_parser("doc-remove", help="删除文档记录（仅本机）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--delete-file", action="store_true", help="同时删除原始文件")

    args = parser.parse_args()

    dispatch = {
        "doc-add": cmd_doc_add,
        "doc-list": cmd_doc_list,
        "doc-show": cmd_doc_show,
        "doc-due": cmd_doc_due,
        "doc-update": cmd_doc_update,
        "doc-ack": cmd_doc_ack,
        "doc-remove": cmd_doc_remove,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: all PASSED.

Caveat to verify while running: `doc_db.add_document` / `due_documents` must receive the `DOC_KEEPER_DB` path before the table exists — `doc-add` is the first call in each test and `init_db` has not run on that fresh file. Fix by calling `doc_db.init_db(db_path=_DB_OVERRIDE)` once at the top of `main()`:

```python
    doc_db.init_db(db_path=_DB_OVERRIDE)
```

(insert as the first line of `main()` — idempotent, also covers the real ledger).

- [ ] **Step 5: Manual smoke test against real config (no write to real DB)**

Run: `python .codewhale/skills/Document_Keeper/cli.py doc-list` with env `DOC_KEEPER_DB` pointing at a temp file, e.g. PowerShell:
`$env:DOC_KEEPER_DB = "$env:TEMP\dk-smoke.db"; python .codewhale/skills/Document_Keeper/cli.py doc-list; Remove-Item env:DOC_KEEPER_DB`
Expected: `没有找到文档。`

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Document_Keeper/cli.py tests/test_document_keeper.py
git commit -m "feat: document keeper CLI with file archiving and member validation"
```

---

### Task 7: reminder.py — daily push

**Files:**
- Create: `.codewhale/skills/Document_Keeper/reminder.py`
- Test: `tests/test_document_keeper.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_document_keeper.py`:

```python
import reminder


@pytest.fixture
def reminder_env(doc_db_path, tmp_path, monkeypatch):
    """Isolated state file + fake member registry + temp db."""
    monkeypatch.setattr(reminder, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(reminder, "load_members",
                        lambda: {"爸爸": {"telegram": ["111", "222"], "wechat": ["w1"]}})
    return doc_db_path


class TestReminder:
    def test_due_message_lists_unacknowledged_only(self, reminder_env):
        _add(reminder_env, title="快到期", expiry_date=date.today().isoformat())
        acked, _ = _add(reminder_env, title="已确认", expiry_date=date.today().isoformat())
        doc_db.ack_document(acked, db_path=reminder_env)
        msg = reminder.due_message(db_path=reminder_env)
        assert "快到期" in msg and "已确认" not in msg

    def test_due_message_none_when_nothing(self, reminder_env):
        assert reminder.due_message(db_path=reminder_env) is None

    def test_push_once_per_day_per_channel(self, reminder_env):
        _add(reminder_env, title="快到期", expiry_date=date.today().isoformat())
        sent = []
        ok = reminder.check_and_push(lambda cid, text: sent.append(cid),
                                     "telegram", db_path=reminder_env)
        assert ok and sent == ["111", "222"]
        sent.clear()
        ok = reminder.check_and_push(lambda cid, text: sent.append(cid),
                                     "telegram", db_path=reminder_env)
        assert not ok and sent == []        # 同日不再推
        ok = reminder.check_and_push(lambda cid, text: sent.append(cid),
                                     "wechat", db_path=reminder_env)
        assert ok and sent == ["w1"]        # 不同频道独立状态

    def test_push_failure_keeps_state_for_retry(self, reminder_env):
        _add(reminder_env, title="快到期", expiry_date=date.today().isoformat())

        def boom(cid, text):
            raise RuntimeError("network down")

        assert not reminder.check_and_push(boom, "telegram", db_path=reminder_env)
        sent = []
        assert reminder.check_and_push(lambda cid, text: sent.append(cid),
                                       "telegram", db_path=reminder_env)
        assert sent == ["111", "222"]       # 失败未记状态，下一轮重试成功

    def test_nothing_due_marks_day_done(self, reminder_env):
        calls = []
        assert not reminder.check_and_push(lambda cid, text: calls.append(cid),
                                           "telegram", db_path=reminder_env)
        assert calls == []
        assert reminder.STATE_FILE.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_document_keeper.py::TestReminder -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reminder'`

- [ ] **Step 3: Write reminder.py**

Create `.codewhale/skills/Document_Keeper/reminder.py`:

```python
"""
Document Keeper — 每日到期提醒

传输层在轮询循环里反复调 check_and_push(send_fn, channel)：
每频道每天最多推送一次，有到期未确认文档才推。无新进程、无定时器。
状态存 data/.doc_reminder_state（JSON：{频道: 最后运行日期}）；
推送失败不记状态，下一轮自动重试。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))                                   # 同目录 doc_db
sys.path.insert(0, str(HERE.parent / "Agent_Runtime"))          # 成员注册表

import doc_db
from members import load_members

STATE_FILE = ROOT / "data" / ".doc_reminder_state"


def due_message(db_path: str | None = None) -> str | None:
    """到期未确认文档的提醒文本；没有则返回 None。"""
    docs = [d for d in doc_db.due_documents(db_path=db_path) if not d["acknowledged"]]
    if not docs:
        return None
    lines = ["📋 文档到期提醒："]
    for d in docs:
        left = d["days_left"]
        when = f"已过期 {-left} 天" if left < 0 else f"{left} 天后到期（{d['expiry_date']}）"
        line = f"#{d['id']} {d['title']}（{d['doc_type']}）{when}"
        if d["action_note"]:
            line += f" — {d['action_note']}"
        lines.append(line)
    lines.append("处理完成后说\"确认 #编号\"即可不再重复提醒。")
    return "\n".join(lines)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def check_and_push(send_fn, channel: str, db_path: str | None = None) -> bool:
    """每频道每日一次：有到期未确认文档则推送给该频道所有已登记 id。

    send_fn(channel_id, text)；抛异常视为失败（不记状态，下一轮重试）。
    返回本次是否推送了消息。
    """
    today = date.today().isoformat()
    state = _load_state()
    if state.get(channel) == today:
        return False
    msg = due_message(db_path=db_path)
    if msg is None:
        state[channel] = today
        _save_state(state)
        return False
    ids = [cid for bindings in load_members().values()
           for cid in (bindings.get(channel) or [])]
    try:
        for cid in ids:
            send_fn(cid, msg)
    except Exception as e:
        print(f"[doc-reminder] 推送失败({channel}): {e}", file=sys.stderr)
        return False
    state[channel] = today
    _save_state(state)
    return bool(ids)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_document_keeper.py -v`
Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Document_Keeper/reminder.py tests/test_document_keeper.py
git commit -m "feat: daily document due-date reminder with per-channel state"
```

---

### Task 8: agent_core.py — tools, routing, prompt, OCR dirs, handle_image

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_document_keeper.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_document_keeper.py`:

```python
import agent_core


class TestAgentIntegration:
    def test_doc_commands_route_to_document_keeper_cli(self):
        p = agent_core._cli_path("doc-add")
        assert p.parts[-2] == "Document_Keeper"
        p = agent_core._cli_path("add")
        assert p.parts[-2] == "Expense_Tracker"

    def test_doc_commands_whitelisted_except_remove(self):
        for cmd in ("doc-add", "doc-list", "doc-show", "doc-due", "doc-update", "doc-ack"):
            assert cmd in agent_core.ALLOWED_COMMANDS
        assert "doc-remove" not in agent_core.ALLOWED_COMMANDS

    def test_add_document_is_member_write_tool(self):
        out = agent_core._apply_member("add_document",
                                       {"type": "lease", "member": "假冒"}, "爸爸")
        assert out["member"] == "爸爸"

    def test_document_tools_registered(self):
        for name in ("add_document", "list_documents", "show_document",
                     "due_documents", "update_document", "ack_document"):
            assert name in agent_core._TOOL_MAP
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert "add_document" in schema_names and "due_documents" in schema_names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_document_keeper.py::TestAgentIntegration -v`
Expected: FAIL — `agent_core` has no `_cli_path`, doc tools missing.

- [ ] **Step 3: Modify agent_core.py**

3a. After the `RECEIPTS_DIR = ...` line (`agent_core.py:66`), add:

```python
# 文档目录（config.json documents_dir，缺失回退 documents）
DOCUMENTS_DIR = ROOT / (_CONFIG.get("documents_dir") or "documents")
```

3b. After the `ALLOWED_COMMANDS = ...` line (`agent_core.py:82`), add:

```python
# doc-* 命令属于 Document_Keeper skill，其余走 Expense_Tracker
_DOC_COMMANDS = {"doc-add", "doc-list", "doc-show", "doc-due",
                 "doc-update", "doc-ack", "doc-remove"}


def _cli_path(cmd: str) -> Path:
    """子命令 → 所属 skill 的 CLI 路径。"""
    skill = "Document_Keeper" if cmd in _DOC_COMMANDS else "Expense_Tracker"
    return ROOT / ".codewhale" / "skills" / skill / "cli.py"
```

3c. In `_run_cli`, replace the hardcoded path line

```python
    cli_path = ROOT / ".codewhale" / "skills" / "Expense_Tracker" / "cli.py"
```

with:

```python
    cli_path = _cli_path(cmd)
```

3d. In `_tool_ocr_image`, replace the receipts-only directory check

```python
        if not resolved.is_relative_to(RECEIPTS_DIR.resolve()):
            return f"[错误] 只允许识别票据目录内的图片: {RECEIPTS_DIR}"
```

with:

```python
        allowed = (RECEIPTS_DIR.resolve(), DOCUMENTS_DIR.resolve())
        if not any(resolved.is_relative_to(d) for d in allowed):
            return f"[错误] 只允许识别票据/文档目录内的图片: {RECEIPTS_DIR} 或 {DOCUMENTS_DIR}"
```

3e. After the `def _tool_delete_transaction...` line, add the document tool executors:

```python
def _tool_add_document(args): return _run_cli("doc-add", args)
def _tool_list_documents(args): return _run_cli("doc-list", args)
def _tool_show_document(args): return _run_cli("doc-show", args)
def _tool_due_documents(args): return _run_cli("doc-due", args)
def _tool_update_document(args): return _run_cli("doc-update", args)
def _tool_ack_document(args): return _run_cli("doc-ack", args)
```

3f. Extend `_TOOL_MAP` with:

```python
    "add_document": _tool_add_document,
    "list_documents": _tool_list_documents,
    "show_document": _tool_show_document,
    "due_documents": _tool_due_documents,
    "update_document": _tool_update_document,
    "ack_document": _tool_ack_document,
```

3g. Change `_MEMBER_WRITE_TOOLS` to:

```python
_MEMBER_WRITE_TOOLS = {"add_transaction", "add_deposit", "add_transfer", "add_tax",
                       "add_document"}
```

3h. Near the other config-derived enums (after `_CATS_DESC = ...`), add:

```python
_DOC_TYPES = list(_CONFIG.get("doc_types") or ["other"])
_DOC_STATUSES = ["active", "expired", "archived", "superseded"]
```

3i. Append to `TOOL_SCHEMAS` (before the closing `]`):

```python
    _fn("add_document", "归档一份家庭重要文档（合同/保单/证件等），登记到期日以便提醒", {
        "type": _s("文档类型", enum=_DOC_TYPES),
        "title": _s("文档名称，如 2026公寓租约"),
        "issuer": _s("签发方：房东/保险公司/政府机构"),
        "number": _s("编号：保单号/证件号"),
        "issue-date": _s("签发日期 YYYY-MM-DD"),
        "expiry": _s("到期日期 YYYY-MM-DD；长期有效不填"),
        "action-note": _s("到期要做什么，如 提前60天通知房东"),
        "remind-days": _int("提前几天提醒（不填用默认值）"),
        "file": _s("原始文件路径（图片已保存的路径）"),
        "ocr-text": _s("OCR 识别全文，用于日后关键词检索"),
        "notes": _s("备注"),
        "force": {"type": "boolean", "description": "跳过重复检查强制写入（仅在用户确认非重复后用）"},
    }, ["type", "title"]),
    _fn("list_documents", "查询已归档的家庭文档", {
        "type": _s("文档类型", enum=_DOC_TYPES),
        "member": _s("按成员过滤"),
        "keyword": _s("关键词，匹配标题/OCR全文/备注"),
        "status": _s("状态（默认隐藏 archived/superseded）", enum=_DOC_STATUSES),
    }),
    _fn("show_document", "查看某文档完整信息（含文件路径）", {
        "id": _int("文档 id"),
    }, ["id"]),
    _fn("due_documents", "查询即将到期/已过期的文档", {
        "days": _int("查看几天内到期（不填按各文档默认提前量）"),
    }),
    _fn("update_document", "更新文档信息（续约改到期日、改状态归档等）", {
        "id": _int("文档 id"),
        "title": _s("文档名称"),
        "issuer": _s("签发方"),
        "number": _s("编号"),
        "issue-date": _s("签发日期 YYYY-MM-DD"),
        "expiry": _s("新到期日 YYYY-MM-DD（改后重新进入提醒）"),
        "action-note": _s("到期要做什么"),
        "remind-days": _int("提前几天提醒"),
        "status": _s("状态", enum=_DOC_STATUSES),
        "notes": _s("备注"),
    }, ["id"]),
    _fn("ack_document", "确认某文档的到期提醒（之后不再每日重复提醒）", {
        "id": _int("文档 id"),
    }, ["id"]),
```

3j. In `_build_system_prompt`, add `doc_types = "/".join(_DOC_TYPES)` next to the other locals, and insert a section between `## 记账合法值…` block and `## 行为准则`:

```python
## 文档管理（家庭重要文档归档与到期提醒）
- 文档类型: {doc_types}
- 用户发来 合同/保单/证件 等重要文档，或说"存一下这个文件"→ add_document（尽量带 expiry 到期日和 action-note 到期动作）
- 用户问"租约什么时候到期""我们有哪些保险""找一下XX保单"→ list_documents / show_document
- 用户问"有什么要到期的""最近有什么要办的"→ due_documents
- 用户说"续约了""换新证了"→ update_document 改到期日；旧文档另存时把旧的 status 改 superseded
- 用户说"知道了""别再提醒"→ ack_document
```

(remember the surrounding string is an f-string — `{doc_types}` interpolates).

3k. In `handle_image`, replace the prompt construction

```python
                prompt = (
                    f"用户发了一张票据图片，OCR结果:\n{ocr_text}\n"
                    f"提取金额/日期/类别帮用户记账。如果不完整，告知需要什么。"
                )
```

with:

```python
                prompt = (
                    f"用户发了一张图片，已保存为 {image_path}，OCR结果:\n{ocr_text}\n"
                    f"若是消费票据：提取金额/日期/类别帮用户记账（add_transaction）。\n"
                    f"若内容或用户语境表明是重要文档（合同/保单/证件）：用 add_document 归档，"
                    f"file 参数传上面的保存路径，ocr-text 传 OCR 全文。信息不完整就先问用户。"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: all PASSED (new integration tests + every existing test).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_document_keeper.py
git commit -m "feat: agent document tools, CLI routing, OCR dir allowance"
```

---

### Task 9: Transports — daily reminder hook

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py`
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py`

- [ ] **Step 1: Telegram — check once per poll iteration**

In `telegram_bot.py`, after the `from members import resolve` import line, add:

```python
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Document_Keeper"))
from reminder import check_and_push as _doc_reminder_check
```

In `run()`, at the end of the `while True:` body, right after the `_save_offset(offset)` line, add:

```python
        # 文档到期提醒：每天最多推一次（reminder 内部按日去重）
        try:
            _doc_reminder_check(send_message, "telegram")
        except Exception as e:
            print(f"[tg] 文档提醒检查异常: {e}", file=sys.stderr)
```

- [ ] **Step 2: WeChat — daemon thread (bot.run() blocks)**

In `wechat_ilink.py`, after the `from members import resolve` import line, add:

```python
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Document_Keeper"))
from reminder import check_and_push as _doc_reminder_check
```

In `run_bot()`, just before the `bot.run()` call (inside the `try:` at the bottom of the function, place the thread start above the try), add:

```python
    # 文档到期提醒：后台线程每 10 分钟检查（reminder 内部按日去重，
    # weixin-ilink bot.run() 阻塞，无轮询循环可挂钩）
    import threading
    import time as _time

    def _reminder_loop():
        while True:
            try:
                _doc_reminder_check(lambda wxid, text: bot.send_text(wxid, text), "wechat")
            except Exception as e:
                print(f"[wx] 文档提醒检查异常: {e}", file=sys.stderr)
            _time.sleep(600)

    threading.Thread(target=_reminder_loop, daemon=True, name="doc-reminder").start()
```

- [ ] **Step 3: Verify both files still import cleanly**

Run: `python -c "import sys; sys.path.insert(0, '.codewhale/skills/Agent_Runtime'); import telegram_bot; print('tg ok')"`
Expected: `tg ok`

Run: `python -c "import sys; sys.path.insert(0, '.codewhale/skills/Agent_Runtime'); import wechat_ilink; print('wx ok')"`
Expected: `wx ok` (weixin_ilink import is inside `run_bot`, so the module imports without the package).

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Agent_Runtime/wechat_ilink.py
git commit -m "feat: daily document reminder push in telegram and wechat transports"
```

---

### Task 10: Documentation — SKILL.md, FamilyAssistant.md, README

**Files:**
- Create: `.codewhale/skills/Document_Keeper/SKILL.md`
- Modify: `FamilyAssistant.md`
- Modify: `README.md`

- [ ] **Step 1: Write SKILL.md**

Create `.codewhale/skills/Document_Keeper/SKILL.md`:

```markdown
# Document Keeper

> Family Assistant 的家庭文档管理 skill。归档重要/临时文档（租约、保险单、SIN、健康卡等），OCR 索引，跟踪到期日并提醒。

## 代码位置

实现就在本 skill 目录 `.codewhale/skills/Document_Keeper/`，自包含、零外部依赖（仅标准库 + SQLite）：

```
.codewhale/skills/Document_Keeper/
├── SKILL.md       ← 本文件
├── doc_models.py  ← 数据模型 / SCHEMA / 文档类型（读 config.json）
├── doc_db.py      ← SQLite CRUD & 到期查询（documents 表，建在共享 data/ledger.db）
├── cli.py         ← 命令行入口（user / agent / 任意调用方）
└── reminder.py    ← 每日到期提醒（传输层轮询时调用，按频道按日去重）
```

数据模块名带 `doc_` 前缀（不叫 models/db）：Expense_Tracker 已在共享进程占用这两个模块名。

文件存档在项目根 `documents/<类型>/`（config.json `documents_dir`），数据库共用 `data/ledger.db`。

## 数据模型

### documents — 文档表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| doc_type | TEXT | 类型（config.json `doc_types`：lease/insurance/health/id_document/other） |
| title | TEXT | 名称，如 "2026公寓租约" |
| member | TEXT | 归属成员（空 = 家庭级） |
| issuer | TEXT | 签发方（房东/保险公司/政府机构） |
| doc_number | TEXT | 编号（保单号/证件号） |
| issue_date / expiry_date | TEXT | 签发/到期 ISO 日期；长期有效则 expiry 为空 |
| action_note | TEXT | 到期要做什么（如 提前60天通知房东） |
| remind_days | INTEGER | 该文档提醒提前量；空用 config `reminder_lead_days` |
| acknowledged | INTEGER | 提醒已确认（到期日变更自动清零） |
| file_path | TEXT | 原始文件相对路径 `documents/<类型>/...` |
| ocr_text | TEXT | OCR 全文（关键词检索用） |
| data | TEXT(JSON) | 灵活字段（含 file_sha256 重复检测哈希） |
| status | TEXT | active / expired / archived / superseded |
| notes / created_at | TEXT | 备注 / 创建时间 |

## 接收文档（拍照 / 截图）

1. **存档原始文件** — `doc-add --file` 自动复制到 `documents/<类型>/YYYY-MM-DD_类型_标题.ext`
2. **OCR 提取** — 全文进 `ocr_text` 索引；DeepSeek 结构化提取 类型/标题/签发方/编号/日期
3. **写入记录** — `doc-add`，归属成员由代码注入（防冒名，与记账同规则）
4. **告知结果** — 回复提取出的到期日等关键信息，用户可用 `doc-update` 纠正

### 重复检测

同类型 + 同编号（无编号时同文件 SHA-256）→ 拦截。`--force` 强制写入。superseded 的旧文档不算重复。

## 到期提醒（双通道）

- **随问随查**：`doc-due [--days N]` — active 且 `到期日 − 提前量 ≤ 今天`（含已过期），未确认在前。提前量：`--days` > 文档 `remind_days` > config `reminder_lead_days`。
- **每日推送**：`reminder.check_and_push(send_fn, 频道)` 由传输层轮询调用，每频道每日最多一次，推给该频道全部已登记成员。状态存 `data/.doc_reminder_state`；推送失败不记状态、下轮重试。`doc-ack` 后该文档不再重复提醒，直到到期日更新。

## CLI 命令参考

```bash
# 归档（--file 自动复制进文档目录；--member 可选）
python .codewhale/skills/Document_Keeper/cli.py doc-add --type lease --title "2026公寓租约" \
  --issuer "房东张三" --number L-001 --issue-date 2026-03-01 --expiry 2027-02-28 \
  --action-note "提前60天通知房东" --file receipts/2026-06/xxx.jpg --ocr-text "..."

# 查询 / 详情
python .codewhale/skills/Document_Keeper/cli.py doc-list --type insurance --keyword 车险
python .codewhale/skills/Document_Keeper/cli.py doc-show --id 3

# 到期
python .codewhale/skills/Document_Keeper/cli.py doc-due
python .codewhale/skills/Document_Keeper/cli.py doc-due --days 90

# 更新（续约改到期日会重新进入提醒）/ 确认提醒
python .codewhale/skills/Document_Keeper/cli.py doc-update --id 3 --expiry 2028-02-28
python .codewhale/skills/Document_Keeper/cli.py doc-ack --id 3

# 删除（仅本机；Agent 白名单外）
python .codewhale/skills/Document_Keeper/cli.py doc-remove --id 3 --delete-file
```

## 查询模式

| 用户问法 | 操作 |
|---------|------|
| "存一下这份租约"（带图） | OCR → `doc-add --file <图> --ocr-text ...` |
| "租约什么时候到期" | `doc-list --type lease` |
| "我们有哪些保险" | `doc-list --type insurance` |
| "最近有什么要到期的" | `doc-due` |
| "续约了，新到期日X" | `doc-update --id N --expiry X` |
| "知道了别再提醒" | `doc-ack --id N` |

## 隐私

所有文档图片走腾讯云 OCR、提取文本走 DeepSeek（用户已知情选择）。原始文件与数据库均只存本机。

## 技能边界

覆盖：
- ✅ 文档归档 + OCR 全文索引
- ✅ 到期跟踪、按需查询 + 每日推送提醒
- ✅ 成员归属（与记账同防冒名机制）
- ✅ 重复检测（编号 / 文件哈希）

不覆盖：
- ❌ 文档版本对比（新版本另存一条，旧的标 superseded）
- ❌ 静态加密
- ❌ PDF 文字层解析（PDF 只存档，元数据手动填）
- ❌ 与文档无关的通用提醒
```

- [ ] **Step 2: Update FamilyAssistant.md**

In the `## 可用技能` table add a row after the OCR row:

```markdown
| **Document Keeper** | 家庭文档归档、OCR 索引、到期跟踪与每日提醒 | [SKILL.md](.codewhale/skills/Document_Keeper/SKILL.md) | 文档、合同、租约、保险单、证件、到期、提醒 |
```

In `## 加载策略` add:

```markdown
- 用户意图涉及文档归档/合同/保险/证件/到期提醒 → 加载 Document Keeper（如需图片识别，同时加载 OCR）
```

In `## 快速开始` code block, before the WeChat bot line add:

```bash
# 归档文档 / 查到期
python .codewhale/skills/Document_Keeper/cli.py doc-add --type lease --title "2026公寓租约" --expiry 2027-02-28
python .codewhale/skills/Document_Keeper/cli.py doc-due
```

In the `## 配置原则` table add rows:

```markdown
| `documents_dir` | `Document_Keeper/doc_models.py`（DOCUMENTS_DIR）、`agent_core.DOCUMENTS_DIR` |
| `doc_types` / `reminder_lead_days` | `Document_Keeper/doc_models.py`（读一次→常量）、`agent_core`（工具 enum） |
```

In `## 项目关键文件` add:

```markdown
- `.codewhale/skills/Document_Keeper/` — 文档管理 skill（cli.py 入口 + doc_db.py 数据层 + reminder.py 每日提醒）
```

- [ ] **Step 3: Update README.md directory tree**

In the tree under `Expense_Tracker/`, add after the OCR block:

```
│       ├── Document_Keeper/  ← 家庭文档管理技能
│       │   ├── SKILL.md
│       │   ├── doc_models.py     ← 数据模型
│       │   ├── doc_db.py         ← SQLite 数据层
│       │   ├── cli.py            ← 文档 CLI 入口
│       │   └── reminder.py       ← 每日到期提醒
```

And in the root listing, after the `└── receipts/` line, change the tree to include:

```
├── receipts/             ← 票据存档
└── documents/            ← 家庭文档存档（按类型子目录）
```

Also under 手机端 usage examples, extend the example sentence:

```markdown
发什么都可以，比如 `花了45块 午餐`、`这个月花了多少`，或发一张租约照片说 `存一下这份租约`。
到期前 Bot 会每天主动提醒（如 "租约 20 天后到期 — 提前60天通知房东"）。
```

- [ ] **Step 4: Final full suite + commit**

Run: `python -m pytest tests/ -v`
Expected: all PASSED.

```bash
git add .codewhale/skills/Document_Keeper/SKILL.md FamilyAssistant.md README.md
git commit -m "docs: document keeper skill doc, tree entry, loading strategy"
```

---

## Self-Review (completed)

- **Spec coverage:** schema §2 → Task 2/3; file storage §3 → Task 6 `_store_file`; ingestion §4 → Task 8 (handle_image + add_document tool); CLI §5 → Task 6; reminders §6 → Tasks 5/7/9 (state is per-channel JSON — refines the spec's single-date file, required because two transports may run simultaneously); config §7 → Task 1; agent integration §8 → Task 8; error handling §9 → Tasks 3/6/7 tests; testing §10 → Tasks 2–8.
- **Placeholder scan:** none; every code step has full code.
- **Type consistency:** CLI flags `--type/--number/--expiry` map to columns `doc_type/doc_number/expiry_date` via the `mapping` dict in `cmd_doc_update` and explicit kwargs in `cmd_doc_add`; tool schema parameter names match CLI flags (hyphenated, `_run_cli` converts).
