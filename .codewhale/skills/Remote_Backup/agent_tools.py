"""Remote_Backup 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

backup-restore / backup-reorg 仅限本机，不进 AGENT_COMMANDS。
"""

from __future__ import annotations


import tool_runtime as rt
from tool_runtime import fn, s
from backup_sync import backup_tick as _backup_tick

ORDER = 40

COMMANDS = {"backup-now", "backup-status", "backup-verify", "backup-restore", "backup-reorg"}
AGENT_COMMANDS = {"backup-now", "backup-status", "backup-verify"}

_ACTIONS = ("status", "now", "verify")


def tool_backup(args):
    action = args.get("action") or "status"
    if action not in _ACTIONS:
        return f"[错误] action 只能是 {'/'.join(_ACTIONS)}"
    return rt.run_cli(f"backup-{action}")


TOOLS = {"backup": tool_backup}

SCHEMAS = [
    fn("backup", "云盘备份（需用户已配置 backup provider）", {
        "action": s("status=看状态（启用/配置/待同步/上次同步/错误）；now=立即镜像到云盘；"
                    "verify=校验云端与本地清单是否一致", enum=list(_ACTIONS)),
    }, ["action"]),
]

PROMPT_SECTIONS = [
    """## 数据备份（可选功能）
- 未启用/未配置时告知需在电脑上按 Remote_Backup/SKILL.md 授权启用；不要反复推销
- 数据恢复只能在电脑上手动执行，你调不到""",
]


def backup_tick(push_text, channel) -> None:
    """慢拍：脏 + 去抖到期才真正上传（backup_sync 自理）。"""
    _backup_tick()


SLOW_TICKS = [backup_tick]
