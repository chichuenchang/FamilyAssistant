"""
Family Assistant — Note Keeper 数据库操作层

个人备忘（notes）的 SQLite CRUD。Agent / CLI 统一走这个模块。
备忘按成员私有分库：data/<成员>/notes/notes.db（路径经 paths.member_store，CLI 据 --member 解析；db_path 参数注入）。
所有查询强制按 member 过滤，实现按成员隔离。
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_cfg = _load_config()
_ROOT = _CONFIG_PATH.parent
# 旧单库默认：仅未传 db_path 时的兜底；运行时一律按成员分库经 paths 注入 db_path。
DB_PATH = _ROOT / "data" / "ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member       TEXT    NOT NULL,
    content      TEXT    NOT NULL,
    source_image TEXT    DEFAULT '',
    pinned       INTEGER DEFAULT 0,
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_member ON notes(member);
"""


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，自动启用 WAL 并确保 notes 表存在。

    幂等建表（CREATE ... IF NOT EXISTS）：首次调用时自动创建表与索引。
    """
    path = db_path or str(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def add_note(
    member: str,
    content: str,
    source_image: str = "",
    pinned: bool = False,
    db_path: Optional[str] = None,
) -> int:
    """添加一条备忘，返回新记录的 id。member / content 为空时抛出 ValueError。"""
    if not member or not member.strip():
        raise ValueError("member 不能为空")
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    conn = _connect(db_path)
    created_at = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO notes (member, content, source_image, pinned, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (member.strip(), content.strip(), source_image, 1 if pinned else 0, created_at),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_notes(
    member: str,
    limit: int = 20,
    db_path: Optional[str] = None,
) -> list[dict]:
    """列出 member 的备忘，最新在前（ORDER BY id DESC）。置顶在此不做特殊排序。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM notes WHERE member = ? ORDER BY id DESC LIMIT ?",
        (member, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_notes(
    member: str,
    keyword: str,
    db_path: Optional[str] = None,
) -> list[dict]:
    """按关键词模糊搜索 member 的备忘（content LIKE %kw%）。空关键词抛出 ValueError。"""
    if not keyword or not keyword.strip():
        raise ValueError("keyword 不能为空")
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM notes WHERE member = ? AND content LIKE ? ORDER BY id DESC",
        (member, f"%{keyword.strip()}%"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_note(
    member: str,
    note_id: int,
    db_path: Optional[str] = None,
) -> bool:
    """删除 member 名下的一条备忘。仅当该备忘确属此 member 时才删除，返回 True。
    若 id 不存在或归属他人则返回 False（不泄露存在性）。"""
    conn = _connect(db_path)
    cur = conn.execute(
        "DELETE FROM notes WHERE id = ? AND member = ?",
        (note_id, member),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_pinned(
    member: str,
    note_id: int,
    pinned: bool,
    db_path: Optional[str] = None,
) -> bool:
    """置顶/取消置顶 member 名下的一条备忘。归属校验同 delete_note。"""
    conn = _connect(db_path)
    cur = conn.execute(
        "UPDATE notes SET pinned = ? WHERE id = ? AND member = ?",
        (1 if pinned else 0, note_id, member),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def pinned_and_recent(
    member: str,
    recent_limit: int = 5,
    db_path: Optional[str] = None,
) -> list[dict]:
    """返回 member 的所有置顶备忘 + 最近 recent_limit 条非置顶备忘。
    每条 dict 含 id, content, source_image, pinned, created_at。
    用于 agent 上下文注入。"""
    conn = _connect(db_path)
    pinned_rows = conn.execute(
        "SELECT id, content, source_image, pinned, created_at "
        "FROM notes WHERE member = ? AND pinned = 1 ORDER BY id DESC",
        (member,),
    ).fetchall()
    recent_rows = conn.execute(
        "SELECT id, content, source_image, pinned, created_at "
        "FROM notes WHERE member = ? AND pinned = 0 ORDER BY id DESC LIMIT ?",
        (member, recent_limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in pinned_rows] + [dict(r) for r in recent_rows]
