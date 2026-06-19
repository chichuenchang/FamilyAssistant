"""
一次性迁移：单一全局备份 → 每成员备份（per-member backup）。

幂等、可回滚（先快照 .bak）。前置：机器人已停。
- 给 Jim 加 backup 块（scopes 复刻今日全量覆盖，零重传）。
- config.json backup 段瘦身为 {enabled, debounce_seconds}。
- 全局清单 data/.backup_manifest.json → data/Jim/.backup_manifest.json（rel 不变）。
- 全局时钟 data/.backup_state.json 原地保留（仍是共享防抖时钟）。
- 校验 Wenliang/Euphie 目录无文件（否则它们会被排除在任何备份外 → 中止待人工决定）。

用法: python .codewhale/skills/Agent_Runtime/migrate_backup.py [--root PATH]
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_JIM = "Jim Zheng"
_JIM_SCOPES = ["Jim", "Family", "members.json", "config.json"]


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write(p: Path, obj: dict) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _has_files(d: Path) -> bool:
    return d.is_dir() and any(f.is_file() for f in d.rglob("*"))


def migrate(root: Path) -> dict:
    root = Path(root)
    members_path = root / "data" / "members.json"
    config_path = root / "config.json"
    members = _read(members_path)
    config = _read(config_path)

    # 安全校验：其他成员目录非空 → 它们的数据将无人备份，停下来让人决定
    for other in ("Wenliang", "Euphie"):
        if _has_files(root / "data" / other):
            print(f"中止：data/{other} 含文件，但该成员无 backup 配置 → 其数据不会被备份。"
                  f"先为该成员加 backup 块或确认可不备份后再迁移。", file=sys.stderr)
            raise SystemExit(1)

    # 快照
    for p in (members_path, config_path,
              root / "data" / ".backup_manifest.json",
              root / "data" / ".backup_state.json"):
        if p.exists() and not p.with_suffix(p.suffix + ".bak").exists():
            shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))

    summary = {"added_block": False, "config_shrunk": False,
               "manifest_moved": False, "empty_ok": True}

    # 1) Jim backup 块
    jim = members.get(_JIM)
    if isinstance(jim, dict) and "backup" not in jim:
        jim["backup"] = {"provider": "google_drive", "cred_prefix": "GDRIVE",
                         "remote_root": "FamilyAssistant", "enabled": True,
                         "scopes": list(_JIM_SCOPES)}
        _write(members_path, members)
        summary["added_block"] = True

    # 2) config.json backup 瘦身
    bk = config.get("backup")
    if isinstance(bk, dict) and set(bk) - {"enabled", "debounce_seconds"}:
        config["backup"] = {"enabled": bool(bk.get("enabled", False)),
                            "debounce_seconds": int(bk.get("debounce_seconds", 60))}
        _write(config_path, config)
        summary["config_shrunk"] = True

    # 3) 全局清单 → Jim
    src = root / "data" / ".backup_manifest.json"
    dst = root / "data" / "Jim" / ".backup_manifest.json"
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        summary["manifest_moved"] = True

    return summary


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1])
    s = migrate(root)
    print(f"迁移完成：{s}")


if __name__ == "__main__":
    main()
