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
    refresh_domain()  刷新某成员某域：先推后拉 + 对账，写该域状态。活动窗口 = _event_window()
                      （过去 sync_past_days 天 ~ 未来 sync_horizon_days 天）。
    refresh_range()   按需拉任意窗口（含更久远的过去）进本地，只拉不推、不写状态。
                      cal-list --from/--to 的历史查询走它。
    verify_domain()   只读校验某成员某域本地↔远端一致性（分桶：待推送/远端缺失/
                      本地缺失/字段漂移；60s 新鲜行豁免）。永不抛。
    verify_and_heal() 校验→不一致则 refresh_domain 修复→复检。CLI 每个 cal-* 命令
                      收尾调用（无节流）。永不抛。
    force_sync()      cal-sync。给 member → 刷新其所有启用域；否则回退单库全局 provider。
    status()          cal-status。给 member+domain → 该域状态；否则单库全局视图。

兼容路径（测试与简单场景）：refresh()/push_pending()/status() 在不给 member 时走
"单库 + 模块全局 provider"，provider 模块全局默认 = calendar_provider，可注入/monkeypatch。
sync_for_query() 保留兼容（cal-list 主路径已改为 verify_and_heal）。

状态文件（不入备份、不入 git）。测试钩子：CALENDAR_STATE_DIR（全局状态目录）、
CALENDAR_CONFIG（替代 config.json）、DATA_ROOT（数据根，经 paths）。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
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
    "query_refresh_seconds": 60,   # sync_for_query 节流（兼容保留；主路径=每操作校验，无节流）
    "sync_horizon_days": 90,       # 远端拉取窗口（未来侧，独立于 lookahead_days）
    "sync_past_days": 365,         # 远端拉取窗口（过去侧）；更早的历史走 refresh_range 按需拉
}

_DOMAIN_KIND = {"schedule": "event", "tasks": "task"}

# 校验/对账的新鲜保护窗：updated_at 距今不足此秒数的行免判。
# Google list API 非严格读写一致——刚推送的行可能短暂缺席远端列表，
# 无此保护会被误判"远端缺失"甚至被对账环节误取消。
_VERIFY_FRESH_SECONDS = 60


def _window_iso(day: date, end_of_day: bool = False) -> str:
    """日期 → 带本地时区偏移的 ISO 时刻（provider 的 timeMin/timeMax 参数）。

    不用 naive.astimezone()：Windows 上它走 C 运行时的 localtime，1970 之前的
    时刻（时区西于 UTC 时 1970-01-01 本地 = 1969 UTC）抛 OSError [Errno 22]，
    整段历史拉取会静默退化成一条 error。固定偏移拼装则任意年份都成立。
    """
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    t = dtime(23, 59, 59) if end_of_day else dtime.min
    return datetime.combine(day, t).replace(tzinfo=timezone(off)) \
        .isoformat(timespec="seconds")


def _is_fresh(updated_at: str, now: datetime) -> bool:
    """行的 updated_at 在保护窗内 → True（解析失败按不新鲜处理）。"""
    try:
        return (now - datetime.fromisoformat(updated_at)).total_seconds() \
            < _VERIFY_FRESH_SECONDS
    except (TypeError, ValueError):
        return False


def _event_window(today: date) -> tuple[date, date]:
    """常规活动拉取/校验窗口 (起, 止)：过去 sync_past_days 天 ~ 未来 sync_horizon_days 天。

    未来侧不受 lookahead_days（仅上下文注入与列表默认窗口）限制；过去侧让"上个月游了几次课"
    这类历史查询直接命中缓存。更早的历史不常驻，由 refresh_range() 按需拉。
    """
    past = max(int(CFG.get("sync_past_days", 365) or 0), 0)
    ahead = max(int(CFG.get("sync_horizon_days", 90) or 0),
                int(CFG.get("lookahead_days", 10) or 0))
    return today - timedelta(days=past), today + timedelta(days=ahead)


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
    return Path(os.environ.get("CALENDAR_STATE_DIR") or _paths.data_root()) \
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

def _sync_events(db_path, p, win_start: date, win_end: date,
                 now: datetime | None = None) -> tuple[int, list[str]]:
    """活动半：拉 [win_start, win_end] 内活动，按 uid 合并（remote wins）；
    窗口内远端消失 → 本地取消。窗口可含过去（历史活动同样入本地缓存）。

    新鲜保护：updated_at 在 _VERIFY_FRESH_SECONDS 内的行不参与"远端消失→取消"
    对账（Google list 读写延迟会让刚推送的行短暂缺席）。
    """
    now = now or datetime.now()
    today, horizon = win_start, win_end
    errors: list[str] = []
    n = 0
    try:
        time_min = _window_iso(today)
        time_max = _window_iso(horizon, end_of_day=True)
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
            if d and t_iso <= d <= h_iso and row["uid"] not in seen \
                    and not _is_fresh(row["updated_at"], now):
                cal_db.set_status(row["id"], "cancelled", from_remote=True,
                                  db_path=db_path)
    except Exception as e:
        errors.append(f"events: {e}")
    return n, errors


def _sync_tasks(db_path, p, now: datetime | None = None) -> tuple[int, list[str]]:
    """待办半：全量拉，按 uid 合并；远端完成 → 本地完成，远端消失 → 本地取消。

    新鲜保护同 _sync_events：刚推送的行免于"远端消失→取消"误判。
    """
    now = now or datetime.now()
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
            if row["uid"] not in seen and not _is_fresh(row["updated_at"], now):
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
    win_start, horizon = _event_window(today)
    errors: list[str] = []

    pushed, push_errors = push_pending(db_path=db_path, prov=prov)
    errors.extend(push_errors)
    p = prov if prov is not None else provider
    n_events, e1 = _sync_events(db_path, p, win_start, horizon, now)
    n_tasks, e2 = _sync_tasks(db_path, p, now)
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
    win_start, horizon = _event_window(today)
    db_path = db_path or str(_paths.member_store(member, domain))
    p = prov if prov is not None else provider_for(member, domain)
    state_path = state_path or _paths.member_sync_state(member, domain)
    kind = _DOMAIN_KIND[domain]
    errors: list[str] = []

    pushed, push_errors = push_pending(db_path=db_path, prov=p, kind=kind)
    errors.extend(push_errors)
    if domain == "schedule":
        n, e = _sync_events(db_path, p, win_start, horizon, now)
    else:
        n, e = _sync_tasks(db_path, p, now)
    errors.extend(e)

    st = _load_state(state_path)
    st["last_refresh"] = now.isoformat(timespec="seconds")
    st["last_error"] = "; ".join(errors[:5]) if errors else None
    _save_state(state_path, st)
    return {"pushed": pushed, "synced": n, "errors": errors}


def refresh_range(member: str, domain: str, win_start: date, win_end: date, *,
                  db_path=None, prov=None, now: datetime | None = None) -> dict:
    """按需拉任意窗口（**含任意久远的过去**）进本地缓存，只拉不推。

    常驻窗口（_event_window）之外的历史查询用：cal-list --from/--to 先调它把该段
    远端活动灌进本地，再读本地。待办域无窗口概念（list_tasks 恒全量），忽略窗口。
    远端查询失败 → errors 非空，本地照旧可读（拉取异常在 _sync_events 内被捕获，
    "远端消失→取消"对账不会在空结果上误跑）。永不抛。
    """
    if domain not in _DOMAIN_KIND:
        raise ValueError(f"domain 必须是 {tuple(_DOMAIN_KIND)}")
    now = now or datetime.now()
    db_path = db_path or str(_paths.member_store(member, domain))
    p = prov if prov is not None else provider_for(member, domain)
    try:
        if p is None or not p.is_configured():
            return {"mode": "local", "synced": 0, "errors": []}
    except Exception:
        return {"mode": "local", "synced": 0, "errors": []}
    if domain == "schedule":
        n, errors = _sync_events(db_path, p, win_start, win_end, now)
    else:
        n, errors = _sync_tasks(db_path, p, now)
    return {"mode": "remote", "synced": n, "errors": errors}


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


def sync_for_query(member: str, domain: str | None = None,
                   now: datetime | None = None) -> bool:
    """拉远端 → 本地，按短节流（query_refresh_seconds）。兼容保留。

    历史：曾是 cal-list 的查询前拉取；现 cal-list 主路径改走 verify_and_heal
    （每操作无节流校验），本函数仅测试/外部调用方使用。
    本地模式成员/未配置域跳过。永不抛。
    """
    try:
        if not member or not CFG.get("enabled"):
            return False
        now = now or datetime.now()
        throttle = float(CFG.get("query_refresh_seconds", 60))
        domains = [domain] if domain else ["schedule", "tasks"]
        ran = False
        for d in domains:
            if d not in _DOMAIN_KIND:
                continue
            state_path = _paths.member_sync_state(member, d)
            try:
                p = provider_for(member, d)
                if p is None or not p.is_configured():
                    continue
                last = _load_state(state_path).get("last_refresh")
                if last:
                    try:
                        if (now - datetime.fromisoformat(last)).total_seconds() < throttle:
                            continue
                    except ValueError:
                        pass
                refresh_domain(member, d, prov=p, state_path=state_path,
                               today=now.date(), now=now)
                ran = True
            except Exception as e:
                _record_error(state_path, str(e))
        return ran
    except Exception:
        return False


# ── 校验（每操作本地↔远端一致性核对） ────────────────────────────

def _norm(v) -> str:
    return (v or "").strip()


def _fields_differ(kind: str, row: dict, remote: dict) -> bool:
    """本地行 vs 远端条目核心字段是否漂移。

    待办：synced_active 只给 active 行 → 远端 done 即漂移（用户在手机上划掉），
    修复时 remote-wins 会把本地也置 done。
    活动 end 为空时远端曾默认 +1h → 首次校验判漂移，一轮修复即收敛，属预期。
    """
    if kind == "event":
        return (_norm(row["title"]) != _norm(remote.get("title"))
                or _norm(row["start_at"]) != _norm(remote.get("start"))
                or _norm(row["end_at"]) != _norm(remote.get("end"))
                or int(row["all_day"] or 0) != int(bool(remote.get("all_day")))
                or _norm(row["location"]) != _norm(remote.get("location"))
                or _norm(row["notes"]) != _norm(remote.get("notes")))
    return (bool(remote.get("done"))
            or _norm(row["title"]) != _norm(remote.get("title"))
            or _norm(row["start_at"]) != _norm(remote.get("due"))
            or _norm(row["notes"]) != _norm(remote.get("notes")))


def verify_domain(member: str, domain: str, *, db_path=None, prov=None,
                  now: datetime | None = None) -> dict:
    """只读校验某成员某域：查本地 + 查远端，分桶报告差异。永不抛。

    返回：
        {"mode": "local"}                          未配置/本地模式（调用方静默跳过）
        {"mode": "error", "error": str}            远端查询失败
        {"mode": "remote", "in_sync": bool,
         "pending": [...], "local_only": [...],    待推送 / 远端缺失
         "remote_only": [...], "drift": [...],     本地缺失 / 字段漂移
         "local_total": n, "remote_total": n}
    桶元素为人类可读短句（"#12 游泳课"），CLI 直接拼 verdict。
    新鲜保护（_VERIFY_FRESH_SECONDS）豁免 local_only/drift 判定；
    pending 不豁免——推送失败必须立刻可见。
    """
    try:
        if domain not in _DOMAIN_KIND:
            raise ValueError(f"domain 必须是 {tuple(_DOMAIN_KIND)}")
        p = prov if prov is not None else provider_for(member, domain)
        try:
            if p is None or not p.is_configured():
                return {"mode": "local"}
        except Exception:
            return {"mode": "local"}
        now = now or datetime.now()
        today = now.date()
        kind = _DOMAIN_KIND[domain]
        db_path = db_path or str(_paths.member_store(member, domain))

        if kind == "event":
            win_start, horizon = _event_window(today)   # 与 refresh 同窗口，历史行也参与校验
            time_min = _window_iso(win_start)
            time_max = _window_iso(horizon, end_of_day=True)
            remote = {e["uid"]: e for e in p.list_events(time_min, time_max)}
        else:
            remote = {t["uid"]: t for t in p.list_tasks()}

        local_uids = cal_db.uids(kind, db_path=db_path)
        pend = [f"#{r['id']} {r['title'][:20]}"
                for r in cal_db.pending(db_path=db_path) if r["kind"] == kind]

        local_only: list[str] = []
        drift: list[str] = []
        for row in cal_db.synced_active(kind, db_path=db_path):
            if kind == "event":
                d = row["start_at"][:10]
                if not d or not (win_start.isoformat() <= d <= horizon.isoformat()):
                    continue                      # 窗口外不参与（拉取也拉不到）
            if _is_fresh(row["updated_at"], now):
                continue                          # 读写延迟保护
            r = remote.get(row["uid"])
            if r is None:
                local_only.append(f"#{row['id']} {row['title'][:20]}")
            elif _fields_differ(kind, row, r):
                drift.append(f"#{row['id']} {row['title'][:20]}")

        remote_only = [_norm(remote[u].get("title"))[:20] or "(无标题)"
                       for u in remote if u not in local_uids]
        in_sync = not (pend or local_only or remote_only or drift)
        return {"mode": "remote", "in_sync": in_sync, "pending": pend,
                "local_only": local_only, "remote_only": remote_only,
                "drift": drift, "local_total": len(local_uids),
                "remote_total": len(remote)}
    except Exception as e:
        return {"mode": "error", "error": str(e)}


def verify_and_heal(member: str, domain: str, *, db_path=None, prov=None,
                    now: datetime | None = None) -> dict:
    """校验；不一致则 refresh_domain（先推后拉+对账，remote wins）后复检。永不抛。

    返回最终 verify_domain 结果 + healed（修复后复检通过才 True）
    + heal_pushed / heal_synced（未跑修复时为 0）。
    复检与修复共用同一 prov/db_path；修复刚写入的行 updated_at 新鲜 →
    复检的新鲜保护自然豁免它们，静态远端快照下也能收敛。
    """
    first = verify_domain(member, domain, db_path=db_path, prov=prov, now=now)
    if first.get("mode") != "remote" or first.get("in_sync"):
        return {**first, "healed": False, "heal_pushed": 0, "heal_synced": 0}
    try:
        healed = refresh_domain(member, domain, db_path=db_path, prov=prov,
                                now=now)
    except Exception as e:
        return {**first, "healed": False, "heal_pushed": 0, "heal_synced": 0,
                "heal_error": str(e)}
    second = verify_domain(member, domain, db_path=db_path, prov=prov, now=now)
    if second.get("mode") != "remote":
        second = first                       # 复检查询失败 → 报首轮差异
    return {**second, "healed": bool(second.get("in_sync")),
            "heal_pushed": healed.get("pushed", 0),
            "heal_synced": healed.get("synced", 0)}


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
