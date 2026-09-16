"""Remote_Backup 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

backup-restore / backup-reorg 仅限本机，不进 AGENT_COMMANDS。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

from tool_runtime import fn
from backup_sync import backup_tick as _backup_tick

ORDER = 40

COMMANDS = {"backup-now", "backup-status", "backup-verify", "backup-restore", "backup-reorg"}
AGENT_COMMANDS = {"backup-now", "backup-status", "backup-verify"}

TOOLS = {
    "backup_now": "backup-now",
    "backup_status": "backup-status",
    "backup_verify": "backup-verify",
}

SCHEMAS = [
    fn("backup_now", "立即把用户数据镜像到云盘（需用户已配置 backup provider）", {}),
    fn("backup_status", "查看云盘备份状态（是否启用/已配置/待同步/上次同步/错误）", {}),
    fn("backup_verify", "校验云端镜像与本地清单是否一致", {}),
]

PROMPT_SECTIONS = [
    """## 数据备份（可选功能）
- 用户问"备份了吗""上次备份什么时候"→ backup_status
- 用户说"立刻备份""把数据同步到云盘"→ backup_now
- 用户问"云端和本地一致吗"→ backup_verify
- backup_status 显示未启用/未配置时：告知备份是可选功能，需要在电脑上按
  Remote_Backup/SKILL.md 完成 Google Drive 授权并启用；不要反复推销
- 数据恢复（backup-restore）只能在电脑上手动执行，你调不到""",
]


def backup_tick(push_text, channel) -> None:
    """慢拍：脏 + 去抖到期才真正上传（backup_sync 自理）。"""
    _backup_tick()


SLOW_TICKS = [backup_tick]
