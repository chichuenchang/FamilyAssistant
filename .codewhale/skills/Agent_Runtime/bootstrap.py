"""sys.path 单一入口：import 本模块即把全部 skill 目录挂上。

只有**直接运行的入口文件**（各 cli.py、telegram_bot / wechat_ilink、agent_core、
tests/conftest）需要这一行；被 import 的模块不需要（此时路径已挂好）：
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap

Agent_Runtime 排最前（paths / members 唯一，须优先命中），其余按目录名。
扁平命名空间：除 cli.py / agent_tools.py（无人按名 import）外，模块名跨 skill
不得重复——check_unique_modules 在挂载时报错，防后挂目录被前面同名模块遮住。
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1]
ROOT = SKILLS_DIR.parents[1]


def skill_dirs() -> list[Path]:
    others = sorted(d for d in SKILLS_DIR.iterdir()
                    if d.is_dir() and d.name != "Agent_Runtime"
                    and not d.name.startswith((".", "_")))
    return [SKILLS_DIR / "Agent_Runtime", *others]


_SHADOW_OK = {"cli.py", "agent_tools.py", "__init__.py"}


def check_unique_modules(dirs: list[Path] | None = None) -> None:
    """跨 skill 模块名重复 → RuntimeError（同名会互相遮蔽，import 拿到哪个看挂载顺序）。"""
    seen: dict[str, str] = {}
    for d in dirs if dirs is not None else skill_dirs():
        for f in d.glob("*.py"):
            if f.name in _SHADOW_OK:
                continue
            if f.name in seen:
                raise RuntimeError(f"skill 模块重名: {f.name}（{seen[f.name]} 与 {d.name}）")
            seen[f.name] = d.name


def ensure_paths() -> None:
    """幂等：已在 sys.path 的目录不重复插入，顺序按 skill_dirs()。"""
    check_unique_modules()
    for d in reversed(skill_dirs()):
        p = str(d)
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


ensure_paths()
