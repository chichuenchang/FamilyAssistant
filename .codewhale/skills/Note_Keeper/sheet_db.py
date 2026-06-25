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


def _touch(conn, sheet_id, member):
    conn.execute("UPDATE worksheets SET updated_at = ? WHERE id = ? AND member = ?",
                 (_now(), sheet_id, member))


# ── 创建 / 读取 / 列表 ──────────────────────────────────────

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


# ── kv 字段 set / unset ─────────────────────────────────────

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


# ── table 行 add / edit / delete ────────────────────────────

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


# ── rename / pin / delete / pinned_sheets ───────────────────

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
