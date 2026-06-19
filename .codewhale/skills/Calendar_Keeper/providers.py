"""
Calendar Keeper — provider 注册表

按 (domain, provider_name) 取 provider 实现。domain ∈ {schedule, tasks}。
成员的同步偏好（members.json sync 块）给出 provider 名，引擎据此选实现。

当前内置：
    schedule + google_calendar → calendar_provider（日历事件半契约）
    tasks    + google_tasks    → calendar_provider（Tasks 待办半契约）

换/加平台：实现对应半契约的模块，在 _REGISTRY 注册即可，引擎零改动。
未知或本地（provider 名不在注册表）→ get 返回 None（= 本地模式，不推不拉）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calendar_provider

# 同一 google 模块实现两半（list_events/create_event/... 与 list_tasks/create_task/...）
_REGISTRY = {
    ("schedule", "google_calendar"): calendar_provider,
    ("tasks", "google_tasks"): calendar_provider,
}


def get(domain: str, provider_name: str):
    """返回 (domain, provider_name) 的 provider 模块/对象；未注册返回 None。"""
    return _REGISTRY.get((domain, provider_name or ""))
