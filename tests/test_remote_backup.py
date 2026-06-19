# tests/test_remote_backup.py — Remote Backup skill tests.
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backup_provider


@pytest.fixture
def gdrive(monkeypatch, tmp_path):
    """Google Drive provider with env creds set and HTTP layer faked."""
    monkeypatch.setenv("GDRIVE_CLIENT_ID", "cid")
    monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "cs")
    monkeypatch.setenv("GDRIVE_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(backup_provider, "_token", lambda: "TOK")
    backup_provider._folder_cache["id"] = "FOLDER1"

    calls = []

    class FakeHttp:
        def __init__(self):
            self.responses = []  # list of (status, body-bytes)
        def __call__(self, method, url, data=None, headers=None):
            calls.append({"method": method, "url": url, "data": data,
                          "headers": headers or {}})
            return self.responses.pop(0)

    fake = FakeHttp()
    monkeypatch.setattr(backup_provider, "_http", fake)
    yield fake, calls, tmp_path
    backup_provider._folder_cache["id"] = None


def _files_resp(*files, next_token=None):
    import json as _json
    body = {"files": list(files)}
    if next_token:
        body["nextPageToken"] = next_token
    return (200, _json.dumps(body).encode())


class TestGdriveProvider:
    def test_is_configured_requires_all_env(self, monkeypatch):
        for var in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        assert backup_provider.is_configured() is False
        monkeypatch.setenv("GDRIVE_CLIENT_ID", "x")
        monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "y")
        assert backup_provider.is_configured() is False
        monkeypatch.setenv("GDRIVE_REFRESH_TOKEN", "z")
        assert backup_provider.is_configured() is True

    def test_upload_new_file_multipart_create(self, gdrive):
        fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"IMGBYTES")
        fake.responses = [
            _files_resp(),                       # _find: no existing
            (200, b'{"id": "NEW1"}'),            # multipart POST
        ]
        backup_provider.upload(f, "receipts/2026-06/a.jpg")
        up = calls[-1]
        assert up["method"] == "POST"
        assert "uploadType=multipart" in up["url"]
        assert b"IMGBYTES" in up["data"]
        assert b'"rel": "receipts/2026-06/a.jpg"' in up["data"]
        assert b'"parents": ["FOLDER1"]' in up["data"]

    def test_upload_existing_file_patches(self, gdrive):
        fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"V2")
        fake.responses = [
            _files_resp({"id": "OLD9"}),         # _find: existing
            (200, b'{"id": "OLD9"}'),            # multipart PATCH
        ]
        backup_provider.upload(f, "receipts/a.jpg")
        up = calls[-1]
        assert up["method"] == "PATCH"
        assert "/files/OLD9?" in up["url"]
        assert b'"parents"' not in up["data"]    # update never re-parents

    def test_delete_missing_is_noop(self, gdrive):
        fake, calls, _ = gdrive
        fake.responses = [_files_resp()]         # _find: nothing
        backup_provider.delete("gone.jpg")       # must not raise
        assert len(calls) == 1                   # no DELETE issued

    def test_list_remote_paginates_and_maps_rel(self, gdrive):
        fake, calls, _ = gdrive
        fake.responses = [
            _files_resp({"id": "1", "size": "10",
                         "appProperties": {"rel": "config.json"}},
                        next_token="T2"),
            _files_resp({"id": "2", "size": "20",
                         "appProperties": {"rel": "receipts/a.jpg"}},
                        {"id": "3", "size": "5"}),   # no rel tag → skipped
        ]
        out = backup_provider.list_remote()
        assert out == {"config.json": {"size": 10},
                       "receipts/a.jpg": {"size": 20}}
        assert "pageToken=T2" in calls[-1]["url"]

    def test_download_writes_bytes(self, gdrive):
        fake, calls, tmp_path = gdrive
        fake.responses = [
            _files_resp({"id": "F7"}),
            (200, b"CONTENT"),
        ]
        target = tmp_path / "sub" / "x.bin"
        backup_provider.download("documents/x.bin", target)
        assert target.read_bytes() == b"CONTENT"
        assert "alt=media" in calls[-1]["url"]

    def test_api_error_raises_runtimeerror(self, gdrive):
        fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"x")
        fake.responses = [
            _files_resp(),
            (403, b'{"error": "rate limit"}'),
        ]
        with pytest.raises(RuntimeError):
            backup_provider.upload(f, "a.jpg")


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
        (root / "data" / "Jim" / "schedule").mkdir(parents=True)
        (root / "data" / "Jim" / "schedule" / ".sync_state.json").write_text("{}")
        (root / "data" / "Jim" / "schedule" / "schedule.db").write_bytes(b"")
        files = backup_sync._iter_local_files()
        assert "data/ledger.db" in files
        assert not any("creds" in f for f in files)
        assert "data/.telegram_offset" not in files
        assert "data/.doc_reminder_state" not in files
        assert "data/.backup_state.json" not in files
        # 每成员每域的同步状态文件可再生，不进备份；分库 .db 照常入备份
        assert "data/Jim/schedule/.sync_state.json" not in files
        assert "data/Jim/schedule/schedule.db" in files


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


import json
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


def _disabled_cfg(tmp_path):
    """生成 backup.enabled=false 的隔离配置（不依赖真实 config.json 的开关状态）。"""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"backup": {"enabled": False}}), encoding="utf-8")
    return {"BACKUP_STATE_DIR": str(tmp_path), "BACKUP_CONFIG": str(cfg)}


class TestCli:
    def test_backup_status_works_unconfigured(self, tmp_path):
        r = _run_cli("backup-status", env_extra=_disabled_cfg(tmp_path))
        assert r.returncode == 0
        assert "enabled" in r.stdout and "False" in r.stdout

    def test_backup_now_disabled_exits_1(self, tmp_path):
        # 隔离配置 backup.enabled = false → 拒绝
        r = _run_cli("backup-now", env_extra=_disabled_cfg(tmp_path))
        assert r.returncode == 1
        assert "未启用" in r.stderr or "未实现" in r.stderr

    def test_backup_verify_unconfigured_exits_1(self, tmp_path):
        r = _run_cli("backup-verify", env_extra=_disabled_cfg(tmp_path))
        assert r.returncode == 1

    def test_backup_restore_unconfigured_exits_1(self, tmp_path):
        r = _run_cli("backup-restore", env_extra=_disabled_cfg(tmp_path))
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


class TestScopeResolver:
    @pytest.fixture
    def sr(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        (root / "data").mkdir(parents=True)
        monkeypatch.setattr(backup_sync, "ROOT", root)
        monkeypatch.setattr(backup_sync, "_DATA_DIRNAME", "data")
        return root

    def test_scope_to_prefix(self, sr):
        assert backup_sync._scope_to_prefix("config.json") == "config.json"
        assert backup_sync._scope_to_prefix("Jim") == "data/Jim"
        assert backup_sync._scope_to_prefix("Family/documents") == "data/Family/documents"

    def test_member_files_prefix_boundary(self, sr):
        root = sr
        (root / "data" / "Jim" / "notes").mkdir(parents=True)
        (root / "data" / "Jim" / "notes" / "n.db").write_bytes(b"x")
        (root / "data" / "Jimbo").mkdir()
        (root / "data" / "Jimbo" / "y.txt").write_text("y")
        files = backup_sync._member_files(["Jim"])
        assert "data/Jim/notes/n.db" in files
        assert not any("Jimbo" in f for f in files)   # 前缀不跨目录边界

    def test_member_files_config_and_members_aliases(self, sr):
        root = sr
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "data" / "members.json").write_text("{}", encoding="utf-8")
        files = backup_sync._member_files(["members.json", "config.json"])
        assert "config.json" in files
        assert "data/members.json" in files

    def test_member_files_applies_hard_excludes(self, sr):
        root = sr
        (root / "data" / "Jim").mkdir(parents=True)
        (root / "data" / "Jim" / "notes.db").write_bytes(b"")
        (root / "data" / "Jim" / ".backup_manifest.json").write_text("{}")
        (root / "data" / "Jim" / "wechat_creds.json").write_text("secret")
        files = backup_sync._member_files(["Jim"])
        assert "data/Jim/notes.db" in files
        assert not any("creds" in f for f in files)
        assert "data/Jim/.backup_manifest.json" not in files

    def test_member_files_missing_token_is_silent(self, sr):
        assert backup_sync._member_files(["Ghost"]) == {}
