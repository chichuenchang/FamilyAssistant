# tests/test_remote_backup.py — Remote Backup skill tests.
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backup_provider


def test_provider_stub_unconfigured():
    assert backup_provider.is_configured() is False
    with pytest.raises(NotImplementedError):
        backup_provider.upload(Path("x"), "x")
    with pytest.raises(NotImplementedError):
        backup_provider.delete("x")
    with pytest.raises(NotImplementedError):
        backup_provider.list_remote()
    with pytest.raises(NotImplementedError):
        backup_provider.download("x", Path("x"))


import backup_sync


@pytest.fixture
def bk(tmp_path, monkeypatch):
    """Isolated engine: fake ROOT tree, state in tmp, enabled config, fake provider."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "receipts").mkdir()
    (root / "documents").mkdir()
    (root / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(backup_sync, "ROOT", root)
    monkeypatch.setattr(backup_sync, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(backup_sync, "MANIFEST_FILE", tmp_path / "manifest.json")
    monkeypatch.setattr(backup_sync, "CFG", {
        "enabled": True, "debounce_seconds": 60,
        "include": ["data/ledger.db", "receipts", "documents", "config.json"],
        "remote_root": "FamilyAssistant",
    })

    class FakeProvider:
        def __init__(self):
            self.files = {}      # remote_rel -> bytes
            self.fail_uploads = False
        def is_configured(self):
            return True
        def upload(self, local_path, remote_rel):
            if self.fail_uploads:
                raise RuntimeError("network down")
            self.files[remote_rel] = Path(local_path).read_bytes()
        def delete(self, remote_rel):
            self.files.pop(remote_rel, None)
        def list_remote(self):
            return {rel: {"size": len(b)} for rel, b in self.files.items()}
        def download(self, remote_rel, local_path):
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(self.files[remote_rel])

    fake = FakeProvider()
    monkeypatch.setattr(backup_sync, "provider", fake)
    return root, fake


class TestStateAndWalk:
    def test_mark_dirty_sets_timestamps(self, bk):
        backup_sync.mark_dirty()
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert st["dirty_since"] and st["last_write"]

    def test_mark_dirty_preserves_dirty_since(self, bk):
        backup_sync.mark_dirty()
        first = backup_sync._load_json(backup_sync.STATE_FILE)["dirty_since"]
        backup_sync.mark_dirty()
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert st["dirty_since"] == first

    def test_mark_dirty_never_raises(self, bk, monkeypatch):
        monkeypatch.setattr(backup_sync, "STATE_FILE",
                            Path("Z:/nonexistent/state.json"))
        backup_sync.mark_dirty()  # must not raise

    def test_walk_includes_files_and_dirs(self, bk):
        root, _ = bk
        (root / "receipts" / "2026-06").mkdir()
        (root / "receipts" / "2026-06" / "a.jpg").write_bytes(b"img")
        (root / "documents" / "lease").mkdir()
        (root / "documents" / "lease" / "l.pdf").write_bytes(b"pdf")
        files = backup_sync._iter_local_files()
        assert "receipts/2026-06/a.jpg" in files
        assert "documents/lease/l.pdf" in files
        assert "config.json" in files
        assert "data/ledger.db" not in files  # doesn't exist yet

    def test_walk_hard_excludes(self, bk):
        root, _ = bk
        backup_sync.CFG["include"] = backup_sync.CFG["include"] + ["data"]
        (root / "data" / "ledger.db").write_bytes(b"")
        (root / "data" / "wechat_creds.json").write_text("secret")
        (root / "data" / ".telegram_offset").write_text("1")
        (root / "data" / ".doc_reminder_state").write_text("{}")
        (root / "data" / ".backup_state.json").write_text("{}")
        files = backup_sync._iter_local_files()
        assert "data/ledger.db" in files
        assert not any("creds" in f for f in files)
        assert "data/.telegram_offset" not in files
        assert "data/.doc_reminder_state" not in files
        assert "data/.backup_state.json" not in files


class TestSync:
    def test_uploads_new_and_skips_unchanged(self, bk):
        root, fake = bk
        (root / "receipts" / "a.jpg").write_bytes(b"img-a")
        r1 = backup_sync.sync()
        assert "receipts/a.jpg" in r1["uploaded"]
        assert fake.files["receipts/a.jpg"] == b"img-a"
        r2 = backup_sync.sync()
        assert r2["uploaded"] == [] and r2["skipped"] >= 1

    def test_reuploads_changed(self, bk):
        root, fake = bk
        f = root / "receipts" / "a.jpg"
        f.write_bytes(b"v1")
        backup_sync.sync()
        f.write_bytes(b"v2")
        r = backup_sync.sync()
        assert "receipts/a.jpg" in r["uploaded"]
        assert fake.files["receipts/a.jpg"] == b"v2"

    def test_propagates_deletes(self, bk):
        root, fake = bk
        f = root / "documents" / "old.pdf"
        f.write_bytes(b"x")
        backup_sync.sync()
        assert "documents/old.pdf" in fake.files
        f.unlink()
        r = backup_sync.sync()
        assert "documents/old.pdf" in r["deleted"]
        assert "documents/old.pdf" not in fake.files

    def test_sqlite_snapshot_with_open_connection(self, bk):
        import os
        import sqlite3 as sq
        import tempfile
        root, fake = bk
        db = root / "data" / "ledger.db"
        conn = sq.connect(str(db))
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        try:
            r = backup_sync.sync()
            assert "data/ledger.db" in r["uploaded"]
            # snapshot must be a valid sqlite db containing the row
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            Path(tmp).write_bytes(fake.files["data/ledger.db"])
            check = sq.connect(tmp)
            assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 1
            check.close()
            os.unlink(tmp)
        finally:
            conn.close()

    def test_failure_records_error_keeps_dirty(self, bk):
        root, fake = bk
        (root / "receipts" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()
        fake.fail_uploads = True
        r = backup_sync.sync()
        assert r["errors"]
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert st["dirty_since"] is not None
        assert st["last_error"]
        fake.fail_uploads = False
        r = backup_sync.sync()
        assert "receipts/a.jpg" in r["uploaded"]
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert st["dirty_since"] is None and st["last_error"] is None


class TestTick:
    def test_disabled_noop(self, bk):
        backup_sync.CFG["enabled"] = False
        backup_sync.mark_dirty()
        assert backup_sync.backup_tick() is False

    def test_clean_noop(self, bk):
        assert backup_sync.backup_tick() is False

    def test_recent_write_waits(self, bk):
        root, _ = bk
        (root / "receipts" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()  # last_write = now
        assert backup_sync.backup_tick() is False  # 60s 未到

    def test_quiet_period_syncs(self, bk):
        root, fake = bk
        (root / "receipts" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is True
        assert "receipts/a.jpg" in fake.files
        # 干净后再 tick 不动
        assert backup_sync.backup_tick(now=later) is False

    def test_unconfigured_provider_noop(self, bk, monkeypatch):
        root, fake = bk
        monkeypatch.setattr(fake, "is_configured", lambda: False)
        backup_sync.mark_dirty()
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is False

    def test_tick_never_raises(self, bk, monkeypatch):
        root, fake = bk
        (root / "receipts" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()
        def boom():
            raise RuntimeError("explode")
        monkeypatch.setattr(backup_sync, "sync", boom)
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is False
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert "explode" in (st.get("last_error") or "")


class TestRestoreVerifyStatus:
    def test_restore_to_empty_root(self, bk):
        root, fake = bk
        fake.files = {
            "data/ledger.db": b"db-bytes",
            "receipts/2026-06/a.jpg": b"img",
            "documents/lease/l.pdf": b"pdf",
        }
        r = backup_sync.restore()
        assert sorted(r["downloaded"]) == sorted(fake.files)
        assert (root / "receipts" / "2026-06" / "a.jpg").read_bytes() == b"img"
        manifest = backup_sync._load_json(backup_sync.MANIFEST_FILE)
        assert set(manifest) == set(fake.files)

    def test_restore_refuses_non_empty(self, bk):
        root, fake = bk
        (root / "receipts" / "existing.jpg").write_bytes(b"x")
        fake.files = {"receipts/other.jpg": b"y"}
        with pytest.raises(ValueError):
            backup_sync.restore()
        backup_sync.restore(force=True)  # force 放行
        assert (root / "receipts" / "other.jpg").exists()

    def test_restore_unconfigured_raises(self, bk, monkeypatch):
        root, fake = bk
        monkeypatch.setattr(fake, "is_configured", lambda: False)
        with pytest.raises(ValueError):
            backup_sync.restore()

    def test_verify_reports(self, bk):
        root, fake = bk
        (root / "receipts" / "a.jpg").write_bytes(b"aaaa")
        backup_sync.sync()
        fake.files["receipts/ghost.jpg"] = b"zz"        # extra remote
        fake.files["receipts/a.jpg"] = b"a"             # size mismatch
        v = backup_sync.verify()
        assert "receipts/ghost.jpg" in v["extra_remote"]
        assert "receipts/a.jpg" in v["size_mismatch"]
        del fake.files["receipts/a.jpg"]
        v = backup_sync.verify()
        assert "receipts/a.jpg" in v["missing_remote"]

    def test_status_fields(self, bk):
        s = backup_sync.status()
        assert s["enabled"] is True and s["configured"] is True
        assert s["files_tracked"] == 0
        backup_sync.mark_dirty()
        s = backup_sync.status()
        assert s["dirty_since"] is not None


import os
import subprocess
import sys as _sys

_CLI = str(Path(__file__).resolve().parent.parent
           / ".codewhale" / "skills" / "Remote_Backup" / "cli.py")


def _run_cli(*args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [_sys.executable, _CLI, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


class TestCli:
    def test_backup_status_works_unconfigured(self, tmp_path):
        r = _run_cli("backup-status",
                     env_extra={"BACKUP_STATE_DIR": str(tmp_path)})
        assert r.returncode == 0
        assert "enabled" in r.stdout and "False" in r.stdout

    def test_backup_now_disabled_exits_1(self, tmp_path):
        # 真实 config.json: backup.enabled = false → 拒绝
        r = _run_cli("backup-now",
                     env_extra={"BACKUP_STATE_DIR": str(tmp_path)})
        assert r.returncode == 1
        assert "未启用" in r.stderr or "未实现" in r.stderr

    def test_backup_verify_unconfigured_exits_1(self, tmp_path):
        r = _run_cli("backup-verify",
                     env_extra={"BACKUP_STATE_DIR": str(tmp_path)})
        assert r.returncode == 1

    def test_backup_restore_unconfigured_exits_1(self, tmp_path):
        r = _run_cli("backup-restore",
                     env_extra={"BACKUP_STATE_DIR": str(tmp_path)})
        assert r.returncode == 1


_DOC_CLI = str(Path(__file__).resolve().parent.parent
               / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")


class TestDirtyHooks:
    def test_doc_add_marks_dirty(self, tmp_path):
        import json as _json
        env = {**os.environ,
               "BACKUP_STATE_DIR": str(tmp_path),
               "DOC_KEEPER_DB": str(tmp_path / "d.db")}
        r = subprocess.run(
            [_sys.executable, _DOC_CLI, "doc-add", "--type", "lease",
             "--title", "t"],
            capture_output=True, text=True, encoding="utf-8", env=env)
        assert r.returncode == 0, r.stderr
        st = _json.loads((tmp_path / ".backup_state.json").read_text(encoding="utf-8"))
        assert st["dirty_since"]

    def test_doc_list_does_not_mark_dirty(self, tmp_path):
        env = {**os.environ,
               "BACKUP_STATE_DIR": str(tmp_path),
               "DOC_KEEPER_DB": str(tmp_path / "d.db")}
        subprocess.run(
            [_sys.executable, _DOC_CLI, "doc-list"],
            capture_output=True, text=True, encoding="utf-8", env=env)
        assert not (tmp_path / ".backup_state.json").exists()


import agent_core


class TestAgentIntegration:
    def test_backup_commands_route(self):
        assert agent_core._cli_path("backup-now").parts[-2] == "Remote_Backup"
        assert agent_core._cli_path("doc-add").parts[-2] == "Document_Keeper"
        assert agent_core._cli_path("add").parts[-2] == "Expense_Tracker"

    def test_whitelist(self):
        for cmd in ("backup-now", "backup-status", "backup-verify"):
            assert cmd in agent_core.ALLOWED_COMMANDS
        assert "backup-restore" not in agent_core.ALLOWED_COMMANDS

    def test_tools_registered(self):
        for name in ("backup_now", "backup_status", "backup_verify"):
            assert name in agent_core._TOOL_MAP
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert "backup_status" in schema_names
