"""sys.path 单一入口：import 本模块即把全部 skill 目录挂上。

每个入口文件只需一行（Agent_Runtime 内外同一写法）：
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap

Agent_Runtime 排最前（paths / members 唯一，须优先命中），其余按目录名。
跨 skill 唯一重名模块是各家 cli.py，无人按名 import，安全。
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


def ensure_paths() -> None:
    """幂等：已在 sys.path 的目录不重复插入，顺序按 skill_dirs()。"""
    for d in reversed(skill_dirs()):
        p = str(d)
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


ensure_paths()
