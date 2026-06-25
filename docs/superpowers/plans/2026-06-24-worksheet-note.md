# Worksheet Note Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-member, named "worksheet" notes (kv fact sheets + table logs) the assistant can update in place over time, on top of the existing Note_Keeper skill.

**Architecture:** A new `sheet_db.py` module in the Note_Keeper skill provides SQLite CRUD over two new tables (`worksheets`, `worksheet_rows`) in the existing per-member `notes.db`. Dynamic schema stored as JSON blobs. The existing `cli.py` gains `sheet-*` subcommands. `agent_core.py` exposes worksheet tools to the LLM, forces per-member isolation, and injects pinned worksheets into the system prompt.

**Tech Stack:** Python 3.12+ stdlib only (`sqlite3`, `json`, `argparse`), pytest.

## Global Constraints

- Stdlib only — no third-party deps in Note_Keeper (matches `note_db.py`).
- Per-member private store: db path via `Agent_Runtime/paths.member_store(member, "notes")` → `data/<member>/notes/notes.db`. Test override env var `NOTE_DB_PATH`.
- Every DB query MUST filter `WHERE member = ?`. No-leak: unknown/other-member item returns the same `False`/`None` as a missing one — never disclose existence.
- All `sheet_db` functions take an optional trailing `db_path=None` kwarg; default falls back to `note_db.DB_PATH` (single-db legacy), runtime injects per-member path.
- CLI: plain-text stdout; call `_mark_backup_dirty()` after every write; exit 1 + stderr `[错误] ...` on failure. Windows console: UTF-8 reconfigure block (copy from existing `cli.py`).
- Timestamps: `datetime.now().isoformat(timespec="seconds")`.
- `kind` ∈ `{"kv","table"}`. Title unique per member.

---

## File Structure

- Create: `.codewhale/skills/Note_Keeper/sheet_db.py` — worksheets + rows CRUD.
- Modify: `.codewhale/skills/Note_Keeper/cli.py` — add `sheet-*` subcommands + dispatch.
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` — whitelist, routing, tool handlers, tool schemas, member-forcing, context injection, system-prompt guidance.
- Modify: `.codewhale/skills/Note_Keeper/SKILL.md` — document worksheet feature.
- Create: `tests/test_worksheet.py` — sheet_db unit tests + CLI smoke + isolation.

---

### Task 1: sheet_db schema + create/get/list

**Files:**
- Create: `.codewhale/skills/Note_Keeper/sheet_db.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: nothing (new module).
- Produces:
  - `create_sheet(member, title, kind, pinned=False, db_path=None) -> int` (new sheet id; `ValueError` on empty title, bad kind, or duplicate `(member,title)`).
  - `get_sheet(member, title, db_path=None) -> dict | None` — keys: `id, member, title, kind, pinned, created_at, updated_at, kv_data` (parsed dict), `rows` (list of `{id, row_data(dict), created_at}` for table; `[]` for kv).
  - `list_sheets(member, db_path=None) -> list[dict]` — `{id, title, kind, pinned, size, updated_at}`, `size` = field count (kv) or row count (table), newest `updated_at` first.
  - `_connect(db_path=None) -> sqlite3.Connection` (WAL + idempotent schema).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worksheet.py — Note_Keeper worksheet (sheet_db) 测试
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Note_Keeper"
sys.path.insert(0, str(SKILL))
import sheet_db  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "notes.db")


def test_create_kv_and_get(db):
    sid = sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert isinstance(sid, int) and sid > 0
    s = sheet_db.get_sheet("爸爸", "房贷", db_path=db)
    assert s["title"] == "房贷" and s["kind"] == "kv"
    assert s["kv_data"] == {} and s["rows"] == []


def test_create_table_and_list(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    rows = sheet_db.list_sheets("爸爸", db_path=db)
    assert len(rows) == 1
    assert rows[0]["title"] == "血压" and rows[0]["kind"] == "table"
    assert rows[0]["size"] == 0


def test_duplicate_title_raises(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    with pytest.raises(ValueError):
        sheet_db.create_sheet("爸爸", "房贷", "table", db_path=db)


def test_create_bad_kind_raises(db):
    with pytest.raises(ValueError):
        sheet_db.create_sheet("爸爸", "x", "grid", db_path=db)


def test_get_missing_returns_none(db):
    assert sheet_db.get_sheet("爸爸", "无此表", db_path=db) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sheet_db'`.

- [ ] **Step 3: Write minimal implementation**

```python
# .codewhale/skills/Note_Keeper/sheet_db.py
"""
Family Assistant — Note Keeper 工作表（worksheet）数据库操作层。

命名工作表，按成员私有，两种 kind：
  - kv    : 事实清单，字段 set/unset/overwrite（kv_data JSON）
  - table : 流水/记录，行有动态列，按行 id 编辑/删除（worksheet_rows）

动态 schema 用 JSON 存储。所有查询强制按 member 过滤，实现按成员隔离。
与 notes 同库（data/<成员>/notes/notes.db），共享备份 scope。
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

# 兜底单库默认（仅未传 db_path 时）；运行时按成员注入 db_path。
_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ROOT / "data" / "ledger.db"

_KINDS = ("kv", "table")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worksheets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member     TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    kv_data    TEXT    NOT NULL DEFAULT '{}',
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    UNIQUE(member, title)
);
CREATE INDEX IF NOT EXISTS idx_worksheets_member ON worksheets(member);

CREATE TABLE IF NOT EXISTS worksheet_rows (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id   INTEGER NOT NULL,
    member     TEXT    NOT NULL,
    row_data   TEXT    NOT NULL DEFAULT '{}',
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worksheet_rows_sheet ON worksheet_rows(sheet_id);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or str(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _row_meta(member: str, title: str, conn) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM worksheets WHERE member = ? AND title = ?",
        (member, title),
    ).fetchone()


def create_sheet(member, title, kind, pinned=False, db_path=None) -> int:
    if not member or not member.strip():
        raise ValueError("member 不能为空")
    if not title or not title.strip():
        raise ValueError("title 不能为空")
    if kind not in _KINDS:
        raise ValueError(f"kind 必须是 {_KINDS}")
    conn = _connect(db_path)
    try:
        now = _now()
        cur = conn.execute(
            "INSERT INTO worksheets (member, title, kind, kv_data, pinned, created_at, updated_at) "
            "VALUES (?, ?, ?, '{}', ?, ?, ?)",
            (member.strip(), title.strip(), kind, 1 if pinned else 0, now, now),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"工作表已存在: {title}")
    finally:
        conn.close()


def get_sheet(member, title, db_path=None) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None:
            return None
        d = dict(meta)
        d["kv_data"] = json.loads(d["kv_data"] or "{}")
        d["pinned"] = bool(d["pinned"])
        rows = conn.execute(
            "SELECT id, row_data, created_at FROM worksheet_rows "
            "WHERE sheet_id = ? AND member = ? ORDER BY id",
            (d["id"], member),
        ).fetchall()
        d["rows"] = [
            {"id": r["id"], "row_data": json.loads(r["row_data"] or "{}"),
             "created_at": r["created_at"]}
            for r in rows
        ]
        return d
    finally:
        conn.close()


def list_sheets(member, db_path=None) -> list[dict]:
    conn = _connect(db_path)
    try:
        metas = conn.execute(
            "SELECT * FROM worksheets WHERE member = ? ORDER BY updated_at DESC, id DESC",
            (member,),
        ).fetchall()
        out = []
        for m in metas:
            if m["kind"] == "kv":
                size = len(json.loads(m["kv_data"] or "{}"))
            else:
                size = conn.execute(
                    "SELECT COUNT(*) AS c FROM worksheet_rows WHERE sheet_id = ?",
                    (m["id"],),
                ).fetchone()["c"]
            out.append({"id": m["id"], "title": m["title"], "kind": m["kind"],
                        "pinned": bool(m["pinned"]), "size": size,
                        "updated_at": m["updated_at"]})
        return out
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/sheet_db.py tests/test_worksheet.py
git commit -m "feat(notes): worksheet schema + create/get/list"
```

---

### Task 2: kv field mutation (set/unset)

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/sheet_db.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: `create_sheet`, `get_sheet` (Task 1).
- Produces:
  - `set_field(member, title, field, value, db_path=None) -> bool` — add/overwrite field on a kv sheet; bumps `updated_at`. False if no such kv sheet (missing, or sheet is a table).
  - `unset_field(member, title, field, db_path=None) -> bool` — remove field; False if sheet/field absent or sheet is a table.

- [ ] **Step 1: Write the failing test**

```python
def test_kv_set_overwrite_unset(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert sheet_db.set_field("爸爸", "房贷", "利率", "5.2%", db_path=db) is True
    assert sheet_db.set_field("爸爸", "房贷", "到期", "2027-03", db_path=db) is True
    s = sheet_db.get_sheet("爸爸", "房贷", db_path=db)
    assert s["kv_data"] == {"利率": "5.2%", "到期": "2027-03"}
    # overwrite
    assert sheet_db.set_field("爸爸", "房贷", "利率", "4.9%", db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "房贷", db_path=db)["kv_data"]["利率"] == "4.9%"
    # unset
    assert sheet_db.unset_field("爸爸", "房贷", "到期", db_path=db) is True
    assert "到期" not in sheet_db.get_sheet("爸爸", "房贷", db_path=db)["kv_data"]


def test_kv_unset_missing_field_false(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert sheet_db.unset_field("爸爸", "房贷", "无此字段", db_path=db) is False


def test_set_field_on_table_false(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    assert sheet_db.set_field("爸爸", "血压", "x", "1", db_path=db) is False


def test_set_field_missing_sheet_false(db):
    assert sheet_db.set_field("爸爸", "无表", "x", "1", db_path=db) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worksheet.py -k kv -v`
Expected: FAIL — `AttributeError: module 'sheet_db' has no attribute 'set_field'`.

- [ ] **Step 3: Write minimal implementation**

Append to `sheet_db.py`:

```python
def set_field(member, title, field, value, db_path=None) -> bool:
    if not field or not str(field).strip():
        raise ValueError("field 不能为空")
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None or meta["kind"] != "kv":
            return False
        data = json.loads(meta["kv_data"] or "{}")
        data[str(field).strip()] = value
        conn.execute(
            "UPDATE worksheets SET kv_data = ?, updated_at = ? WHERE id = ? AND member = ?",
            (json.dumps(data, ensure_ascii=False), _now(), meta["id"], member),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def unset_field(member, title, field, db_path=None) -> bool:
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None or meta["kind"] != "kv":
            return False
        data = json.loads(meta["kv_data"] or "{}")
        if str(field) not in data:
            return False
        del data[str(field)]
        conn.execute(
            "UPDATE worksheets SET kv_data = ?, updated_at = ? WHERE id = ? AND member = ?",
            (json.dumps(data, ensure_ascii=False), _now(), meta["id"], member),
        )
        conn.commit()
        return True
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: PASS (all prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/sheet_db.py tests/test_worksheet.py
git commit -m "feat(notes): worksheet kv set/unset field"
```

---

### Task 3: table row mutation (add/edit/delete)

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/sheet_db.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: `create_sheet`, `get_sheet` (Task 1).
- Produces:
  - `add_row(member, title, row_data: dict, db_path=None) -> int | None` — append row to a table sheet, return new row id; `None` if no such table sheet; `ValueError` if `row_data` not a dict.
  - `edit_row(member, title, row_id, row_data: dict, db_path=None) -> bool` — overwrite a row's data; False if row/sheet absent or not owned.
  - `delete_row(member, title, row_id, db_path=None) -> bool` — delete a row; False if absent/not owned.

- [ ] **Step 1: Write the failing test**

```python
def test_table_add_edit_delete_row(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    rid = sheet_db.add_row("爸爸", "血压", {"date": "06-24", "sys": 120}, db_path=db)
    assert isinstance(rid, int) and rid > 0
    # dynamic new column on a later row
    rid2 = sheet_db.add_row("爸爸", "血压", {"date": "06-25", "sys": 118, "note": "ok"}, db_path=db)
    s = sheet_db.get_sheet("爸爸", "血压", db_path=db)
    assert len(s["rows"]) == 2
    assert s["rows"][1]["row_data"]["note"] == "ok"
    # edit overwrites
    assert sheet_db.edit_row("爸爸", "血压", rid, {"date": "06-24", "sys": 125}, db_path=db) is True
    s = sheet_db.get_sheet("爸爸", "血压", db_path=db)
    assert s["rows"][0]["row_data"] == {"date": "06-24", "sys": 125}
    # delete
    assert sheet_db.delete_row("爸爸", "血压", rid2, db_path=db) is True
    assert len(sheet_db.get_sheet("爸爸", "血压", db_path=db)["rows"]) == 1


def test_add_row_on_kv_returns_none(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert sheet_db.add_row("爸爸", "房贷", {"x": 1}, db_path=db) is None


def test_add_row_bad_data_raises(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    with pytest.raises(ValueError):
        sheet_db.add_row("爸爸", "血压", "notadict", db_path=db)


def test_edit_delete_missing_row_false(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    assert sheet_db.edit_row("爸爸", "血压", 999, {"x": 1}, db_path=db) is False
    assert sheet_db.delete_row("爸爸", "血压", 999, db_path=db) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worksheet.py -k "row" -v`
Expected: FAIL — `AttributeError: ... 'add_row'`.

- [ ] **Step 3: Write minimal implementation**

Append to `sheet_db.py`:

```python
def _touch(conn, sheet_id, member):
    conn.execute("UPDATE worksheets SET updated_at = ? WHERE id = ? AND member = ?",
                 (_now(), sheet_id, member))


def add_row(member, title, row_data, db_path=None):
    if not isinstance(row_data, dict):
        raise ValueError("row_data 必须是 dict")
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None or meta["kind"] != "table":
            return None
        cur = conn.execute(
            "INSERT INTO worksheet_rows (sheet_id, member, row_data, created_at) "
            "VALUES (?, ?, ?, ?)",
            (meta["id"], member, json.dumps(row_data, ensure_ascii=False), _now()),
        )
        _touch(conn, meta["id"], member)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def edit_row(member, title, row_id, row_data, db_path=None) -> bool:
    if not isinstance(row_data, dict):
        raise ValueError("row_data 必须是 dict")
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None or meta["kind"] != "table":
            return False
        cur = conn.execute(
            "UPDATE worksheet_rows SET row_data = ? WHERE id = ? AND sheet_id = ? AND member = ?",
            (json.dumps(row_data, ensure_ascii=False), row_id, meta["id"], member),
        )
        if cur.rowcount == 0:
            return False
        _touch(conn, meta["id"], member)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_row(member, title, row_id, db_path=None) -> bool:
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None or meta["kind"] != "table":
            return False
        cur = conn.execute(
            "DELETE FROM worksheet_rows WHERE id = ? AND sheet_id = ? AND member = ?",
            (row_id, meta["id"], member),
        )
        if cur.rowcount == 0:
            return False
        _touch(conn, meta["id"], member)
        conn.commit()
        return True
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/sheet_db.py tests/test_worksheet.py
git commit -m "feat(notes): worksheet table row add/edit/delete"
```

---

### Task 4: rename, pin, delete, pinned_sheets + isolation

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/sheet_db.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: all prior.
- Produces:
  - `rename_sheet(member, title, new_title, db_path=None) -> bool` — False on missing or new-title clash.
  - `set_pinned(member, title, pinned, db_path=None) -> bool`.
  - `delete_sheet(member, title, db_path=None) -> bool` — cascades its rows.
  - `pinned_sheets(member, db_path=None) -> list[dict]` — full `get_sheet`-shaped dicts for all pinned sheets, `updated_at DESC`.

- [ ] **Step 1: Write the failing test**

```python
def test_rename_pin_delete(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    assert sheet_db.rename_sheet("爸爸", "血压", "血压记录", db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "血压", db_path=db) is None
    assert sheet_db.get_sheet("爸爸", "血压记录", db_path=db) is not None
    # pin
    assert sheet_db.set_pinned("爸爸", "血压记录", True, db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "血压记录", db_path=db)["pinned"] is True
    # delete cascades rows
    sheet_db.add_row("爸爸", "血压记录", {"sys": 120}, db_path=db)
    assert sheet_db.delete_sheet("爸爸", "血压记录", db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "血压记录", db_path=db) is None


def test_rename_clash_false(db):
    sheet_db.create_sheet("爸爸", "A", "kv", db_path=db)
    sheet_db.create_sheet("爸爸", "B", "kv", db_path=db)
    assert sheet_db.rename_sheet("爸爸", "A", "B", db_path=db) is False


def test_pinned_sheets_full_content(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", pinned=True, db_path=db)
    sheet_db.set_field("爸爸", "房贷", "利率", "5%", db_path=db)
    sheet_db.create_sheet("爸爸", "杂", "kv", db_path=db)  # not pinned
    pins = sheet_db.pinned_sheets("爸爸", db_path=db)
    assert len(pins) == 1 and pins[0]["title"] == "房贷"
    assert pins[0]["kv_data"] == {"利率": "5%"}


def test_member_isolation(db):
    sheet_db.create_sheet("爸爸", "私", "kv", db_path=db)
    sheet_db.set_field("爸爸", "私", "k", "v", db_path=db)
    # 妈妈 cannot see/touch 爸爸's sheet
    assert sheet_db.get_sheet("妈妈", "私", db_path=db) is None
    assert sheet_db.set_field("妈妈", "私", "k", "x", db_path=db) is False
    assert sheet_db.delete_sheet("妈妈", "私", db_path=db) is False
    assert sheet_db.list_sheets("妈妈", db_path=db) == []
    # 爸爸's data intact
    assert sheet_db.get_sheet("爸爸", "私", db_path=db)["kv_data"] == {"k": "v"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worksheet.py -k "rename or pinned or isolation" -v`
Expected: FAIL — `AttributeError: ... 'rename_sheet'`.

- [ ] **Step 3: Write minimal implementation**

Append to `sheet_db.py`:

```python
def rename_sheet(member, title, new_title, db_path=None) -> bool:
    if not new_title or not new_title.strip():
        raise ValueError("new_title 不能为空")
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None:
            return False
        clash = conn.execute(
            "SELECT 1 FROM worksheets WHERE member = ? AND title = ?",
            (member, new_title.strip()),
        ).fetchone()
        if clash:
            return False
        conn.execute(
            "UPDATE worksheets SET title = ?, updated_at = ? WHERE id = ? AND member = ?",
            (new_title.strip(), _now(), meta["id"], member),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def set_pinned(member, title, pinned, db_path=None) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE worksheets SET pinned = ?, updated_at = ? WHERE member = ? AND title = ?",
            (1 if pinned else 0, _now(), member, title),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_sheet(member, title, db_path=None) -> bool:
    conn = _connect(db_path)
    try:
        meta = _row_meta(member, title, conn)
        if meta is None:
            return False
        conn.execute("DELETE FROM worksheet_rows WHERE sheet_id = ? AND member = ?",
                     (meta["id"], member))
        conn.execute("DELETE FROM worksheets WHERE id = ? AND member = ?",
                     (meta["id"], member))
        conn.commit()
        return True
    finally:
        conn.close()


def pinned_sheets(member, db_path=None) -> list[dict]:
    conn = _connect(db_path)
    try:
        titles = [r["title"] for r in conn.execute(
            "SELECT title FROM worksheets WHERE member = ? AND pinned = 1 "
            "ORDER BY updated_at DESC, id DESC", (member,),
        ).fetchall()]
    finally:
        conn.close()
    return [get_sheet(member, t, db_path=db_path) for t in titles]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/sheet_db.py tests/test_worksheet.py
git commit -m "feat(notes): worksheet rename/pin/delete + pinned_sheets + isolation"
```

---

### Task 5: CLI subcommands + render helpers

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/cli.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: all `sheet_db` functions.
- Produces CLI subcommands (stdout text; `NOTE_DB_PATH` honored via existing `_db_for`):
  `sheet-create, sheet-list, sheet-show, sheet-set, sheet-unset, sheet-row-add, sheet-row-edit, sheet-row-delete, sheet-rename, sheet-pin, sheet-delete`.
  `--data` is a JSON object string parsed with `json.loads`.

- [ ] **Step 1: Write the failing test (CLI smoke via subprocess)**

```python
def _cli(db, *args):
    env = dict(os.environ, NOTE_DB_PATH=db, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, str(SKILL / "cli.py"), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return r


def test_cli_kv_flow(db):
    assert _cli(db, "sheet-create", "--member", "爸爸", "--title", "房贷",
                "--kind", "kv").returncode == 0
    assert _cli(db, "sheet-set", "--member", "爸爸", "--title", "房贷",
                "--field", "利率", "--value", "5%").returncode == 0
    out = _cli(db, "sheet-show", "--member", "爸爸", "--title", "房贷").stdout
    assert "利率" in out and "5%" in out


def test_cli_table_flow(db):
    _cli(db, "sheet-create", "--member", "爸爸", "--title", "血压", "--kind", "table")
    r = _cli(db, "sheet-row-add", "--member", "爸爸", "--title", "血压",
             "--data", '{"date":"06-24","sys":120}')
    assert r.returncode == 0
    out = _cli(db, "sheet-show", "--member", "爸爸", "--title", "血压").stdout
    assert "120" in out
    assert "血压" in _cli(db, "sheet-list", "--member", "爸爸").stdout


def test_cli_bad_json_fails(db):
    _cli(db, "sheet-create", "--member", "爸爸", "--title", "血压", "--kind", "table")
    r = _cli(db, "sheet-row-add", "--member", "爸爸", "--title", "血压", "--data", "{bad")
    assert r.returncode == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worksheet.py -k cli -v`
Expected: FAIL — `sheet-create` is not a valid subcommand (argparse exits 2 / stderr).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add `import json` and `import sheet_db` near the existing imports (after `import note_db`). Add render helpers + command functions before `def main()`:

```python
def _fmt_sheet(s: dict) -> str:
    head = f"📊 {s['title']}（{s['kind']}）" + ("  📌" if s["pinned"] else "")
    lines = [head]
    if s["kind"] == "kv":
        if not s["kv_data"]:
            lines.append("  （空）")
        for k, v in s["kv_data"].items():
            lines.append(f"  {k}: {v}")
    else:
        if not s["rows"]:
            lines.append("  （无行）")
        for row in s["rows"]:
            cells = "  ".join(f"{k}={v}" for k, v in row["row_data"].items())
            lines.append(f"  #{row['id']}  {cells}")
    return "\n".join(lines)


def cmd_sheet_create(args):
    sid = sheet_db.create_sheet(args.member, args.title, args.kind,
                                pinned=args.pinned, db_path=_db_for(args.member))
    _mark_backup_dirty()
    print(f"已创建工作表 #{sid}：{args.title}（{args.kind}）")


def cmd_sheet_list(args):
    rows = sheet_db.list_sheets(args.member, db_path=_db_for(args.member))
    if not rows:
        print("（无工作表）")
        return
    for r in rows:
        mark = "  📌" if r["pinned"] else ""
        print(f"📊 {r['title']}（{r['kind']}, {r['size']}）{mark}")


def cmd_sheet_show(args):
    s = sheet_db.get_sheet(args.member, args.title, db_path=_db_for(args.member))
    if s is None:
        print("[错误] 无此工作表", file=sys.stderr)
        sys.exit(1)
    print(_fmt_sheet(s))


def cmd_sheet_set(args):
    ok = sheet_db.set_field(args.member, args.title, args.field, args.value,
                            db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已更新 {args.title}.{args.field} = {args.value}")
    else:
        print("[错误] 无此 kv 工作表", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_unset(args):
    ok = sheet_db.unset_field(args.member, args.title, args.field,
                              db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已删除字段 {args.title}.{args.field}")
    else:
        print("[错误] 无此字段", file=sys.stderr)
        sys.exit(1)


def _parse_data(raw: str) -> dict:
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("--data 必须是 JSON 对象")
    return obj


def cmd_sheet_row_add(args):
    rid = sheet_db.add_row(args.member, args.title, _parse_data(args.data),
                           db_path=_db_for(args.member))
    if rid is None:
        print("[错误] 无此 table 工作表", file=sys.stderr)
        sys.exit(1)
    _mark_backup_dirty()
    print(f"已添加行 #{rid}")


def cmd_sheet_row_edit(args):
    ok = sheet_db.edit_row(args.member, args.title, args.row_id,
                           _parse_data(args.data), db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已更新行 #{args.row_id}")
    else:
        print("[错误] 无此行", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_row_delete(args):
    ok = sheet_db.delete_row(args.member, args.title, args.row_id,
                             db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已删除行 #{args.row_id}")
    else:
        print("[错误] 无此行", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_rename(args):
    ok = sheet_db.rename_sheet(args.member, args.title, args.new_title,
                               db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已重命名为 {args.new_title}")
    else:
        print("[错误] 重命名失败（无此表或新名已存在）", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_pin(args):
    pinned = not args.unpin
    ok = sheet_db.set_pinned(args.member, args.title, pinned,
                             db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(("已置顶 " if pinned else "已取消置顶 ") + args.title)
    else:
        print("[错误] 无此工作表", file=sys.stderr)
        sys.exit(1)


def cmd_sheet_delete(args):
    ok = sheet_db.delete_sheet(args.member, args.title, db_path=_db_for(args.member))
    if ok:
        _mark_backup_dirty()
        print(f"已删除工作表 {args.title}")
    else:
        print("[错误] 无此工作表", file=sys.stderr)
        sys.exit(1)
```

Then register the subparsers inside `main()` (after the existing `note-pin` parser, before `args = parser.parse_args()`):

```python
    p = sub.add_parser("sheet-create", help="创建工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--kind", required=True, choices=["kv", "table"])
    p.add_argument("--pinned", action="store_true")

    p = sub.add_parser("sheet-list", help="列出工作表")
    p.add_argument("--member", required=True)

    p = sub.add_parser("sheet-show", help="显示工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)

    p = sub.add_parser("sheet-set", help="设置 kv 字段")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)

    p = sub.add_parser("sheet-unset", help="删除 kv 字段")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--field", required=True)

    p = sub.add_parser("sheet-row-add", help="表格加行")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--data", required=True, help="JSON 对象")

    p = sub.add_parser("sheet-row-edit", help="表格改行")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--row-id", type=int, required=True, dest="row_id")
    p.add_argument("--data", required=True, help="JSON 对象")

    p = sub.add_parser("sheet-row-delete", help="表格删行")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--row-id", type=int, required=True, dest="row_id")

    p = sub.add_parser("sheet-rename", help="重命名工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--new-title", required=True, dest="new_title")

    p = sub.add_parser("sheet-pin", help="置顶/取消置顶工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--unpin", action="store_true")

    p = sub.add_parser("sheet-delete", help="删除工作表")
    p.add_argument("--member", required=True)
    p.add_argument("--title", required=True)
```

Add to the `dispatch` dict:

```python
        "sheet-create": cmd_sheet_create,
        "sheet-list": cmd_sheet_list,
        "sheet-show": cmd_sheet_show,
        "sheet-set": cmd_sheet_set,
        "sheet-unset": cmd_sheet_unset,
        "sheet-row-add": cmd_sheet_row_add,
        "sheet-row-edit": cmd_sheet_row_edit,
        "sheet-row-delete": cmd_sheet_row_delete,
        "sheet-rename": cmd_sheet_rename,
        "sheet-pin": cmd_sheet_pin,
        "sheet-delete": cmd_sheet_delete,
```

The existing `main()` already wraps `dispatch` in `try/except ValueError -> return 1`, which covers `_parse_data` bad-JSON (`json.JSONDecodeError` subclasses `ValueError`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/cli.py tests/test_worksheet.py
git commit -m "feat(notes): worksheet CLI subcommands"
```

---

### Task 6: agent_core wiring — whitelist, routing, tools, member-forcing

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`

**Interfaces:**
- Consumes: `sheet-*` CLI commands (Task 5).
- Produces: LLM tools `create_worksheet, show_worksheet, list_worksheets, set_worksheet_field, unset_worksheet_field, add_worksheet_row, edit_worksheet_row, delete_worksheet_row, rename_worksheet, pin_worksheet, delete_worksheet`. All member-forced.

- [ ] **Step 1: Add command set, routing, whitelist**

After the `_NOTE_COMMANDS = {...}` line (~134), add:

```python
_SHEET_COMMANDS = {"sheet-create", "sheet-list", "sheet-show", "sheet-set",
                   "sheet-unset", "sheet-row-add", "sheet-row-edit",
                   "sheet-row-delete", "sheet-rename", "sheet-pin", "sheet-delete"}
```

In `_cli_path`, extend the Note_Keeper branch:

```python
    elif cmd in _NOTE_COMMANDS or cmd in _SHEET_COMMANDS:
        skill = "Note_Keeper"
```

After `ALLOWED_COMMANDS |= _NOTE_COMMANDS` (~140), add:

```python
ALLOWED_COMMANDS |= _SHEET_COMMANDS
```

- [ ] **Step 2: Add tool handlers**

After `_tool_pin_note` (~366), add:

```python
def _tool_create_worksheet(args): return _run_cli("sheet-create", args)
def _tool_list_worksheets(args): return _run_cli("sheet-list", args)
def _tool_show_worksheet(args): return _run_cli("sheet-show", args)
def _tool_set_worksheet_field(args): return _run_cli("sheet-set", args)
def _tool_unset_worksheet_field(args): return _run_cli("sheet-unset", args)
def _tool_delete_worksheet_row(args): return _run_cli("sheet-row-delete", args)
def _tool_rename_worksheet(args): return _run_cli("sheet-rename", args)
def _tool_pin_worksheet(args): return _run_cli("sheet-pin", args)
def _tool_delete_worksheet(args): return _run_cli("sheet-delete", args)


def _tool_add_worksheet_row(args):
    args = dict(args)
    data = args.pop("data", None)
    if isinstance(data, (dict, list)):
        args["data"] = json.dumps(data, ensure_ascii=False)
    elif data is not None:
        args["data"] = str(data)
    return _run_cli("sheet-row-add", args)


def _tool_edit_worksheet_row(args):
    args = dict(args)
    data = args.pop("data", None)
    if isinstance(data, (dict, list)):
        args["data"] = json.dumps(data, ensure_ascii=False)
    elif data is not None:
        args["data"] = str(data)
    return _run_cli("sheet-row-edit", args)
```

- [ ] **Step 3: Register in dispatch + member-forced set**

In the tool dispatch dict (after `"pin_note": _tool_pin_note,` ~468), add:

```python
    "create_worksheet": _tool_create_worksheet,
    "list_worksheets": _tool_list_worksheets,
    "show_worksheet": _tool_show_worksheet,
    "set_worksheet_field": _tool_set_worksheet_field,
    "unset_worksheet_field": _tool_unset_worksheet_field,
    "add_worksheet_row": _tool_add_worksheet_row,
    "edit_worksheet_row": _tool_edit_worksheet_row,
    "delete_worksheet_row": _tool_delete_worksheet_row,
    "rename_worksheet": _tool_rename_worksheet,
    "pin_worksheet": _tool_pin_worksheet,
    "delete_worksheet": _tool_delete_worksheet,
```

Replace the `_NOTE_TOOLS = {...}` set (~486) so worksheet tools are member-forced too:

```python
_NOTE_TOOLS = {"save_note", "search_notes", "list_notes", "delete_note", "pin_note",
               "create_worksheet", "list_worksheets", "show_worksheet",
               "set_worksheet_field", "unset_worksheet_field", "add_worksheet_row",
               "edit_worksheet_row", "delete_worksheet_row", "rename_worksheet",
               "pin_worksheet", "delete_worksheet"}
```

- [ ] **Step 4: Verify import + dispatch load**

Run: `python -c "import sys; sys.path.insert(0, '.codewhale/skills/Agent_Runtime'); import agent_core; assert 'create_worksheet' in agent_core._TOOL_DISPATCH if hasattr(agent_core,'_TOOL_DISPATCH') else True; print('ok')"`

Note: the dispatch dict variable name is the one holding `"save_note": _tool_save_note` (read the file to confirm its identifier; if it has no module-level name, instead run the smoke import below).

Smoke: `python -c "import sys; sys.path.insert(0,'.codewhale/skills/Agent_Runtime'); import agent_core; print('SHEET' , sorted(agent_core._SHEET_COMMANDS))"`
Expected: prints the 11 sheet commands, no import error.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py
git commit -m "feat(agent): wire worksheet tools (whitelist, handlers, member-forcing)"
```

---

### Task 7: tool schemas + context injection + system-prompt guidance

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_worksheet.py`

**Interfaces:**
- Consumes: Task 6 handlers; `sheet_db.pinned_sheets`.
- Produces: `_worksheets_context(member, db_path=None) -> str`; tool JSON schemas in the tools list; guidance text in system prompt.

- [ ] **Step 1: Write the failing test (context injection)**

```python
def test_worksheets_context_render(tmp_path, monkeypatch):
    import importlib
    AR = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"
    sys.path.insert(0, str(AR))
    db = str(tmp_path / "notes.db")
    sheet_db.create_sheet("爸爸", "房贷", "kv", pinned=True, db_path=db)
    sheet_db.set_field("爸爸", "房贷", "利率", "5%", db_path=db)
    import agent_core
    out = agent_core._worksheets_context("爸爸", db_path=db)
    assert "房贷" in out and "利率" in out and "5%" in out


def test_worksheets_context_row_cap(tmp_path):
    db = str(tmp_path / "notes.db")
    sheet_db.create_sheet("爸爸", "大表", "table", pinned=True, db_path=db)
    for i in range(90):
        sheet_db.add_row("爸爸", "大表", {"i": i}, db_path=db)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"))
    import agent_core
    out = agent_core._worksheets_context("爸爸", db_path=db)
    assert "还有" in out  # truncation note present (cap default 80)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worksheet.py -k worksheets_context -v`
Expected: FAIL — `AttributeError: module 'agent_core' has no attribute '_worksheets_context'`.

- [ ] **Step 3: Implement context injection**

Near the config-derived constants (after `_CAL_LOOKAHEAD`, ~510), add:

```python
_WORKSHEET_PIN_ROW_CAP = int((_CONFIG.get("notes") or {}).get("worksheet_pin_row_cap") or 80)
```

After `_notes_context` (~778), add (note: `sheet_db` is importable because the Note_Keeper dir is already on `sys.path` from the existing `_notes_context` block):

```python
def _worksheets_context(member: str, db_path: str | None = None) -> str:
    """取该成员置顶工作表，整表渲染进 system prompt（你的选择 B：全量注入）。

    进程内直调 sheet_db。table 超 _WORKSHEET_PIN_ROW_CAP 行截断并提示。
    任何失败返回空串 —— 工作表注入绝不能拖垮 handle()。
    """
    try:
        import sheet_db
        kw = {"db_path": db_path} if db_path else {}
        sheets = sheet_db.pinned_sheets(member, **kw)
        if not sheets:
            return ""
        blocks = []
        for s in sheets:
            if s is None:
                continue
            lines = [f"### {s['title']}（{s['kind']}）"]
            if s["kind"] == "kv":
                for k, v in s["kv_data"].items():
                    lines.append(f"- {k}: {v}")
            else:
                rows = s["rows"]
                shown = rows[:_WORKSHEET_PIN_ROW_CAP]
                for row in shown:
                    cells = "  ".join(f"{k}={v}" for k, v in row["row_data"].items())
                    lines.append(f"- #{row['id']} {cells}")
                if len(rows) > _WORKSHEET_PIN_ROW_CAP:
                    lines.append(f"- …还有 {len(rows) - _WORKSHEET_PIN_ROW_CAP} 行，"
                                 f"用 show_worksheet 看全部")
            blocks.append("\n".join(lines))
        return (f"\n\n## 已存工作表（仅 {member} 可见，置顶项全量带上）\n"
                + "\n\n".join(blocks))
    except Exception:
        _log.exception("工作表上下文注入失败（已跳过）")
        return ""
```

- [ ] **Step 4: Wire injection into handle()**

In `handle()` (~853) change:

```python
                 + _notes_context(member) + _schedule_context(member)}]
```

to:

```python
                 + _notes_context(member) + _worksheets_context(member)
                 + _schedule_context(member)}]
```

- [ ] **Step 5: Add tool schemas**

In the tools list, after the `pin_note` `_fn(...)` entry (~701), add:

```python
    _fn("create_worksheet", "创建一张工作表，用于长期跟踪结构化信息。仅当用户明确要求"
        "\"建个表/做个 worksheet/长期记录这些\"时才用；普通杂事用 save_note。"
        "kind=kv 是事实清单（字段→值，如房贷利率/到期）；kind=table 是流水记录"
        "（多行，每行动态列，如血压/体重打卡）", {
        "title": _s("工作表名（唯一，作为后续引用的句柄）"),
        "kind": _s("kv=事实清单 / table=流水记录", enum=["kv", "table"]),
        "pinned": {"type": "boolean", "description": "置顶：每次对话自动带上全表内容"},
    }, ["title", "kind"]),
    _fn("list_worksheets", "列出本人的工作表（名称/类型/规模）", {}),
    _fn("show_worksheet", "显示一张工作表的完整内容", {
        "title": _s("工作表名"),
    }, ["title"]),
    _fn("set_worksheet_field", "在 kv 工作表上设置/覆盖一个字段", {
        "title": _s("工作表名"),
        "field": _s("字段名"),
        "value": _s("字段值"),
    }, ["title", "field", "value"]),
    _fn("unset_worksheet_field", "从 kv 工作表删除一个字段", {
        "title": _s("工作表名"),
        "field": _s("字段名"),
    }, ["title", "field"]),
    _fn("add_worksheet_row", "向 table 工作表追加一行（列名→值，列可动态新增）", {
        "title": _s("工作表名"),
        "data": {"type": "object", "description": "一行数据，键=列名 值=单元格值"},
    }, ["title", "data"]),
    _fn("edit_worksheet_row", "覆盖 table 工作表的某一行（按行 id）", {
        "title": _s("工作表名"),
        "row-id": _int("行 id（见 show_worksheet 的 #号）"),
        "data": {"type": "object", "description": "整行新数据（覆盖式）"},
    }, ["title", "row-id", "data"]),
    _fn("delete_worksheet_row", "删除 table 工作表的某一行（按行 id）", {
        "title": _s("工作表名"),
        "row-id": _int("行 id"),
    }, ["title", "row-id"]),
    _fn("rename_worksheet", "重命名一张工作表", {
        "title": _s("当前名"),
        "new-title": _s("新名"),
    }, ["title", "new-title"]),
    _fn("pin_worksheet", "置顶/取消置顶工作表（置顶=每次对话自动带上全表）", {
        "title": _s("工作表名"),
        "unpin": {"type": "boolean", "description": "true=取消置顶"},
    }, ["title"]),
    _fn("delete_worksheet", "删除整张工作表（含所有行）", {
        "title": _s("工作表名"),
    }, ["title"]),
```

- [ ] **Step 6: Add system-prompt guidance**

In `_build_system_prompt`, after the note guidance line (search for `save_note；重要长期信息建议 pinned`, ~255), add a new bullet block in the same f-string:

```
- 工作表（长期结构化跟踪）：仅当用户明确说"建个表/做个 worksheet/长期记录这些字段/这些流水"时用 create_worksheet；普通"记一下"仍用 save_note，不要升级成工作表。kv=事实清单（房贷利率/保单号），table=流水（血压/体重/读数打卡）。更新已存表用 set_worksheet_field（kv）或 add_worksheet_row/edit_worksheet_row（table）。
```

(Insert as a plain text line within the existing prompt string; match surrounding indentation/format.)

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_worksheet.py -v`
Expected: PASS (all, incl. the 2 context tests).

- [ ] **Step 8: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_worksheet.py
git commit -m "feat(agent): worksheet tool schemas + pinned context injection + prompt guidance"
```

---

### Task 8: docs (SKILL.md) + full suite green

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/SKILL.md`

**Interfaces:** none (docs).

- [ ] **Step 1: Document worksheet in SKILL.md**

Add a `## 工作表（Worksheet）` section after the existing note CLI reference, covering: the two kinds (kv/table), dynamic schema, the `sheet-*` CLI commands (mirror the list from the design spec), the `sheet_db` API table, per-member isolation + no-leak, pinned full-context injection with `worksheet_pin_row_cap` (config key `notes.worksheet_pin_row_cap`, default 80), and the "only on explicit user request" agent rule. Add `sheet_db.py` to the code-layout tree.

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (existing suite + `test_worksheet.py`).

- [ ] **Step 3: Commit**

```bash
git add .codewhale/skills/Note_Keeper/SKILL.md
git commit -m "docs(notes): document worksheet feature"
```

---

## Self-Review

**Spec coverage:** kv+table (T1–T3), dynamic schema (JSON, T1/T3), full mutation set/unset/row add-edit-delete/rename/pin/delete (T2–T4), per-member isolation + no-leak (T1–T4 tests), unique title (T1), CLI surface (T5), agent wiring + member-forcing (T6), tool schemas + pinned full-content injection + soft row cap + explicit-only guidance (T7), tests (T1–T7), docs (T8). All spec sections mapped.

**Placeholder scan:** No TBD/TODO; every code step shows full code. Task 6 Step 4 notes the dispatch-dict identifier must be confirmed by reading the file (the smoke import is the reliable check) — this is a verification instruction, not a code placeholder.

**Type consistency:** `db_path=None` trailing kwarg on every `sheet_db` function; `get_sheet`/`pinned_sheets` return the same dict shape (`kv_data` dict + `rows` list); CLI uses `dest="row_id"`/`dest="new_title"` to match `args.row_id`/`args.new_title`; agent `row-id`/`new-title` tool params map to `--row-id`/`--new-title` via `_run_cli`. `_WORKSHEET_PIN_ROW_CAP` used consistently. Names align across tasks.
