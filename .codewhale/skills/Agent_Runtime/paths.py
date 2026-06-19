"""
Family Assistant — 磁盘布局解析（数据落盘位置的单一事实来源）

所有 skill 经本模块取数据路径，集中管理 data/ 下的分区：

    data/<成员目录>/schedule/schedule.db   成员活动（events），私有
    data/<成员目录>/tasks/tasks.db         成员待办（tasks），私有
    data/<成员目录>/notes/notes.db         成员备忘 + notes/YYYY-MM/ 图片，私有
    data/<成员目录>/inbox/YYYY-MM/          来图暂存（按发送成员归属）
    data/Family/ledger.db                   家庭账本（收支/定期/划转/报税/汇率/文档）
    data/Family/receipts/YYYY-MM/           票据图片
    data/Family/documents/<doc_type>/       长期文档（家庭与成员）

config.json：data_root（默认 data）、family_dir_name（默认 Family）。
测试钩子：环境变量 DATA_ROOT 覆盖数据根（优先于 config）。
成员目录名来自 members.member_dir_name（members.json 的 dir 字段，缺省 slug）。

存储里的文件链接（receipt_path / file_path / source_image）一律存 data_root 的
相对 posix 路径（如 'Family/receipts/2026-06/x.jpg'），用 to_rel / resolve_rel
转换，换工作目录也不失效。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

# 本文件位于 .codewhale/skills/Agent_Runtime/ ，向上 3 级到项目根
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 members

import members as _members


def _config() -> dict:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def data_root() -> Path:
    """数据根目录。DATA_ROOT 环境变量优先（测试用），否则 config.data_root，回退 data/。"""
    env = os.environ.get("DATA_ROOT")
    if env:
        return Path(env)
    return ROOT / (_config().get("data_root") or "data")


def _family_name() -> str:
    return _config().get("family_dir_name") or "Family"


# ── 家庭共享 ────────────────────────────────────────────────

def family_dir() -> Path:
    return data_root() / _family_name()


def family_ledger() -> Path:
    """家庭账本 DB（收支/定期/划转/报税/汇率/文档）。"""
    return family_dir() / "ledger.db"


def family_receipts_dir(dt: date | None = None) -> Path:
    """票据按月分目录 Family/receipts/YYYY-MM/，不存在则创建。"""
    d = family_dir() / "receipts" / (dt or date.today()).strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d


def family_documents_dir(doc_type: str) -> Path:
    """文档按类型分目录 Family/documents/<doc_type>/，不存在则创建。"""
    d = family_dir() / "documents" / (doc_type or "other")
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 成员私有 ────────────────────────────────────────────────

_DOMAINS = {"schedule": "schedule.db", "tasks": "tasks.db", "notes": "notes.db"}


def member_dir(member: str) -> Path:
    return data_root() / _members.member_dir_name(member)


def member_store_dir(member: str, domain: str) -> Path:
    if domain not in _DOMAINS:
        raise ValueError(f"domain 必须是 {tuple(_DOMAINS)}")
    return member_dir(member) / domain


def member_store(member: str, domain: str) -> Path:
    """成员某 domain 的 DB 文件路径（schedule/tasks/notes）。父目录由 DB 连接时建。"""
    return member_store_dir(member, domain) / _DOMAINS[domain]


def member_sync_state(member: str, domain: str) -> Path:
    """成员某 domain 的同步状态文件（与 store 同目录，不入备份）。"""
    return member_store_dir(member, domain) / ".sync_state.json"


def member_inbox_dir(member: str, dt: date | None = None) -> Path:
    """来图暂存 data/<成员>/inbox/YYYY-MM/，不存在则创建。"""
    d = member_dir(member) / "inbox" / (dt or date.today()).strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d


def member_domain_image_dir(member: str, domain: str, dt: date | None = None) -> Path:
    """某域来图按月目录 data/<成员>/<域>/YYYY-MM/（notes/schedule/tasks 通用），不存在则创建。"""
    d = member_store_dir(member, domain) / (dt or date.today()).strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d


def member_notes_image_dir(member: str, dt: date | None = None) -> Path:
    """备忘图片 data/<成员>/notes/YYYY-MM/，不存在则创建。"""
    return member_domain_image_dir(member, "notes", dt)


# ── 文件链接：data_root 相对 posix ──────────────────────────

def to_rel(p: str | Path) -> str:
    """绝对/相对路径 → 相对 data_root 的 posix 字符串。

    已是 data_root 相对形式（不在 root 下解析）则原样返回 posix。
    """
    pp = Path(p)
    root = data_root().resolve()
    try:
        ap = pp if pp.is_absolute() else (Path.cwd() / pp)
        return ap.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return pp.as_posix()


def resolve_rel(rel: str) -> Path:
    """data_root 相对路径 → 绝对路径。"""
    return data_root() / rel
