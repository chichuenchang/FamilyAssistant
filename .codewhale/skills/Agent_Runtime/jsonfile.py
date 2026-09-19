"""JSON 文件读写：config.json 与各状态文件共用。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def load_dict(path: str | Path) -> dict:
    """解析 JSON 对象；缺失/损坏/非对象返回 {}（调用方各自带回退默认值）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str | Path, obj, *, indent: int | None = None) -> None:
    """原子写 JSON（保留中文），父目录不存在则建。临时文件 + os.replace：
    另一个传输进程读到的永远是完整文件（半截文件会被 load_dict 当成 {}，下次保存即清空）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, indent=indent))
        for attempt in range(5):          # Windows：对方正开着读时 replace 报 PermissionError
            try:
                os.replace(tmp, p)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
