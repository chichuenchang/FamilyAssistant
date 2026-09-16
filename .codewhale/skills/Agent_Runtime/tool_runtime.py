"""各 skill 的 agent_tools.py 与 agent_core 共用的工具运行时。

- 命令路由表 / 白名单 / 超时：由 skill_registry.load() 填充，run_cli 查表执行
- schema 小助手 fn / s / num / int_ / boolean
- 来图搬迁 relocate_image、送文件闸门 resolve_sendable（路径安全，代码确定性执行）

本模块不 import agent_core（避免环）；manifest 只依赖本模块 + paths/members。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import bootstrap

import paths as _paths
import jsonfile

ROOT = bootstrap.ROOT
SKILLS_DIR = bootstrap.SKILLS_DIR
log = logging.getLogger("familyassist.agent")


def load_config() -> dict:
    """项目根 config.json；缺失/损坏返回 {}（各处自带回退默认值）。"""
    return jsonfile.load_dict(ROOT / "config.json")


CONFIG = load_config()

# ── 命令注册表（skill_registry.load() 填充；同一对象经 agent_core 转发给测试） ──
ALLOWED: set[str] = set()            # Agent 可调的 CLI 子命令
COMMAND_DIR: dict[str, Path] = {}    # 子命令 → skill 目录（路由）
CLI_TIMEOUTS: dict[str, int] = {}    # 子命令 → 秒（默认 30）
DEFAULT_SKILL_DIR = SKILLS_DIR / "Expense_Tracker"   # 未登记子命令的兜底路由（历史行为）
DEFAULT_TIMEOUT = 30


def cli_path(cmd: str) -> Path:
    return COMMAND_DIR.get(cmd, DEFAULT_SKILL_DIR) / "cli.py"


def run_cli(cmd: str, args: dict[str, Any] | None = None) -> str:
    """执行 CLI 子命令并返回 stdout（空则 stderr）。白名单外直接拒绝。"""
    if cmd not in ALLOWED:
        return f"[错误] 命令不允许: {cmd}"
    cli_args = [cmd]
    if args:
        for k, v in args.items():
            flag = k if k.startswith("-") else f"--{k}"  # 容忍裸键（type→--type）
            if v is True:
                cli_args.append(flag)                     # 布尔开关，无值（如 --force）
            elif v is False or v is None or v == "":
                continue                                  # 未设置则跳过
            else:
                cli_args.append(flag)
                cli_args.append(str(v))
    try:
        result = subprocess.run(
            [sys.executable, str(cli_path(cmd))] + cli_args,
            capture_output=True, text=True, cwd=str(ROOT),
            timeout=CLI_TIMEOUTS.get(cmd, DEFAULT_TIMEOUT), encoding="utf-8", errors="replace",
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "[错误] 超时"
    except Exception as e:
        return f"[错误] {e}"


def cli_tool(cmd: str):
    """纯透传工具：LLM 参数原样转 CLI 标志。"""
    def _tool(args):
        return run_cli(cmd, args)
    _tool.__name__ = "tool_" + cmd.replace("-", "_")
    return _tool


def json_arg(args: dict, key: str) -> dict:
    """LLM 给的对象/数组参数序列化为 JSON 字符串交 CLI。"""
    args = dict(args)
    v = args.pop(key, None)
    if isinstance(v, (dict, list)):
        args[key] = json.dumps(v, ensure_ascii=False)
    elif v is not None:
        args[key] = str(v)
    return args


# ── schema 小助手（OpenAI function calling 格式） ──────────────

def fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props,
                       "required": required or []},
    }}


def s(desc: str, **kw) -> dict:
    return {"type": "string", "description": desc, **kw}


def num(desc: str) -> dict:
    return {"type": "number", "description": desc}


def int_(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def boolean(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


# ── 路径安全（代码确定性执行，不交给 LLM） ─────────────────────

def relocate_image(src: str, member: str, domain: str) -> str:
    """来图从暂存（成员 inbox，data_root 内）搬到该成员某域 YYYY-MM/，返回 data 相对路径。

    domain ∈ notes/schedule/tasks。失败保留原路径，绝不丢图。
    仅处理 data_root 内的文件 + 已知成员；否则原样返回。"""
    try:
        if not member:
            return src
        p = Path(src)
        resolved = (p if p.is_absolute() else ROOT / p).resolve()
        droot = _paths.data_root().resolve()
        if not (resolved.exists() and resolved.is_relative_to(droot)):
            return src
        dest_dir = _paths.member_domain_image_dir(member, domain)
        dest = dest_dir / resolved.name
        i = 1
        while dest.exists():
            dest = dest_dir / f"{resolved.stem}_{i}{resolved.suffix}"
            i += 1
        resolved.rename(dest)
        log.debug("来图已移动 %s → %s", resolved, dest)
        return _paths.to_rel(dest)
    except Exception:
        log.exception("来图移动失败（保留原路径）")
        return src


def resolve_sendable(path: str, member: str) -> str | None:
    """送文件闸门：路径须存在、是文件、在 data_root 内，且属家庭共享或本成员目录。
    通过 → 返回 data 相对路径；否则 None。"""
    try:
        p = Path(path)
        ap = (p if p.is_absolute() else _paths.resolve_rel(str(path))).resolve()
        root = _paths.data_root().resolve()
        if not (ap.exists() and ap.is_file() and ap.is_relative_to(root)):
            return None
        allowed = [_paths.family_dir().resolve()]
        if member:
            allowed.append(_paths.member_dir(member).resolve())
        if not any(ap.is_relative_to(a) for a in allowed):
            return None
        return _paths.to_rel(ap)
    except (ValueError, OSError):
        return None


def data_path_guard(path: str) -> Path | None:
    """LLM 给的路径只允许指向 data_root 内；越界或无效返回 None。"""
    try:
        p = Path(path)
        resolved = (p if p.is_absolute() else ROOT / p).resolve()
        if not resolved.is_relative_to(_paths.data_root().resolve()):
            return None
        return resolved
    except (OSError, ValueError):
        return None
