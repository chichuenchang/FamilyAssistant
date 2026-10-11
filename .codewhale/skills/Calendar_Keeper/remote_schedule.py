"""
Calendar Keeper — 远程日历直连层（无本地缓存）。

每次操作按成员 + 域取 provider，直接读写远端（活动=schedule 域，待办=tasks 域）。
远端失败抛 RuntimeError 直达调用方，不缓冲、不重试。
未配 provider / 总开关关 / 凭据缺 → NotConfigured（其他成员占位：members.json 加 sync 块即接入）。

对外 id = e:<uid>（活动）/ t:<uid>（待办），uid 为远端原值。
条目 {id, kind, title, start_at, end_at, all_day, location, notes, status(active|done)}。
测试钩子：CALENDAR_CONFIG（替代 config.json）。
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import jsonfile
import members as _members
import providers as _providers

ROOT = Path(__file__).resolve().parents[3]
EPOCH = "1970-01-01"
_DOMAIN = {"event": "schedule", "task": "tasks"}
_PREFIX = {"event": "e", "task": "t"}


class NotConfigured(Exception):
    pass


class NotFound(LookupError):
    pass


def _load_cfg() -> dict:
    cfg_path = Path(os.environ.get("CALENDAR_CONFIG") or (ROOT / "config.json"))
    cal = jsonfile.load_dict(cfg_path).get("calendar")
    return {"enabled": False, "lookahead_days": 10, **(cal if isinstance(cal, dict) else {})}


CFG = _load_cfg()


def provider_for(member: str, domain: str):
    """成员某域的 provider；总开关关 / 无 sync 偏好 / 未启用 → None。"""
    if not CFG.get("enabled"):
        return None
    pref = _members.sync_pref(member, domain)
    if not pref or not pref.get("enabled"):
        return None
    return _providers.get(domain, pref.get("provider"))


def _prov(member: str, kind: str):
    if not member:
        raise ValueError("按成员私有日历，需要 --member")
    p = provider_for(member, _DOMAIN[kind])
    if p is None or not p.is_configured():
        raise NotConfigured(f"{member} 未配置远程日历")
    return p


def ref(kind: str, uid: str) -> str:
    return f"{_PREFIX[kind]}:{uid}"


def parse_ref(raw) -> tuple[str, str]:
    """e:<uid> / t:<uid>（可带 #）→ (kind, uid)。"""
    prefix, sep, uid = str(raw).strip().lstrip("#").partition(":")
    kind = {"e": "event", "t": "task"}.get(prefix.lower())
    if not (sep and kind and uid):
        raise ValueError(f"id 格式不对: {raw}（照列表原样传，形如 e:… 活动 / t:… 待办）")
    return kind, uid


def _window_iso(day: date, end_of_day: bool = False) -> str:
    """日期 → 带本地时区偏移的 ISO 时刻（timeMin/timeMax）。

    不用 naive.astimezone()：Windows 上 1970 前的本地时刻抛 OSError [Errno 22]，
    整段历史拉取会失败。固定偏移拼装任意年份都成立。
    """
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    t = dtime(23, 59, 59) if end_of_day else dtime.min
    return datetime.combine(day, t).replace(tzinfo=timezone(off)).isoformat(timespec="seconds")


def _item(kind: str, raw: dict) -> dict:
    if kind == "event":
        start, end = raw["start"], raw["end"]
        if raw["all_day"] and end:      # Google 全天 end 独占 → 改成含当日；单日全天清空
            end = (date.fromisoformat(end[:10]) - timedelta(days=1)).isoformat()
            end = "" if end <= start[:10] else end
        return {"id": ref(kind, raw["uid"]), "kind": kind, "title": raw["title"],
                "start_at": start, "end_at": end, "all_day": int(bool(raw["all_day"])),
                "location": raw.get("location", ""), "notes": raw.get("notes", ""),
                "status": "active"}
    return {"id": ref(kind, raw["uid"]), "kind": kind, "title": raw["title"],
            "start_at": raw["due"], "end_at": "", "all_day": 0, "location": "",
            "notes": raw.get("notes", ""), "status": "done" if raw["done"] else "active"}


def _in_window(it: dict, start: str, end: str) -> bool:
    s = it["start_at"][:10]
    e = (it["end_at"] or it["start_at"])[:10]
    return (not end or s <= end) and (not start or e >= start)


def list_items(member: str, kind: str | None = None, start: str = "", end: str = "",
               closed: bool = False, undated: bool = False) -> list[dict]:
    """窗口内条目，start/end 'YYYY-MM-DD'，空 = 不限。undated 加无截止日待办；closed 加已完成。"""
    out: list[dict] = []
    for k in ([kind] if kind else ["event", "task"]):
        p = _prov(member, k)
        if k == "event":
            hi = (date.fromisoformat(end) if end
                  else date.today() + timedelta(days=int(CFG.get("lookahead_days") or 10)))
            raws = p.list_events(_window_iso(date.fromisoformat(start or EPOCH)),
                                 _window_iso(hi, True))
        else:
            raws = p.list_tasks()
        for it in (_item(k, r) for r in raws):
            if it["status"] == "done" and not closed:
                continue
            if it["start_at"] and _in_window(it, start, end) or (undated and not it["start_at"]):
                out.append(it)
    out.sort(key=lambda r: (r["start_at"] == "", r["start_at"], r["title"]))
    return out


def get(member: str, item_ref) -> dict | None:
    kind, uid = parse_ref(item_ref)
    p = _prov(member, kind)
    raw = p.get_event(uid) if kind == "event" else p.get_task(uid)
    return _item(kind, raw) if raw else None


def _must_get(member: str, item_ref) -> dict:
    it = get(member, item_ref)
    if it is None:
        raise NotFound(item_ref)
    return it


def add(member: str, kind: str, title: str, start_at: str = "", end_at: str = "",
        all_day: bool = False, location: str = "", notes: str = "") -> dict:
    if not title or not title.strip():
        raise ValueError("title 不能为空")
    if kind == "event" and not start_at:
        raise ValueError("活动必须带日期")
    p = _prov(member, kind)
    item = {"kind": kind, "title": title.strip(), "start_at": start_at, "end_at": end_at,
            "all_day": int(bool(all_day)), "location": location, "notes": notes,
            "status": "active"}
    uid = p.create_event(item) if kind == "event" else p.create_task(item)
    return {"id": ref(kind, uid), **item}


def update(member: str, item_ref, fields: dict) -> dict:
    """只改给了的字段：先取远端当前值合并，再整体 PATCH。"""
    it = _must_get(member, item_ref)
    if it["status"] == "done":
        raise ValueError(f"{it['id']} 已完成，不能编辑")
    merged = {**it, **fields}
    p = _prov(member, it["kind"])
    uid = parse_ref(it["id"])[1]
    (p.update_event if it["kind"] == "event" else p.update_task)(uid, merged)
    return merged


def complete(member: str, item_ref) -> dict:
    if parse_ref(item_ref)[0] == "event":
        raise ValueError(f"{item_ref} 是活动，取消请用 cal-delete")
    it = _must_get(member, item_ref)
    _prov(member, "task").complete_task(parse_ref(it["id"])[1])
    return {**it, "status": "done"}


def delete(member: str, item_ref) -> dict:
    it = _must_get(member, item_ref)
    p = _prov(member, it["kind"])
    uid = parse_ref(it["id"])[1]
    (p.delete_event if it["kind"] == "event" else p.delete_task)(uid)
    return it


def status(member: str) -> dict[str, bool]:
    out = {}
    for kind, domain in _DOMAIN.items():
        p = provider_for(member, domain)
        out[domain] = bool(p and p.is_configured())
    return out
