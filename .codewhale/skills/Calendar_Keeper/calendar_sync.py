"""
Calendar Keeper — 同步引擎（远程日历 ↔ 本地缓存），按成员 + 域分别同步。

每个成员的活动（schedule 域，kind=event）与待办（tasks 域，kind=task）各自独立：
    存储      paths.member_store(member, "schedule"|"tasks")
    状态      paths.member_sync_state(member, "schedule"|"tasks")  {last_refresh,last_error}
    provider  members.sync_pref(member, domain) → providers.get(domain, name)；无则本地模式

调用契约：
    calendar_tick()   传输层在已注册成员消息到达后调用。enabled + 遍历每个成员 × 每个域，
                      对启用且 provider 就绪的域按各自节流刷新。单成员/单域失败被隔离，
                      永不抛异常。本地模式成员（无 sync 偏好）一律跳过。
    refresh_domain()  刷新某成员某域：先推后拉 + 对账，写该域状态。
    force_sync()      cal-sync。给 member → 刷新其所有启用域；否则回退单库全局 provider。
    status()          cal-status。给 member+domain → 该域状态；否则单库全局视图。

兼容路径（测试与简单场景）：refresh()/push_pending()/status() 在不给 member 时走
"单库 + 模块全局 provider"，provider 模块全局默认 = calendar_provider，可注入/monkeypatch。

状态文件（不入备份、不入 git）。测试钩子：CALENDAR_STATE_DIR（全局状态目录）、
CALENDAR_CONFIG（替代 config.json）、DATA_ROOT（数据根，经 paths）。
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
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Agent_Runtime"))
import cal_db
import calendar_provider as provider          # 模块全局默认 provider（可注入/monkeypatch）
import providers as _providers
import members as _members
import paths as _paths

_FALLBACK_CFG = {
    "enabled": False,
    "lookahead_days": 10,
    "refresh_minutes": 15,
}

_DOMAIN_KIND = {"schedule": "event", "tasks": "task"}


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


# ── 状态文件 ────────────────────────────────────────────────

def _global_state_file() -> Path:
    """兼容老路径的全局状态文件（无 member 的 refresh/status 用）。"""
    return Path(os.environ.get("CALENDAR_STATE_DIR") or (ROOT / "data")) \
        / ".calendar_state.json"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, st: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _record_error(path: Path, msg: str) -> None:
    try:
        st = _load_state(path)
        st["last_error"] = msg
        _save_state(path, st)
    except Exception:
        pass


# ── provider 解析 ───────────────────────────────────────────

def _registered_members() -> list[str]:
    return _members.member_names()


def provider_for(member: str, domain: str):
    """成员某域的 provider；未启用/无偏好 → None（本地模式，不推不拉）。"""
    pref = _members.sync_pref(member, domain)
    if not pref or not pref.get("enabled"):
        return None
    return _providers.get(domain, pref.get("provider"))


# ── 推送（先推） ────────────────────────────────────────────

def push_pending(db_path=None, prov=None, kind: str | None = None):
    """推送待同步行（synced=0）。prov 缺省用模块全局 provider；kind 给定则只推该类。

    逐行隔离：单行失败不影响其余，留待下轮重试。返回 (成功行数, 错误列表)。
    provider 未配置 → (0, [])。
    """
    p = prov if prov is not None else provider
    try:
        if not p.is_configured():
            return 0, []
    except Exception:
        return 0, []
    pushed = 0
    errors: list[str] = []
    for row in cal_db.pending(db_path=db_path):
        if kind and row["kind"] != kind:
            continue
        try:
            if row["status"] == "active" and not row["uid"]:
                if row["kind"] == "event":
                    uid = p.create_event(row)
                else:
                    uid = p.create_task(row)
                cal_db.mark_synced(row["id"], uid=uid, db_path=db_path)
            elif row["status"] == "done":
                if row["kind"] == "task" and row["uid"]:
                    p.complete_task(row["uid"])
                cal_db.mark_synced(row["id"], db_path=db_path)
            elif row["status"] == "cancelled":
                if row["uid"]:
                    if row["kind"] == "event":
                        p.delete_event(row["uid"])
                    else:
                        p.delete_task(row["uid"])
                cal_db.mark_synced(row["id"], db_path=db_path)
            else:
                # active 且已有 uid（无编辑路径）：标记已同步，避免卡死在待推送
                cal_db.mark_synced(row["id"], db_path=db_path)
            pushed += 1
        except Exception as e:
            errors.append(f"#{row['id']} {row['title'][:20]}: {e}")
    return pushed, errors


# ── 拉取 + 对账（后拉），按域分半 ────────────────────────────

def _sync_events(db_path, p, today: date, horizon: date) -> tuple[int, list[str]]:
    """活动半：拉窗口活动，按 uid 合并（remote wins）；窗口内远端消失 → 本地取消。"""
    errors: list[str] = []
    n = 0
    try:
        time_min = datetime.combine(today, dtime.min).astimezone() \
            .isoformat(timespec="seconds")
        time_max = datetime.combine(horizon, dtime(23, 59, 59)).astimezone() \
            .isoformat(timespec="seconds")
        events = p.list_events(time_min, time_max)
        seen = set()
        for e in events:
            cal_db.upsert_remote("event", e["uid"], {
                "title": e["title"], "start_at": e["start"], "end_at": e["end"],
                "all_day": 1 if e["all_day"] else 0,
                "location": e["location"], "notes": e["notes"],
                "status": "active"}, db_path=db_path)
            seen.add(e["uid"])
        n = len(seen)
        t_iso, h_iso = today.isoformat(), horizon.isoformat()
        for row in cal_db.synced_active("event", db_path=db_path):
            d = row["start_at"][:10]
            if d and t_iso <= d <= h_iso and row["uid"] not in seen:
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
    except Exception as e:
        errors.append(f"events: {e}")
    return n, errors


def _sync_tasks(db_path, p) -> tuple[int, list[str]]:
    """待办半：全量拉，按 uid 合并；远端完成 → 本地完成，远端消失 → 本地取消。"""
    errors: list[str] = []
    n = 0
    try:
        tasks = p.list_tasks()
        seen = set()
        for t in tasks:
            cal_db.upsert_remote("task", t["uid"], {
                "title": t["title"], "start_at": t["due"], "end_at": "",
                "all_day": 0, "location": "", "notes": t["notes"],
                "status": "done" if t["done"] else "active"}, db_path=db_path)
            seen.add(t["uid"])
        n = len(seen)
        for row in cal_db.synced_active("task", db_path=db_path):
            if row["uid"] not in seen:
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
    except Exception as e:
        errors.append(f"tasks: {e}")
    return n, errors


# ── 兼容路径：单库 + 全局 provider（两半都跑，全局状态） ──────

def refresh(db_path=None, today: date | None = None,
            now: datetime | None = None, prov=None) -> dict:
    """单库刷新（活动 + 待办两半），写全局状态。错误收集进返回值，不抛出。"""
    now = now or datetime.now()
    today = today or now.date()
    horizon = today + timedelta(days=int(CFG.get("lookahead_days", 10)))
    errors: list[str] = []

    pushed, push_errors = push_pending(db_path=db_path, prov=prov)
    errors.extend(push_errors)
    p = prov if prov is not None else provider
    n_events, e1 = _sync_events(db_path, p, today, horizon)
    n_tasks, e2 = _sync_tasks(db_path, p)
    errors.extend(e1)
    errors.extend(e2)

    sf = _global_state_file()
    st = _load_state(sf)
    st["last_refresh"] = now.isoformat(timespec="seconds")
    st["last_error"] = "; ".join(errors[:5]) if errors else None
    _save_state(sf, st)
    return {"pushed": pushed, "events": n_events, "tasks": n_tasks,
            "errors": errors}


# ── 按成员 + 域刷新（真·分库分 provider） ────────────────────

def refresh_domain(member: str, domain: str, *, db_path=None, prov=None,
                   state_path=None, today: date | None = None,
                   now: datetime | None = None) -> dict:
    """刷新某成员某域（schedule=活动 / tasks=待办）：先推后拉 + 对账，写该域状态。"""
    if domain not in _DOMAIN_KIND:
        raise ValueError(f"domain 必须是 {tuple(_DOMAIN_KIND)}")
    now = now or datetime.now()
    today = today or now.date()
    horizon = today + timedelta(days=int(CFG.get("lookahead_days", 10)))
    db_path = db_path or str(_paths.member_store(member, domain))
    p = prov if prov is not None else provider_for(member, domain)
    state_path = state_path or _paths.member_sync_state(member, domain)
    kind = _DOMAIN_KIND[domain]
    errors: list[str] = []

    pushed, push_errors = push_pending(db_path=db_path, prov=p, kind=kind)
    errors.extend(push_errors)
    if domain == "schedule":
        n, e = _sync_events(db_path, p, today, horizon)
    else:
        n, e = _sync_tasks(db_path, p)
    errors.extend(e)

    st = _load_state(state_path)
    st["last_refresh"] = now.isoformat(timespec="seconds")
    st["last_error"] = "; ".join(errors[:5]) if errors else None
    _save_state(state_path, st)
    return {"pushed": pushed, "synced": n, "errors": errors}


def calendar_tick(now: datetime | None = None) -> bool:
    """已注册成员消息到达后调用。遍历成员 × 域，按各自节流静默刷新。永不抛异常。"""
    try:
        if not CFG.get("enabled"):
            return False
        now = now or datetime.now()
        throttle = float(CFG.get("refresh_minutes", 15)) * 60
        ran = False
        for member in _registered_members():
            for domain in ("schedule", "tasks"):
                state_path = _paths.member_sync_state(member, domain)
                try:
                    p = provider_for(member, domain)
                    if p is None or not p.is_configured():
                        continue
                    last = _load_state(state_path).get("last_refresh")
                    if last:
                        try:
                            if (now - datetime.fromisoformat(last)).total_seconds() < throttle:
                                continue
                        except ValueError:
                            pass
                    refresh_domain(member, domain, prov=p, state_path=state_path,
                                   today=now.date(), now=now)
                    ran = True
                except Exception as e:
                    _record_error(state_path, str(e))
        return ran
    except Exception:
        return False


def force_sync(member: str | None = None, domain: str | None = None,
               db_path=None) -> dict | None:
    """cal-sync。给 member → 刷新其启用域（aggregate）；否则单库全局 provider 路径。

    provider 全未配置 → None（CLI 据此提示未配置）。
    """
    if member:
        domains = [domain] if domain else ["schedule", "tasks"]
        agg = {"pushed": 0, "synced": 0, "errors": [], "ran": 0}
        for d in domains:
            p = provider_for(member, d)
            try:
                if p is None or not p.is_configured():
                    continue
            except Exception:
                continue
            r = refresh_domain(member, d, prov=p)
            agg["pushed"] += r["pushed"]
            agg["synced"] += r.get("synced", 0)
            agg["errors"].extend(r["errors"])
            agg["ran"] += 1
        return agg if agg["ran"] else None
    # 兼容：单库全局 provider
    try:
        if not provider.is_configured():
            return None
    except Exception:
        return None
    return refresh(db_path=db_path)


def status(member: str | None = None, domain: str | None = None,
           db_path=None) -> dict:
    """cal-status。给 member+domain → 该域状态；否则单库 + 全局 provider 视图。"""
    if member and domain:
        st = _load_state(_paths.member_sync_state(member, domain))
        p = provider_for(member, domain)
        try:
            configured = bool(p.is_configured()) if p else False
        except Exception:
            configured = False
        store = db_path or str(_paths.member_store(member, domain))
        return {
            "enabled": bool(CFG.get("enabled")),
            "configured": configured,
            "last_refresh": st.get("last_refresh"),
            "last_error": st.get("last_error"),
            "pending": len(cal_db.pending(db_path=store)),
        }
    st = _load_state(_global_state_file())
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
