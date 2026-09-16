"""备份脏标记入口（各 skill CLI 写入后调用）。

Remote_Backup 可选：未安装/未配置/导入失败一律静默，绝不影响写入本身。
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKUP_DIR = Path(__file__).resolve().parents[1] / "Remote_Backup"


def mark_dirty() -> None:
    try:
        if str(_BACKUP_DIR) not in sys.path:
            sys.path.insert(0, str(_BACKUP_DIR))
        from backup_sync import mark_dirty as _mark
        _mark()
    except Exception:
        pass
