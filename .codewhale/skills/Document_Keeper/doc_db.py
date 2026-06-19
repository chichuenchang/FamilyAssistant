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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))  # paths
from doc_models import (
    SCHEMA, DOC_TYPES, DOC_STATUSES, REMINDER_LEAD_DAYS, DB_PATH,
)
import paths as _paths

_ROOT = Path(__file__).resolve().parents[3]


def _abs_file(file_path: str) -> Path:
    """文档文件绝对路径：新约定按 data_root 相对（Family/documents/..）解析，
    回退项目根相对（旧库里的路径），最后回退 data_root 形式。"""
    p = Path(file_path)
    if p.is_absolute():
        return p
    cand = _paths.resolve_rel(file_path)       # data_root/<file_path>
    if cand.exists():
        return cand
    legacy = _ROOT / file_path                  # 旧：项目根相对
    if legacy.exists():
        return legacy
    return cand

# 可经 update_document 修改的列（id / created_at / data / acknowledged 除外；
# acknowledged 由 ack_document 与到期日变更逻辑管理）
_UPDATABLE = {
    "doc_type", "title", "member", "issuer", "doc_number", "issue_date",
    "expiry_date", "action_note", "remind_days", "file_path", "ocr_text",
    "status", "notes",
}


def get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，自动启用 WAL 和 foreign keys，并确保 documents 表存在。

    幂等建表（CREATE ... IF NOT EXISTS）放在连接处：reminder 每轮轮询都读
    documents，但账本在首次 doc-add 前从未 init_db，会 "no such table"。
    在所有读写经过的唯一入口建表，虚拟账本上的读取返回空而非崩溃。
    """
    path = db_path or str(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
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
        abs_p = _abs_file(file_path)
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
        try:
            _abs_file(doc["file_path"]).unlink(missing_ok=True)
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
