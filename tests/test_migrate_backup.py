# tests/test_migrate_backup.py — one-time per-member backup migration.
import json
from pathlib import Path

import pytest

import migrate_backup


def _old_tree(root: Path):
    (root / "data" / "Jim").mkdir(parents=True)
    (root / "data" / "Family").mkdir(parents=True)
    (root / "data" / "Wenliang").mkdir()
    (root / "data" / "Euphie").mkdir()
    (root / "config.json").write_text(json.dumps({
        "data_root": "data",
        "backup": {"enabled": True, "debounce_seconds": 60,
                   "include": ["data", "config.json"], "remote_root": "FamilyAssistant"},
    }), encoding="utf-8")
    (root / "data" / "members.json").write_text(json.dumps({
        "Jim Zheng": {"dir": "Jim", "aliases": ["郑佶淳"]},
        "Wenliang Li": {"dir": "Wenliang"},
        "Euphie": {"dir": "Euphie"},
    }), encoding="utf-8")
    (root / "data" / ".backup_manifest.json").write_text(json.dumps({
        "config.json": {"sha256": "abc", "size": 2, "uploaded_at": "t"},
        "data/Family/ledger.db": {"sha256": "def", "size": 9, "uploaded_at": "t"},
    }), encoding="utf-8")
    (root / "data" / ".backup_state.json").write_text(json.dumps({
        "dirty_since": None, "last_write": "t", "last_sync": "t", "last_error": None,
    }), encoding="utf-8")


def test_migrate_adds_block_shrinks_config_moves_manifest(tmp_path):
    root = tmp_path
    _old_tree(root)
    summary = migrate_backup.migrate(root)

    members = json.loads((root / "data" / "members.json").read_text(encoding="utf-8"))
    jb = members["Jim Zheng"]["backup"]
    assert jb["provider"] == "google_drive"
    assert jb["cred_prefix"] == "GDRIVE"
    assert jb["remote_root"] == "FamilyAssistant"
    assert jb["enabled"] is True
    assert jb["scopes"] == ["Jim", "Family", "members.json", "config.json"]
    assert "Wenliang Li" in members and "backup" not in members["Wenliang Li"]

    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))["backup"]
    assert set(cfg) == {"enabled", "debounce_seconds"}

    jim_manifest = root / "data" / "Jim" / ".backup_manifest.json"
    assert jim_manifest.exists()
    moved = json.loads(jim_manifest.read_text(encoding="utf-8"))
    assert "data/Family/ledger.db" in moved and "config.json" in moved
    assert not (root / "data" / ".backup_manifest.json").exists()
    assert (root / "data" / ".backup_state.json").exists()   # 全局时钟保留
    assert summary["manifest_moved"] is True


def test_migrate_is_idempotent(tmp_path):
    root = tmp_path
    _old_tree(root)
    migrate_backup.migrate(root)
    second = migrate_backup.migrate(root)
    assert second["added_block"] is False
    members = json.loads((root / "data" / "members.json").read_text(encoding="utf-8"))
    assert "backup" in members["Jim Zheng"]


def test_migrate_aborts_on_nonempty_other_member(tmp_path):
    root = tmp_path
    _old_tree(root)
    (root / "data" / "Wenliang" / "stray.db").write_bytes(b"x")
    with pytest.raises(SystemExit):
        migrate_backup.migrate(root)


def test_migrate_cli_runs_without_encoding_crash(tmp_path):
    # 防御 Windows cp1252 控制台：脚本作为子进程跑完应 0 退出并打印中文成功行
    import subprocess
    import sys
    _old_tree(tmp_path)
    mig = str(Path(__file__).resolve().parent.parent
              / ".codewhale" / "skills" / "Agent_Runtime" / "migrate_backup.py")
    r = subprocess.run([sys.executable, mig, "--root", str(tmp_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "迁移完成" in r.stdout
