"""
Family Assistant — Remote Backup CLI

用户数据云盘镜像。Agent 经白名单子命令调用（backup-restore 除外，仅本机）。
用法: python .codewhale/skills/Remote_Backup/cli.py <command> [args]
"""

import argparse
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_sync


def _require_ready() -> None:
    if not backup_sync.CFG["enabled"]:
        raise ValueError("备份未启用（config.json backup.enabled = false）。"
                         "设置指南见 .codewhale/skills/Remote_Backup/SKILL.md")
    if not backup_sync._provider_ready():
        raise ValueError("backup_provider 未实现/未配置。"
                         "设置指南见 .codewhale/skills/Remote_Backup/SKILL.md")


def cmd_now(_args):
    _require_ready()
    r = backup_sync.sync()
    for rel in r["uploaded"]:
        print(f"↑ {rel}")
    for rel in r["deleted"]:
        print(f"✕ {rel}（远端已删，本地不存在）")
    print(f"完成：上传 {len(r['uploaded'])}，删除 {len(r['deleted'])}，"
          f"未变 {r['skipped']}，错误 {len(r['errors'])}")
    for e in r["errors"]:
        print(f"错误: {e}", file=sys.stderr)
    if r["errors"]:
        sys.exit(1)


def cmd_status(_args):
    s = backup_sync.status()
    print(f"enabled: {s['enabled']} | provider configured: {s['configured']}")
    print(f"dirty_since: {s['dirty_since'] or '-'} | last_write: {s['last_write'] or '-'}")
    print(f"last_sync: {s['last_sync'] or '-'} | files_tracked: {s['files_tracked']}")
    if s["last_error"]:
        print(f"last_error: {s['last_error']}")


def cmd_verify(_args):
    _require_ready()
    v = backup_sync.verify()
    print(f"一致 {len(v['ok'])} | 远端缺失 {len(v['missing_remote'])} | "
          f"远端多余 {len(v['extra_remote'])} | 大小不符 {len(v['size_mismatch'])}")
    for k in ("missing_remote", "extra_remote", "size_mismatch"):
        for rel in v[k]:
            print(f"  [{k}] {rel}")
    if v["missing_remote"] or v["size_mismatch"]:
        sys.exit(1)


def cmd_restore(args):
    _require_ready()
    r = backup_sync.restore(force=args.force)
    for rel in r["downloaded"]:
        print(f"↓ {rel}")
    print(f"恢复完成：{len(r['downloaded'])} 个文件。")


def main():
    parser = argparse.ArgumentParser(description="Family Assistant — Remote Backup CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backup-now", help="立即同步一轮（忽略防抖）")
    sub.add_parser("backup-status", help="备份状态")
    sub.add_parser("backup-verify", help="清单 vs 云端校验")

    p = sub.add_parser("backup-restore", help="新设备：从云端恢复全部数据（仅本机）")
    p.add_argument("--force", action="store_true", help="本地已有数据也覆盖")

    args = parser.parse_args()
    dispatch = {
        "backup-now": cmd_now,
        "backup-status": cmd_status,
        "backup-verify": cmd_verify,
        "backup-restore": cmd_restore,
    }
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
