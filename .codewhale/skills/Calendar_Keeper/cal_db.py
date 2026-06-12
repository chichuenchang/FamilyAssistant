"""
Family Assistant — Calendar Keeper 数据库操作层

家庭日程（events/activities）与待办（tasks）的 SQLite CRUD。
表建在共享账本库 data/ledger.db（DB_PATH 来自 config.json db_path）。

与备忘不同：日程是家庭共享的（member 只记录创建者归属，不做可见性隔离）。
远程日历是日程数据的事实源，本表是缓存 + 离线缓冲：
    synced=0  本地有改动待推送（新建/完成/取消）
    synced=1  与远端一致；拉取时远端字段直接覆盖（remote wins）
    origin    local=本地创建 / remote=从远端拉取
同步引擎（calendar_sync.py）是唯一推/拉调用方。
"""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
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
DB_PATH = _ROOT / (_cfg.get("db_path") or "data/ledger.db")

KINDS = ("event", "task")
STATUSES = ("active", "done", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL,
    uid        TEXT    DEFAULT '',
    title      TEXT    NOT NULL,
    start_at   TEXT    DEFAULT '',
    end_at     TEXT    DEFAULT '',
    all_day    INTEGER DEFAULT 0,
    location   TEXT    DEFAULT '',
    notes      TEXT    DEFAULT '',
    member     TEXT    DEFAULT '',
    status     TEXT    DEFAULT 'active',
    origin     TEXT    DEFAULT 'local',
    synced     INTEGER DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sched_uid ON schedule_items(uid);
CREATE INDEX IF NOT EXISTS idx_sched_start ON schedule_items(start_at);
"""

# 远端拉取时允许覆盖的字段（remote wins；归属/origin/审计字段不动）
_REMOTE_FIELDS = ("title", "start_at", "end_at", "all_day", "location",
                  "notes", "status")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，自动启用 WAL 并确保 schedule_items 表存在（幂等建表）。"""
    path = db_path or str(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_item(
    kind: str,
    title: str,
    start_at: str = "",
    end_at: str = "",
    all_day: bool = False,
    location: str = "",
    notes: str = "",
    member: str = "",
    db_path: Optional[str] = None,
) -> int:
    """本地新建一条日程/待办（origin=local, synced=0），返回 id。

    start_at/end_at 为 ISO 字符串（'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM'）；
    待办的 start_at = 截止日期，可为空（无期限待办）。
    """
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {KINDS}")
    if not title or not title.strip():
        raise ValueError("title 不能为空")
    now = _now()
    conn = _connect(db_path)
    cur = conn.execute(
        "INSERT INTO schedule_items (kind, title, start_at, end_at, all_day, "
        "location, notes, member, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, title.strip(), start_at, end_at, 1 if all_day else 0,
         location, notes, member, now, now),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_item(item_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM schedule_items WHERE id = ?",
                       (item_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_upcoming(
    days: int = 10,
    today: Optional[date] = None,
    kind: Optional[str] = None,
    member: Optional[str] = None,
    include_closed: bool = False,
    db_path: Optional[str] = None,
) -> list[dict]:
    """未来 days 天窗口内的日程 + 无期限的开放待办。

    窗口判定（ISO 字符串前 10 位字典序比较）：
        开始日 <= 窗口末端，且（结束日或开始日）>= 今天 —— 跨今天的多日活动也算。
    排序：有日期的按 start_at 升序，无期限待办排最后（按 id）。
    """
    t = (today or date.today()).isoformat()
    horizon = ((today or date.today()) + timedelta(days=days)).isoformat()
    where = ["(start_at = '' OR (substr(start_at,1,10) <= ? "
             "AND substr(CASE WHEN end_at = '' THEN start_at ELSE end_at END,1,10) >= ?))"]
    args: list = [horizon, t]
    if not include_closed:
        where.append("status = 'active'")
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if member:
        where.append("member = ?")
        args.append(member)
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM schedule_items WHERE " + " AND ".join(where) +
        " ORDER BY (start_at = ''), start_at, id",
        args,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_status(
    item_id: int,
    status: str,
    from_remote: bool = False,
    db_path: Optional[str] = None,
) -> bool:
    """改状态。本地改动 → synced=0（待推送）；from_remote=True → synced=1（无需回推）。"""
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {STATUSES}")
    conn = _connect(db_path)
    cur = conn.execute(
        "UPDATE schedule_items SET status = ?, synced = ?, updated_at = ? WHERE id = ?",
        (status, 1 if from_remote else 0, _now(), item_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def pending(db_path: Optional[str] = None) -> list[dict]:
    """待推送行（synced=0），id 升序。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM schedule_items WHERE synced = 0 ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_synced(item_id: int, uid: Optional[str] = None,
                db_path: Optional[str] = None) -> None:
    """推送成功后标记已同步；新建推送同时写入远端 uid。"""
    conn = _connect(db_path)
    if uid is None:
        conn.execute("UPDATE schedule_items SET synced = 1, updated_at = ? WHERE id = ?",
                     (_now(), item_id))
    else:
        conn.execute(
            "UPDATE schedule_items SET synced = 1, uid = ?, updated_at = ? WHERE id = ?",
            (uid, _now(), item_id))
    conn.commit()
    conn.close()


def upsert_remote(kind: str, uid: str, fields: dict,
                  db_path: Optional[str] = None) -> Optional[int]:
    """拉取合并：按 (kind, uid) 入库。

    无此 uid → 新建（origin=remote, synced=1）。
    已有且 synced=1 → 远端字段覆盖（remote wins）。
    已有但 synced=0 → 跳过（本地待推送改动优先，推送后下轮再合并）。
    返回行 id（跳过时也返回现有行 id）。
    """
    if not uid:
        raise ValueError("uid 不能为空")
    now = _now()
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT id, synced FROM schedule_items WHERE kind = ? AND uid = ?",
        (kind, uid)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO schedule_items (kind, uid, title, start_at, end_at, all_day, "
            "location, notes, member, status, origin, synced, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'remote', 1, ?, ?)",
            (kind, uid, fields.get("title") or "(无标题)",
             fields.get("start_at") or "", fields.get("end_at") or "",
             int(fields.get("all_day") or 0), fields.get("location") or "",
             fields.get("notes") or "", fields.get("status") or "active",
             now, now))
        item_id = cur.lastrowid
    elif row["synced"] == 1:
        sets = ", ".join(f"{f} = ?" for f in _REMOTE_FIELDS)
        conn.execute(
            f"UPDATE schedule_items SET {sets}, updated_at = ? WHERE id = ?",
            [fields.get(f, "") if f != "all_day" else int(fields.get(f) or 0)
             for f in _REMOTE_FIELDS] + [now, row["id"]])
        item_id = row["id"]
    else:
        item_id = row["id"]  # 本地有待推送改动，跳过
    conn.commit()
    conn.close()
    return item_id


def synced_active(kind: str, db_path: Optional[str] = None) -> list[dict]:
    """已同步且 active 的行（uid 非空）——拉取后做删除/完成对账用。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM schedule_items WHERE kind = ? AND synced = 1 "
        "AND status = 'active' AND uid != '' ORDER BY id",
        (kind,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
