"""
成员注册表 — 家庭成员与频道身份的映射（data/members.json，git 不跟踪）。

文件格式（扁平 dict，整个文件就是注册表）:
    {
      "爸爸": { "telegram": ["123456789"], "wechat": ["wxid_abc"],
               "aliases": ["法定名", "Legal Name"] }
    }

为什么独立文件：姓名/法定名/频道 id 属隐私，config.json 是 git 跟踪文件，
不能进仓库。data/members.json 在 .gitignore 里，并加入 backup.include
随云备份镜像（backup-restore 在新设备上会一并恢复）。

aliases = 别名/法定名（出现在票据、合同、证件等文档里的名字），仅供 Agent
理解"文档里的名字 ↔ 家庭成员"，不参与频道闸门（resolve 只认频道 id）。

注册表只在本机用 CLI 管理（member-add / member-list / member-remove，
不在 wechat.allowed_commands 白名单内），Agent Runtime 只读。
未注册的频道 id 一律静默丢弃；注册表缺失/损坏时全部锁定（安全默认）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

# 本文件位于 .codewhale/skills/Agent_Runtime/ ，向上 3 级到项目根
ROOT = Path(__file__).resolve().parents[3]
MEMBERS_PATH = ROOT / "data" / "members.json"

CHANNELS = ("telegram", "wechat")


def load_members(members_path: Path | None = None) -> dict:
    """注册表 dict；文件缺失/损坏/格式不对返回 {}（→ 锁定）。"""
    try:
        data = json.loads((members_path or MEMBERS_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve(channel: str, channel_id, members_path: Path | None = None) -> str | None:
    """频道 id → 成员名；未注册返回 None（调用方必须静默丢弃该消息）。"""
    cid = str(channel_id or "")
    if not cid:
        return None
    for name, bindings in load_members(members_path).items():
        ids = bindings.get(channel) or [] if isinstance(bindings, dict) else []
        if cid in (str(i) for i in ids):
            return name
    return None


def member_names(members_path: Path | None = None) -> list[str]:
    """已登记成员名列表。"""
    return list(load_members(members_path).keys())


def _slug(name: str) -> str:
    """成员名 → 文件系统安全目录 slug（取首个空白分隔词，小写，去特殊字符）。"""
    tok = (name or "").strip().split()
    base = tok[0] if tok else (name or "")
    s = re.sub(r"[^0-9A-Za-z_-]+", "", base).lower()
    return s or "member"


def member_dir_name(name: str, members_path: Path | None = None) -> str:
    """成员的磁盘目录名：members.json 的 dir 字段，缺省取 slug(name)。

    名字含空格/中文/PII，不能直接当目录名 → 显式 dir 优先，回退 slug。
    """
    entry = load_members(members_path).get(name)
    if isinstance(entry, dict) and entry.get("dir"):
        return str(entry["dir"])
    return _slug(name)


def sync_pref(name: str, domain: str, members_path: Path | None = None) -> dict | None:
    """成员某 domain（schedule/tasks）的远程同步偏好。

    返回 {"provider": str, "enabled": bool}；未配置（无 sync 块或无该 domain）→ None
    （= 本地模式，不推不拉）。凭据永远不在此，走 GCAL_* 环境变量。
    """
    entry = load_members(members_path).get(name)
    if not isinstance(entry, dict):
        return None
    sync = entry.get("sync")
    if not isinstance(sync, dict):
        return None
    d = sync.get(domain)
    if not isinstance(d, dict):
        return None
    return {"provider": d.get("provider", ""), "enabled": bool(d.get("enabled", False))}


def _save_members(members: dict, members_path: Path | None = None) -> None:
    """原子写回 members.json（临时文件 + replace，写一半不毁原文件）。"""
    path = members_path or MEMBERS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(members, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_member(name: str, telegram=None, wechat=None, aliases=None,
               members_path: Path | None = None) -> None:
    """新增成员或为已有成员追加频道 id / 别名。id 或别名已属其他成员时报错。"""
    if not name:
        raise ValueError("成员名不能为空")
    new_ids = {"telegram": [str(i) for i in (telegram or [])],
               "wechat": [str(i) for i in (wechat or [])]}
    for ch in CHANNELS:
        for cid in new_ids[ch]:
            owner = resolve(ch, cid, members_path)
            if owner and owner != name:
                raise ValueError(f"{ch} id {cid} 已绑定成员 {owner}")
    new_aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
    members = load_members(members_path)
    for al in new_aliases:
        for other, b in members.items():
            if other == name:
                continue
            other_aliases = [str(x) for x in (b.get("aliases") or [])] \
                if isinstance(b, dict) else []
            if al == other or al in other_aliases:
                raise ValueError(f"别名 {al} 已属成员 {other}")
    entry = members.setdefault(name, {})
    for ch in CHANNELS:
        ids = [str(i) for i in (entry.get(ch) or [])]
        for cid in new_ids[ch]:
            if cid not in ids:
                ids.append(cid)
        if ids:
            entry[ch] = ids
    if new_aliases:
        als = [str(a) for a in (entry.get("aliases") or [])]
        for al in new_aliases:
            if al not in als:
                als.append(al)
        entry["aliases"] = als
    _save_members(members, members_path)


def remove_member(name: str, members_path: Path | None = None) -> bool:
    """删除成员（其历史账目仍保留成员名字符串）。"""
    members = load_members(members_path)
    if name not in members:
        return False
    del members[name]
    _save_members(members, members_path)
    return True
