"""
成员注册表 — 家庭成员与频道身份的映射（config.json members 段）。

格式:
    "members": {
      "爸爸": { "telegram": ["123456789"], "wechat": ["wxid_abc"] }
    }

注册表只在本机用 CLI 管理（member-add / member-list / member-remove，
不在 wechat.allowed_commands 白名单内），Agent Runtime 只读。
未注册的频道 id 一律静默丢弃；注册表缺失/损坏时全部锁定（安全默认）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# 本文件位于 .codewhale/skills/Agent_Runtime/ ，向上 3 级到项目根
ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config.json"

CHANNELS = ("telegram", "wechat")


def _load_config(config_path: Path | None = None) -> dict:
    """解析 config.json；缺失/损坏返回 {}（→ 锁定）。"""
    try:
        return json.loads((config_path or CONFIG_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_members(config_path: Path | None = None) -> dict:
    """members 段；缺失或格式不对返回 {}（→ 锁定）。"""
    members = _load_config(config_path).get("members")
    return members if isinstance(members, dict) else {}


def resolve(channel: str, channel_id, config_path: Path | None = None) -> str | None:
    """频道 id → 成员名；未注册返回 None（调用方必须静默丢弃该消息）。"""
    cid = str(channel_id or "")
    if not cid:
        return None
    for name, bindings in load_members(config_path).items():
        ids = bindings.get(channel) or [] if isinstance(bindings, dict) else []
        if cid in (str(i) for i in ids):
            return name
    return None


def member_names(config_path: Path | None = None) -> list[str]:
    """已登记成员名列表。"""
    return list(load_members(config_path).keys())


def _save_config(cfg: dict, config_path: Path | None = None) -> None:
    """原子写回 config.json（临时文件 + replace，写一半不毁原文件）。"""
    path = config_path or CONFIG_PATH
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_member(name: str, telegram=None, wechat=None,
               config_path: Path | None = None) -> None:
    """新增成员或为已有成员追加频道 id。频道 id 已绑定其他成员时报错。"""
    if not name:
        raise ValueError("成员名不能为空")
    new_ids = {"telegram": [str(i) for i in (telegram or [])],
               "wechat": [str(i) for i in (wechat or [])]}
    for ch in CHANNELS:
        for cid in new_ids[ch]:
            owner = resolve(ch, cid, config_path)
            if owner and owner != name:
                raise ValueError(f"{ch} id {cid} 已绑定成员 {owner}")
    cfg = _load_config(config_path)
    members = cfg.get("members")
    if not isinstance(members, dict):
        members = {}
    entry = members.setdefault(name, {})
    for ch in CHANNELS:
        ids = [str(i) for i in (entry.get(ch) or [])]
        for cid in new_ids[ch]:
            if cid not in ids:
                ids.append(cid)
        if ids:
            entry[ch] = ids
    cfg["members"] = members
    _save_config(cfg, config_path)


def remove_member(name: str, config_path: Path | None = None) -> bool:
    """删除成员（其历史账目仍保留成员名字符串）。"""
    cfg = _load_config(config_path)
    members = cfg.get("members")
    if not isinstance(members, dict) or name not in members:
        return False
    del members[name]
    cfg["members"] = members
    _save_config(cfg, config_path)
    return True
