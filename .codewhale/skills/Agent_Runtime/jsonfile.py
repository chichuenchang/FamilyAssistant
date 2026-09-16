"""JSON 文件读写：config.json 与各状态文件共用。"""

from __future__ import annotations

import json
from pathlib import Path


def load_dict(path: str | Path) -> dict:
    """解析 JSON 对象；缺失/损坏/非对象返回 {}（调用方各自带回退默认值）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str | Path, obj, *, indent: int | None = None) -> None:
    """写 JSON（保留中文），父目录不存在则建。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
