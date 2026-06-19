"""
Family Assistant — 一次性存储迁移：单库 data/ledger.db → 分库布局。

把旧的单一 ledger.db 拆成：
    data/Family/ledger.db               收支/定期/划转/报税/汇率/文档（家庭共享，当前多为空）
    data/<owner>/notes/notes.db         备忘按 member 分库（图片搬进该成员 notes/YYYY-MM/）
    data/<owner>/schedule/schedule.db   活动（kind=event）
    data/<owner>/tasks/tasks.db         待办（kind=task）

日历归属：所有 schedule_items 都活在日历 owner（唯一配置了 schedule 同步的成员，
当前=Jim）的远程账号上 → 全部迁到 owner 分库，**保留 uid/synced/origin/status/时间戳**，
避免下一轮同步把它们当新行重推（远端重复）。未登记的旧 member 标签（如"爸爸"）改为 owner；
已登记标签（如 Euphie）保留。owner 两个域 .sync_state.json 以旧全局状态播种。

幂等：schedule 按 uid、notes 按 (member,content,created_at) 跳过已存在行；family 表
已有行则跳过整表。成功后把旧库改名 <name>.premigration.bak（即快照）。

用法：python .codewhale/skills/Agent_Runtime/migrate_storage.py [--old P] [--state P] [--dry-run]
默认 old=data/ledger.db，state=data/.calendar_state.json。**运行前请停掉机器人。**
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _d in ("Agent_Runtime", "Expense_Tracker", "Document_Keeper",
           "Note_Keeper", "Calendar_Keeper"):
    sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / _d))

import paths
import members
import models as expense_models      # Expense SCHEMA（transactions/deposits/transfers/tax/fx）
import doc_models                     # Document SCHEMA（documents）
import note_db
import cal_db

_FAMILY_TABLES = ["transactions", "deposits", "transfers", "tax_filings",
                  "exchange_rates", "documents"]
_PATH_COLS = {"transactions": "receipt_path", "deposits": "receipt_path",
              "tax_filings": "receipt_path", "documents": "file_path"}


def _has_table(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (t,)).fetchone() is not None


def _columns(conn, t: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]


def _rows(conn, t: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]


def _insert(conn, table: str, d: dict) -> None:
    cols = list(d.keys())
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [d[c] for c in cols])


def _exists(conn, table: str, where: str, params: list) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
                        params).fetchone() is not None


def _detect_calendar_owner() -> str | None:
    """日历 owner = 唯一配置了 schedule 远程同步的成员（当前 = Jim）。"""
    for m in members.member_names():
        if members.sync_pref(m, "schedule"):
            return m
    return None


def _rewrite_path(rel: str) -> str:
    """旧项目根相对路径（receipts/.. 或 documents/..）→ data_root 相对（Family/..）。"""
    if not rel:
        return rel
    r = rel.replace("\\", "/")
    if r.startswith("receipts/") or r.startswith("documents/"):
        return "Family/" + r
    return rel


def _move_note_image(src: str, member: str, report: dict) -> str:
    try:
        if not src:
            return src
        p = Path(src)
        cands = [p if p.is_absolute() else ROOT / p, ROOT / src, paths.resolve_rel(src)]
        srcabs = next((c for c in cands if c.exists()), None)
        if srcabs is None:
            return src                      # 文件已不在，保留字符串
        dest_dir = paths.member_notes_image_dir(member)
        dest = dest_dir / srcabs.name
        i = 1
        while dest.exists():
            dest = dest_dir / f"{srcabs.stem}_{i}{srcabs.suffix}"
            i += 1
        shutil.move(str(srcabs), str(dest))
        report["images_moved"] += 1
        return paths.to_rel(dest)
    except Exception:
        return src


def migrate(old_ledger, old_state=None, *, calendar_owner: str | None = None,
            dry_run: bool = False) -> dict:
    old_ledger = Path(old_ledger)
    if not old_ledger.exists():
        raise FileNotFoundError(old_ledger)
    if calendar_owner is None:
        calendar_owner = _detect_calendar_owner()
    report = {"calendar_owner": calendar_owner, "family": {}, "notes": {},
              "events": 0, "tasks": 0, "images_moved": 0, "snapshot": None,
              "dry_run": dry_run}

    old = sqlite3.connect(str(old_ledger))
    old.row_factory = sqlite3.Row

    # ── Family 账本：建表 + 拷贝财务/文档行（当前多为空，但通用处理） ──
    fconn = None
    if not dry_run:
        fam = paths.family_ledger()
        fam.parent.mkdir(parents=True, exist_ok=True)
        fconn = sqlite3.connect(str(fam))
        fconn.executescript(expense_models.SCHEMA)
        fconn.executescript(doc_models.SCHEMA)
        fconn.commit()
    for t in _FAMILY_TABLES:
        rows = _rows(old, t) if _has_table(old, t) else []
        report["family"][t] = len(rows)
        if dry_run or not rows:
            continue
        if _exists(fconn, t, "1=1", []):
            continue                        # 幂等：目标已有数据，跳过整表
        cols = _columns(fconn, t)
        pc = _PATH_COLS.get(t)
        for row in rows:
            d = {k: row[k] for k in row.keys() if k in cols}
            if pc and d.get(pc):
                d[pc] = _rewrite_path(d[pc])
            _insert(fconn, t, d)
        fconn.commit()
    if fconn:
        fconn.close()

    # ── notes 按 member 分库（图片搬进该成员 notes/YYYY-MM/） ──
    for row in (_rows(old, "notes") if _has_table(old, "notes") else []):
        member = row["member"]
        report["notes"][member] = report["notes"].get(member, 0) + 1
        if dry_run:
            continue
        store = str(paths.member_store(member, "notes"))
        note_db._connect(db_path=store).close()
        nconn = sqlite3.connect(store)
        cols = _columns(nconn, "notes")
        if not _exists(nconn, "notes", "member=? AND content=? AND created_at=?",
                       [row["member"], row["content"], row["created_at"]]):
            d = {k: row[k] for k in row.keys() if k in cols and k != "id"}
            if d.get("source_image"):
                d["source_image"] = _move_note_image(d["source_image"], member, report)
            _insert(nconn, "notes", d)
            nconn.commit()
        nconn.close()

    # ── schedule_items → owner，按 kind 分库（保留 uid/synced/origin/状态/时间戳） ──
    registered = set(members.member_names())
    for row in (_rows(old, "schedule_items") if _has_table(old, "schedule_items") else []):
        kind = row["kind"]
        domain = "schedule" if kind == "event" else "tasks"
        report["events" if kind == "event" else "tasks"] += 1
        if dry_run:
            continue
        store = str(paths.member_store(calendar_owner, domain))
        cal_db._connect(db_path=store).close()
        sconn = sqlite3.connect(store)
        cols = _columns(sconn, "schedule_items")
        uid = row["uid"]
        if uid and _exists(sconn, "schedule_items", "uid=?", [uid]):
            sconn.close()
            continue                        # 幂等：uid 已在
        d = {k: row[k] for k in row.keys() if k in cols}
        if d.get("member") not in registered:
            d["member"] = calendar_owner    # 未登记旧标签（爸爸…）→ owner
        _insert(sconn, "schedule_items", d)
        sconn.commit()
        sconn.close()

    # ── 播种 owner 两域同步状态（保留 last_refresh，避免立即churn） ──
    if not dry_run and old_state and Path(old_state).exists() and calendar_owner:
        try:
            gst = json.loads(Path(old_state).read_text(encoding="utf-8"))
        except Exception:
            gst = {}
        for domain in ("schedule", "tasks"):
            sp = paths.member_sync_state(calendar_owner, domain)
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps({"last_refresh": gst.get("last_refresh"),
                                      "last_error": gst.get("last_error")},
                                     ensure_ascii=False), encoding="utf-8")

    old.close()

    # ── 快照：成功后把旧库改名 <name>.premigration.bak ──
    if not dry_run:
        bak = old_ledger.with_name(old_ledger.name + ".premigration.bak")
        i = 1
        while bak.exists():
            bak = old_ledger.with_name(f"{old_ledger.name}.premigration.bak.{i}")
            i += 1
        old_ledger.rename(bak)
        report["snapshot"] = str(bak)

    return report


def _main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="单库 → 分库存储迁移（运行前停机器人）")
    ap.add_argument("--old", default=str(paths.data_root() / "ledger.db"))
    ap.add_argument("--state", default=str(paths.data_root() / ".calendar_state.json"))
    ap.add_argument("--dry-run", action="store_true", help="只报告，不改盘")
    a = ap.parse_args(argv)
    state = a.state if Path(a.state).exists() else None
    rep = migrate(Path(a.old), state, dry_run=a.dry_run)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
