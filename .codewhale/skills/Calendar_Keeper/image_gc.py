"""
Calendar Keeper — 陈旧来图清理（活动/待办的 source_image）

删除 N 年前活动/待办的原始来图文件并清空 source_image 链接（保留行本身）。
陈旧判定：行的 start_at（活动日 / 待办截止日）前 10 位；为空回退 created_at。
该日期 < 今天 − retention_years → 删文件 + cal_db.clear_source_image。
图片非远端字段（Google 无此概念）→ 清理不触发同步。

参数（config.json `calendar`）：
    image_retention_years      默认 2（保留近 N 年的来图）
    image_prune_interval_days  默认 30（节流：约每月扫一次）

传输层在已注册成员消息到达后调 image_gc_tick()（节流 + 静默，永不抛）。
节流状态：data/.image_gc_state.json（不入备份）。
测试钩子：CALENDAR_CONFIG（替代 config.json）、IMAGE_GC_STATE_DIR、DATA_ROOT。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Agent_Runtime"))
import cal_db
import members as _members
import paths as _paths

_FALLBACK = {"image_retention_years": 2, "image_prune_interval_days": 30}


def _cfg() -> dict:
    cfg_path = Path(os.environ.get("CALENDAR_CONFIG") or (ROOT / "config.json"))
    try:
        cal = json.loads(cfg_path.read_text(encoding="utf-8")).get("calendar") or {}
    except Exception:
        cal = {}
    return {**_FALLBACK, **{k: cal[k] for k in _FALLBACK if k in cal}}


def _state_file() -> Path:
    return Path(os.environ.get("IMAGE_GC_STATE_DIR") or (ROOT / "data")) \
        / ".image_gc_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _years_ago(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:           # 2/29 → 2/28
        return d.replace(year=d.year - years, day=28)


def _item_date(row: dict) -> str:
    """陈旧判定用日期：start_at 前 10 位，空则回退 created_at 前 10 位。"""
    return (row["start_at"] or "")[:10] or (row["created_at"] or "")[:10]


def prune_stale_images(now: datetime | None = None,
                       retention_years: int | None = None,
                       *, dry_run: bool = False) -> dict:
    """扫所有成员的 schedule/tasks 库，清陈旧来图。返回报告（不抛异常）。"""
    now = now or datetime.now()
    ry = int(retention_years if retention_years is not None
             else _cfg()["image_retention_years"])
    cutoff = _years_ago(now.date(), ry).isoformat()
    report = {"cleared": 0, "files_deleted": 0, "members": {}, "dry_run": dry_run}
    for member in _members.member_names():
        for domain in ("schedule", "tasks"):
            db = _paths.member_store(member, domain)
            if not db.exists():                  # 该成员该域无库 → 不创建空库
                continue
            db = str(db)
            for row in cal_db.items_with_image(db_path=db):
                d = _item_date(row)
                if not d or d >= cutoff:          # 无日期或还在保留期 → 留
                    continue
                report["cleared"] += 1
                report["members"][member] = report["members"].get(member, 0) + 1
                if dry_run:
                    continue
                try:
                    f = _paths.resolve_rel(row["source_image"])
                    if f.exists():
                        f.unlink()
                        report["files_deleted"] += 1
                except Exception:
                    pass
                cal_db.clear_source_image(row["id"], db_path=db)
    return report


def image_gc_tick(now: datetime | None = None) -> bool:
    """已注册成员消息到达后调。按 interval_days 节流扫一次，静默，永不抛。返回是否扫了。"""
    try:
        now = now or datetime.now()
        interval = float(_cfg()["image_prune_interval_days"]) * 86400
        st = _load_state()
        last = st.get("last_run")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < interval:
                    return False
            except ValueError:
                pass
        prune_stale_images(now=now)
        st["last_run"] = now.isoformat(timespec="seconds")
        _save_state(st)
        return True
    except Exception:
        return False
