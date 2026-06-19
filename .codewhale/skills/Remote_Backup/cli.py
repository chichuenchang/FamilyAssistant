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


def _enabled_now_members(member):
    """要操作的成员名：指定则单个，否则全部 enabled 成员。"""
    if member:
        return [member]
    return [n for n, p in backup_sync._backup_members() if p["enabled"]]


def cmd_now(args):
    if not backup_sync.CFG["enabled"]:
        raise ValueError("备份未启用（config.json backup.enabled = false）。"
                         "设置指南见 .codewhale/skills/Remote_Backup/SKILL.md")
    names = _enabled_now_members(args.member)
    if not names:
        raise ValueError("没有启用备份的成员（members.json 加 backup 块）。")
    total_err = 0
    for name in names:
        pref = backup_sync._resolve(name)
        if pref is None:
            print(f"[{name}] 跳过：无 backup 配置", file=sys.stderr)
            continue
        if not backup_sync._make_provider(pref).is_configured():
            print(f"[{name}] 跳过：provider 未配置", file=sys.stderr)
            continue
        r = backup_sync.sync(name)
        for rel in r["uploaded"]:
            print(f"[{name}] ↑ {rel}")
        for rel in r["deleted"]:
            print(f"[{name}] ✕ {rel}（远端已删，本地不存在）")
        print(f"[{name}] 完成：上传 {len(r['uploaded'])}，删除 {len(r['deleted'])}，"
              f"未变 {r['skipped']}，错误 {len(r['errors'])}")
        for e in r["errors"]:
            print(f"[{name}] 错误: {e}", file=sys.stderr)
        total_err += len(r["errors"])
    if total_err:
        sys.exit(1)


def cmd_status(args):
    s = backup_sync.status(member=args.member)
    print(f"enabled: {s['enabled']} | dirty_since: {s['dirty_since'] or '-'} | "
          f"last_write: {s['last_write'] or '-'}")
    if not s["members"]:
        print("（无启用备份的成员）")
    for m in s["members"]:
        print(f"  [{m['member']}] enabled={m['enabled']} configured={m['configured']} "
              f"provider={m['provider']} root={m['remote_root']} "
              f"files={m['files_tracked']} last_sync={m['last_sync'] or '-'}")
        if m["last_error"]:
            print(f"      last_error: {m['last_error']}")


def cmd_verify(args):
    if not backup_sync.CFG["enabled"]:
        raise ValueError("备份未启用。")
    names = _enabled_now_members(args.member)
    if not names:
        raise ValueError("没有启用备份的成员。")
    bad = 0
    for name in names:
        pref = backup_sync._resolve(name)
        if pref is None or not backup_sync._make_provider(pref).is_configured():
            print(f"[{name}] 跳过：provider 未配置", file=sys.stderr)
            continue
        v = backup_sync.verify(name)
        print(f"[{name}] 一致 {len(v['ok'])} | 远端缺失 {len(v['missing_remote'])} | "
              f"远端多余 {len(v['extra_remote'])} | 大小不符 {len(v['size_mismatch'])}")
        for k in ("missing_remote", "extra_remote", "size_mismatch"):
            for rel in v[k]:
                print(f"  [{name}][{k}] {rel}")
        if v["missing_remote"] or v["size_mismatch"]:
            bad += 1
    if bad:
        sys.exit(1)


def cmd_restore(args):
    if not args.member:
        raise ValueError("backup-restore 需要 --member NAME。")
    override = None
    if backup_sync._resolve(args.member) is None:
        if not args.prefix or not args.remote_root:
            raise ValueError("成员无 backup 配置；引导恢复需 --prefix 与 --remote-root。")
        override = {"provider": args.provider, "cred_prefix": args.prefix,
                    "remote_root": args.remote_root, "scopes": [],
                    "dir": args.dir or args.member}
    r = backup_sync.restore(args.member, force=args.force, override=override)
    for rel in r["downloaded"]:
        print(f"↓ {rel}")
    print(f"[{args.member}] 恢复完成：{len(r['downloaded'])} 个文件。")


def main():
    parser = argparse.ArgumentParser(description="Family Assistant — Remote Backup CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd, help_text in (("backup-now", "立即同步（忽略防抖）"),
                           ("backup-status", "备份状态"),
                           ("backup-verify", "清单 vs 云端校验")):
        p = sub.add_parser(cmd, help=help_text)
        p.add_argument("--member", help="只操作该成员，缺省全部启用成员")

    p = sub.add_parser("backup-restore", help="新设备：从云端恢复某成员数据（仅本机）")
    p.add_argument("--member", help="要恢复的成员")
    p.add_argument("--force", action="store_true", help="本地已有数据也覆盖")
    p.add_argument("--provider", default="google_drive", help="引导恢复用 provider")
    p.add_argument("--prefix", help="引导恢复：凭据环境变量前缀")
    p.add_argument("--remote-root", dest="remote_root", help="引导恢复：云端根目录名")
    p.add_argument("--dir", help="引导恢复：成员磁盘目录名（缺省取成员名）")

    args = parser.parse_args()
    dispatch = {"backup-now": cmd_now, "backup-status": cmd_status,
                "backup-verify": cmd_verify, "backup-restore": cmd_restore}
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
