"""
Calendar Keeper — 陈旧来图清理（按月目录年龄）

来图不再挂到日程条目上：留在 data/<成员>/inbox/YYYY-MM/（旧版搬到 schedule/、tasks/ 月目录的也算）。
删除早于「今天 − image_retention_years」所在月的 YYYY-MM 目录。notes/ 不动（备忘库链接其图）。

参数（config.json `calendar`）：
    image_retention_years      默认 1
    image_prune_interval_days  默认 30（节流：约每月扫一次）

传输层在已注册成员消息到达后调 image_gc_tick()（节流 + 静默，永不抛）。
节流状态：data/.state/.image_gc_state.json（不入备份）。
测试钩子：CALENDAR_CONFIG（替代 config.json）、IMAGE_GC_STATE_DIR、DATA_ROOT。
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
import members as _members
import paths as _paths
import jsonfile

_STATE_NAME = ".image_gc_state.json"
_FALLBACK = {"image_retention_years": 1, "image_prune_interval_days": 30}
_AREAS = ("inbox", "schedule", "tasks")
_MONTH = re.compile(r"\d{4}-\d{2}")


def _cfg() -> dict:
    cfg_path = Path(os.environ.get("CALENDAR_CONFIG") or (ROOT / "config.json"))
    cal = jsonfile.load_dict(cfg_path).get("calendar") or {}
    return {**_FALLBACK, **{k: cal[k] for k in _FALLBACK if k in cal}}


def _state_file() -> Path:
    env = os.environ.get("IMAGE_GC_STATE_DIR")
    return Path(env) / _STATE_NAME if env else _paths.state_file(_STATE_NAME)


def _load_state() -> dict:
    return jsonfile.load_dict(_state_file())


def _save_state(st: dict) -> None:
    jsonfile.save(_state_file(), st)


def _years_ago(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:           # 2/29 → 2/28
        return d.replace(year=d.year - years, day=28)


def prune_stale_images(now: datetime | None = None,
                       retention_years: int | None = None,
                       *, dry_run: bool = False) -> dict:
    """删各成员 _AREAS 下早于截止月的 YYYY-MM 目录。返回报告。"""
    now = now or datetime.now()
    ry = int(retention_years if retention_years is not None
             else _cfg()["image_retention_years"])
    cutoff = _years_ago(now.date(), ry).strftime("%Y-%m")
    report = {"dirs_deleted": 0, "files_deleted": 0, "members": {}, "dry_run": dry_run}
    for member in _members.member_names():
        root = _paths.member_dir(member)
        for area in _AREAS:
            base = root / area
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if not (d.is_dir() and _MONTH.fullmatch(d.name) and d.name < cutoff):
                    continue
                report["dirs_deleted"] += 1
                report["members"][member] = report["members"].get(member, 0) + 1
                if dry_run:
                    continue
                report["files_deleted"] += sum(1 for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
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
