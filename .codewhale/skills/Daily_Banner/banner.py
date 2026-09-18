"""
Daily Banner — 每日早报（FAST_TICKS 钩子）。设计：docs/superpowers/specs/2026-09-17-daily-banner-design.md

每拍先做廉价闸门（开关 / 时间窗 / 今天已推），命中才起守护线程取数 + 成文 + 推送：
取数与 LLM 要 10–30 秒，不能堵传输层轮询。
状态 data/.state/.daily_banner_state.json：{频道: {成员: 最后推送日期}}；没送达不记，进程内退避 RETRY_S。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from pathlib import Path

import jsonfile
import paths as _paths
from members import load_members

_log = logging.getLogger("familyassist.banner")

ROOT = Path(__file__).resolve().parents[3]
STATE_NAME = ".daily_banner_state.json"
RETRY_S = 600
_DEFAULTS = {"enabled": False, "time": "08:20", "catchup_until": "12:00", "lookahead_days": 3}

_lock = threading.Lock()                       # 守 _running / _last_try / 状态文件读改写
_running: set[tuple[str, str]] = set()         # (频道, 成员) 正在跑
_last_try: dict[tuple[str, str], float] = {}   # (频道, 成员) → 上次启动 monotonic


def load_cfg() -> dict:
    cfg = jsonfile.load_dict(ROOT / "config.json").get("daily_banner")
    return {**_DEFAULTS, **cfg} if isinstance(cfg, dict) else dict(_DEFAULTS)


CFG = load_cfg()


def _minutes(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def in_window(now: datetime, cfg: dict) -> bool:
    """[time, catchup_until)：晚启动补推，午后启动当天跳过。"""
    mins = now.hour * 60 + now.minute
    return _minutes(cfg["time"]) <= mins < _minutes(cfg["catchup_until"])


def recipients(channel: str, members_path: Path | None = None) -> dict[str, list[str]]:
    """{成员: 本频道 id 列表}，仅 members.json 里 "banner": true 的成员。"""
    out = {}
    for name, b in load_members(members_path).items():
        if isinstance(b, dict) and b.get("banner") is True:
            ids = [str(i) for i in (b.get(channel) or [])]
            if ids:
                out[name] = ids
    return out


def _state_path() -> Path:
    return _paths.state_file(STATE_NAME)


def _load_state() -> dict:
    return jsonfile.load_dict(_state_path())


def _mark_sent(channel: str, member: str, day: date) -> None:
    with _lock:
        st = _load_state()
        chan = st.get(channel) if isinstance(st.get(channel), dict) else {}
        chan[member] = day.isoformat()
        st[channel] = chan
        jsonfile.save(_state_path(), st)
        _last_try.pop((channel, member), None)     # 退避只罚失败


def build(member: str, day: date, cfg: dict) -> str:
    raise NotImplementedError


def deliver(push_text, channel: str, member: str, ids: list[str], day: date, cfg: dict) -> bool:
    """成文并推给该成员本频道全部 id；至少一个送达才记状态。永不抛。"""
    try:
        text = build(member, day, cfg)
        ok = False
        for cid in ids:
            try:
                ok = (push_text(cid, text) is not False) or ok
            except Exception:
                _log.exception("早报推送失败: %s", cid)
        if ok:
            _mark_sent(channel, member, day)
        return ok
    except Exception:
        _log.exception("早报生成失败: %s", member)
        return False
    finally:
        with _lock:
            _running.discard((channel, member))


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name="daily-banner").start()


def tick(push_text, channel: str, *, now: datetime | None = None, cfg: dict | None = None,
         members_path: Path | None = None, spawn=None) -> list[str]:
    """FAST_TICKS 钩子。返回本拍启动投递的成员。"""
    cfg = cfg or CFG
    if not cfg.get("enabled"):
        return []
    now = now or datetime.now()
    if not in_window(now, cfg):
        return []
    sent = _load_state().get(channel) or {}
    started = []
    for member, ids in recipients(channel, members_path).items():
        if sent.get(member) == now.date().isoformat():
            continue
        key = (channel, member)
        with _lock:
            if key in _running or time.monotonic() - _last_try.get(key, float("-inf")) < RETRY_S:
                continue
            _running.add(key)
            _last_try[key] = time.monotonic()
        (spawn or _spawn)(deliver, push_text, channel, member, ids, now.date(), cfg)
        started.append(member)
    return started
