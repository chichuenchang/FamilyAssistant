"""
Calendar Keeper — 同步引擎（远程日历 ↔ 本地缓存）

本模块是 provider 的唯一调用方：静默刷新、推送本地改动、对账、状态。
远程日历是日程数据的事实源（家人会直接在手机上改日历）；
本地 schedule_items 表是缓存 + 离线缓冲，provider 未配置时一切照常本地工作。

调用契约：
    calendar_tick()   传输层在已注册成员的消息到达后调用（"known member" 触发）。
                      enabled + 距上次刷新 >= refresh_minutes + provider 就绪
                      → 跑一轮 refresh()。静默、节流、永不抛异常。
    refresh()         先推后拉：
                      1) 推送 synced=0 行（新建→create，完成→complete，取消→delete）
                      2) 拉取未来 lookahead_days 窗口的活动 + 全部待办，按 uid 合并
                         （remote wins；本地待推送行跳过）
                      3) 对账：窗口内活动 uid 消失→cancelled；待办 uid 消失→cancelled，
                         远端已完成→done
    push_pending()    cal-add/cal-done/cal-delete 写入后即时尽力推送。
    force_sync()      cal-sync 用：忽略节流立即刷新；provider 未配置返回 None。
    status()          cal-status 用。

状态文件（不入备份、不入 git）：
    data/.calendar_state.json   {last_refresh, last_error}
    节流按"尝试时间"算：失败也记 last_refresh，避免断网时每条消息都重试。
测试钩子：CALENDAR_STATE_DIR 重定位状态文件；CALENDAR_CONFIG 指向替代 config.json。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import cal_db
import calendar_provider as provider

_FALLBACK_CFG = {
    "enabled": False,
    "lookahead_days": 10,
    "refresh_minutes": 15,
}


def _load_cfg() -> dict:
    # CALENDAR_CONFIG 环境变量可指向替代 config.json（测试隔离用）
    cfg_path = Path(os.environ.get("CALENDAR_CONFIG") or (ROOT / "config.json"))
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg = raw.get("calendar")
        if isinstance(cfg, dict):
            return {**_FALLBACK_CFG, **cfg}
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


CFG = _load_cfg()


def _state_file() -> Path:
    return Path(os.environ.get("CALENDAR_STATE_DIR") or (ROOT / "data")) \
        / ".calendar_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _record_error(msg: str) -> None:
    try:
        st = _load_state()
        st["last_error"] = msg
        _save_state(st)
    except Exception:
        pass


def push_pending(db_path: str | None = None) -> tuple[int, list[str]]:
    """推送全部待同步行。逐行隔离：单行失败不影响其余，留待下轮重试。

    返回 (成功处理行数, 错误列表)。provider 未配置 → (0, [])。
    """
    if not provider.is_configured():
        return 0, []
    pushed = 0
    errors: list[str] = []
    for row in cal_db.pending(db_path=db_path):
        try:
            if row["status"] == "active" and not row["uid"]:
                if row["kind"] == "event":
                    uid = provider.create_event(row)
                else:
                    uid = provider.create_task(row)
                cal_db.mark_synced(row["id"], uid=uid, db_path=db_path)
            elif row["status"] == "done":
                if row["kind"] == "task" and row["uid"]:
                    provider.complete_task(row["uid"])
                cal_db.mark_synced(row["id"], db_path=db_path)
            elif row["status"] == "cancelled":
                if row["uid"]:
                    if row["kind"] == "event":
                        provider.delete_event(row["uid"])
                    else:
                        provider.delete_task(row["uid"])
                cal_db.mark_synced(row["id"], db_path=db_path)
            else:
                # active 且已有 uid（v1 无编辑路径）：标记已同步，避免卡死在待推送
                cal_db.mark_synced(row["id"], db_path=db_path)
            pushed += 1
        except Exception as e:
            errors.append(f"#{row['id']} {row['title'][:20]}: {e}")
    return pushed, errors


def refresh(db_path: str | None = None, today: date | None = None,
            now: datetime | None = None) -> dict:
    """先推后拉 + 对账。错误收集进返回值与状态文件，不抛出。"""
    now = now or datetime.now()
    today = today or now.date()
    horizon = today + timedelta(days=int(CFG.get("lookahead_days", 10)))
    errors: list[str] = []

    pushed, push_errors = push_pending(db_path=db_path)
    errors.extend(push_errors)

    n_events = n_tasks = 0
    # 活动：拉窗口 + 对账（remote wins）
    try:
        time_min = datetime.combine(today, dtime.min).astimezone() \
            .isoformat(timespec="seconds")
        time_max = datetime.combine(horizon, dtime(23, 59, 59)).astimezone() \
            .isoformat(timespec="seconds")
        events = provider.list_events(time_min, time_max)
        seen = set()
        for e in events:
            cal_db.upsert_remote("event", e["uid"], {
                "title": e["title"], "start_at": e["start"], "end_at": e["end"],
                "all_day": 1 if e["all_day"] else 0,
                "location": e["location"], "notes": e["notes"],
                "status": "active"}, db_path=db_path)
            seen.add(e["uid"])
        n_events = len(seen)
        t_iso, h_iso = today.isoformat(), horizon.isoformat()
        for row in cal_db.synced_active("event", db_path=db_path):
            d = row["start_at"][:10]
            if d and t_iso <= d <= h_iso and row["uid"] not in seen:
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
    except Exception as e:
        errors.append(f"events: {e}")

    # 待办：全量拉 + 对账（远端完成→done，远端删除→cancelled）
    try:
        tasks = provider.list_tasks()
        seen = set()
        for t in tasks:
            cal_db.upsert_remote("task", t["uid"], {
                "title": t["title"], "start_at": t["due"], "end_at": "",
                "all_day": 0, "location": "", "notes": t["notes"],
                "status": "done" if t["done"] else "active"}, db_path=db_path)
            seen.add(t["uid"])
        n_tasks = len(seen)
        for row in cal_db.synced_active("task", db_path=db_path):
            if row["uid"] not in seen:
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
    except Exception as e:
        errors.append(f"tasks: {e}")

    st = _load_state()
    st["last_refresh"] = now.isoformat(timespec="seconds")
    st["last_error"] = "; ".join(errors[:5]) if errors else None
    _save_state(st)
    return {"pushed": pushed, "events": n_events, "tasks": n_tasks,
            "errors": errors}


def calendar_tick(db_path: str | None = None, now: datetime | None = None) -> bool:
    """已注册成员消息到达后调用。静默节流刷新，返回是否跑了 refresh。永不抛异常。"""
    try:
        if not CFG.get("enabled"):
            return False
        now = now or datetime.now()
        last = _load_state().get("last_refresh")
        if last:
            try:
                elapsed = (now - datetime.fromisoformat(last)).total_seconds()
                if elapsed < float(CFG.get("refresh_minutes", 15)) * 60:
                    return False
            except ValueError:
                pass
        if not provider.is_configured():
            return False
        refresh(db_path=db_path, today=now.date(), now=now)
        return True
    except Exception as e:
        _record_error(str(e))
        return False


def force_sync(db_path: str | None = None) -> dict | None:
    """忽略节流立即刷新（cal-sync）。provider 未配置返回 None。"""
    try:
        if not provider.is_configured():
            return None
    except Exception:
        return None
    return refresh(db_path=db_path)


def status(db_path: str | None = None) -> dict:
    st = _load_state()
    try:
        configured = bool(provider.is_configured())
    except Exception:
        configured = False
    return {
        "enabled": bool(CFG.get("enabled")),
        "configured": configured,
        "last_refresh": st.get("last_refresh"),
        "last_error": st.get("last_error"),
        "pending": len(cal_db.pending(db_path=db_path)),
    }
