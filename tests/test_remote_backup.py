# tests/test_remote_backup.py — Remote Backup skill tests.
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backup_provider


@pytest.fixture
def gdrive(monkeypatch, tmp_path):
    """A GoogleDriveProvider instance with creds in env and the HTTP layer faked."""
    monkeypatch.setenv("GDRIVE_CLIENT_ID", "cid")
    monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "cs")
    monkeypatch.setenv("GDRIVE_REFRESH_TOKEN", "rt")
    prov = backup_provider.GoogleDriveProvider("GDRIVE", "FamilyAssistant")
    monkeypatch.setattr(prov, "_token", lambda: "TOK")
    prov._folder_cache["id"] = "FOLDER1"

    calls = []

    class FakeHttp:
        def __init__(self):
            self.responses = []
        def __call__(self, method, url, data=None, headers=None):
            calls.append({"method": method, "url": url, "data": data,
                          "headers": headers or {}})
            return self.responses.pop(0)

    fake = FakeHttp()
    monkeypatch.setattr(backup_provider, "_http", fake)
    return prov, fake, calls, tmp_path


def _files_resp(*files, next_token=None):
    import json as _json
    body = {"files": list(files)}
    if next_token:
        body["nextPageToken"] = next_token
    return (200, _json.dumps(body).encode())


class TestGdriveProvider:
    def test_is_configured_uses_prefix(self, monkeypatch):
        for var in ("WLI_GDRIVE_CLIENT_ID", "WLI_GDRIVE_CLIENT_SECRET",
                    "WLI_GDRIVE_REFRESH_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        prov = backup_provider.GoogleDriveProvider("WLI_GDRIVE", "WenliangBackup")
        assert prov.is_configured() is False
        monkeypatch.setenv("WLI_GDRIVE_CLIENT_ID", "x")
        monkeypatch.setenv("WLI_GDRIVE_CLIENT_SECRET", "y")
        assert prov.is_configured() is False
        monkeypatch.setenv("WLI_GDRIVE_REFRESH_TOKEN", "z")
        assert prov.is_configured() is True

    def test_default_prefix_is_gdrive(self, monkeypatch):
        monkeypatch.setenv("GDRIVE_CLIENT_ID", "x")
        monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "y")
        monkeypatch.setenv("GDRIVE_REFRESH_TOKEN", "z")
        assert backup_provider.GoogleDriveProvider().is_configured() is True

    def test_upload_new_file_multipart_create(self, gdrive):
        prov, fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"IMGBYTES")
        fake.responses = [_files_resp(), (200, b'{"id": "NEW1"}')]
        prov.upload(f, "data/Family/receipts/2026-06/a.jpg")
        up = calls[-1]
        assert up["method"] == "POST"
        assert "uploadType=multipart" in up["url"]
        assert b"IMGBYTES" in up["data"]
        assert b'"rel": "data/Family/receipts/2026-06/a.jpg"' in up["data"]
        assert b'"parents": ["FOLDER1"]' in up["data"]

    def test_upload_existing_file_patches(self, gdrive):
        prov, fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"V2")
        fake.responses = [_files_resp({"id": "OLD9"}), (200, b'{"id": "OLD9"}')]
        prov.upload(f, "data/Family/a.jpg")
        up = calls[-1]
        assert up["method"] == "PATCH"
        assert "/files/OLD9?" in up["url"]
        assert b'"parents"' not in up["data"]

    def test_delete_missing_is_noop(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [_files_resp()]
        prov.delete("gone.jpg")
        assert len(calls) == 1

    def test_list_remote_paginates_and_maps_rel(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [
            _files_resp({"id": "1", "size": "10",
                         "appProperties": {"rel": "config.json"}}, next_token="T2"),
            _files_resp({"id": "2", "size": "20",
                         "appProperties": {"rel": "data/Family/r.jpg"}},
                        {"id": "3", "size": "5"}),
        ]
        out = prov.list_remote()
        assert out == {"config.json": {"size": 10},
                       "data/Family/r.jpg": {"size": 20}}
        assert "pageToken=T2" in calls[-1]["url"]

    def test_download_writes_bytes(self, gdrive):
        prov, fake, calls, tmp_path = gdrive
        fake.responses = [_files_resp({"id": "F7"}), (200, b"CONTENT")]
        target = tmp_path / "sub" / "x.bin"
        prov.download("data/Family/documents/x.bin", target)
        assert target.read_bytes() == b"CONTENT"
        assert "alt=media" in calls[-1]["url"]

    def test_api_error_raises_runtimeerror(self, gdrive):
        prov, fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"x")
        fake.responses = [_files_resp(), (403, b'{"error": "rate limit"}')]
        with pytest.raises(RuntimeError):
            prov.upload(f, "a.jpg")

    def test_two_instances_isolate_folder_cache(self, monkeypatch):
        a = backup_provider.GoogleDriveProvider("GDRIVE", "RootA")
        b = backup_provider.GoogleDriveProvider("WLI_GDRIVE", "RootB")
        a._folder_cache["id"] = "FA"
        assert b._folder_cache["id"] is None


import backup_sync


@pytest.fixture
def bk(tmp_path, monkeypatch):
    """Per-member engine: fake ROOT, global clock in tmp, one configured member (Jim)."""
    root = tmp_path / "root"
    (root / "data" / "Jim").mkdir(parents=True)
    (root / "data" / "Family").mkdir(parents=True)
    (root / "config.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(backup_sync, "ROOT", root)
    monkeypatch.setattr(backup_sync, "_DATA_DIRNAME", "data")
    monkeypatch.setattr(backup_sync, "STATE_FILE", tmp_path / "clock.json")
    monkeypatch.setattr(backup_sync, "CFG", {"enabled": True, "debounce_seconds": 60})

    class FakeProvider:
        def __init__(self):
            self.files = {}
            self.fail_uploads = False
            self._configured = True
        def is_configured(self):
            return self._configured
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
    pref = {"name": "Jim", "dir": "Jim", "provider": "google_drive",
            "cred_prefix": "GDRIVE", "remote_root": "FamilyAssistant",
            "enabled": True, "scopes": ["Jim", "Family", "config.json"]}
    monkeypatch.setattr(backup_sync, "_backup_members", lambda: [("Jim", pref)])
    monkeypatch.setattr(backup_sync, "_make_provider", lambda p: fake)
    return root, fake, pref


class TestStateClock:
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
        monkeypatch.setattr(backup_sync, "STATE_FILE", Path("Z:/nope/state.json"))
        backup_sync.mark_dirty()


class TestSync:
    def _jim_manifest(self, root):
        return backup_sync._load_json(root / "data" / "Jim" / ".backup_manifest.json")

    def test_uploads_new_and_skips_unchanged(self, bk):
        root, fake, _ = bk
        d = root / "data" / "Family" / "documents"
        d.mkdir(parents=True)
        (d / "a.pdf").write_bytes(b"img-a")
        r1 = backup_sync.sync("Jim")
        assert "data/Family/documents/a.pdf" in r1["uploaded"]
        assert fake.files["data/Family/documents/a.pdf"] == b"img-a"
        r2 = backup_sync.sync("Jim")
        assert r2["uploaded"] == [] and r2["skipped"] >= 1

    def test_manifest_lands_under_member(self, bk):
        root, fake, _ = bk
        (root / "data" / "Jim" / "note.txt").write_bytes(b"x")
        backup_sync.sync("Jim")
        assert (root / "data" / "Jim" / ".backup_manifest.json").exists()
        assert "data/Jim/note.txt" in self._jim_manifest(root)

    def test_reuploads_changed(self, bk):
        root, fake, _ = bk
        f = root / "data" / "Family" / "x.bin"
        f.write_bytes(b"v1")
        backup_sync.sync("Jim")
        f.write_bytes(b"v2")
        r = backup_sync.sync("Jim")
        assert "data/Family/x.bin" in r["uploaded"]
        assert fake.files["data/Family/x.bin"] == b"v2"

    def test_propagates_deletes(self, bk):
        root, fake, _ = bk
        f = root / "data" / "Family" / "old.pdf"
        f.write_bytes(b"x")
        backup_sync.sync("Jim")
        assert "data/Family/old.pdf" in fake.files
        f.unlink()
        r = backup_sync.sync("Jim")
        assert "data/Family/old.pdf" in r["deleted"]
        assert "data/Family/old.pdf" not in fake.files

    def test_empty_scope_skips_mirror_delete(self, bk):
        # 守卫：local 全空但清单非空时不镜像删除（防盘未挂/误清空 scope 抹掉整个远端）
        root, fake, _ = bk
        (root / "data" / "Family" / "keep.pdf").write_bytes(b"x")
        backup_sync.sync("Jim")
        assert "data/Family/keep.pdf" in fake.files
        (root / "data" / "Family" / "keep.pdf").unlink()
        (root / "config.json").unlink()          # 现在所有 scope 都解析为空
        r = backup_sync.sync("Jim")
        assert r["deleted"] == []                 # 未删任何远端
        assert "data/Family/keep.pdf" in fake.files
        assert r["errors"]                        # 记录告警

    def test_sqlite_snapshot_with_open_connection(self, bk):
        import os
        import sqlite3 as sq
        import tempfile
        root, fake, _ = bk
        db = root / "data" / "Family" / "ledger.db"
        conn = sq.connect(str(db))
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        try:
            r = backup_sync.sync("Jim")
            assert "data/Family/ledger.db" in r["uploaded"]
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            Path(tmp).write_bytes(fake.files["data/Family/ledger.db"])
            check = sq.connect(tmp)
            assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 1
            check.close()
            os.unlink(tmp)
        finally:
            conn.close()

    def test_per_member_error_recorded(self, bk):
        root, fake, _ = bk
        (root / "data" / "Family" / "a.jpg").write_bytes(b"x")
        fake.fail_uploads = True
        r = backup_sync.sync("Jim")
        assert r["errors"]
        mst = backup_sync._load_json(root / "data" / "Jim" / ".backup_state.json")
        assert mst["last_error"] and mst["last_sync"]

    def test_sync_unknown_member_raises(self, bk):
        with pytest.raises(ValueError):
            backup_sync.sync("Nobody")


class TestTick:
    def test_disabled_noop(self, bk):
        backup_sync.CFG["enabled"] = False
        backup_sync.mark_dirty()
        assert backup_sync.backup_tick() is False

    def test_clean_noop(self, bk):
        assert backup_sync.backup_tick() is False

    def test_recent_write_waits(self, bk):
        root, _, _ = bk
        (root / "data" / "Family" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()
        assert backup_sync.backup_tick() is False  # 60s 未到

    def test_quiet_period_syncs_and_clears(self, bk):
        root, fake, _ = bk
        (root / "data" / "Family" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is True
        assert "data/Family/a.jpg" in fake.files
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert st["dirty_since"] is None
        assert backup_sync.backup_tick(now=later) is False

    def test_unconfigured_member_skipped(self, bk):
        root, fake, _ = bk
        fake._configured = False
        backup_sync.mark_dirty()
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is False

    def test_failing_member_keeps_dirty(self, bk):
        root, fake, _ = bk
        (root / "data" / "Family" / "a.jpg").write_bytes(b"x")
        fake.fail_uploads = True
        backup_sync.mark_dirty()
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is True
        st = backup_sync._load_json(backup_sync.STATE_FILE)
        assert st["dirty_since"] is not None

    def test_tick_never_raises(self, bk, monkeypatch):
        root, fake, _ = bk
        (root / "data" / "Family" / "a.jpg").write_bytes(b"x")
        backup_sync.mark_dirty()
        def boom(_m):
            raise RuntimeError("explode")
        monkeypatch.setattr(backup_sync, "sync", boom)
        later = datetime.now() + timedelta(seconds=120)
        assert backup_sync.backup_tick(now=later) is False
        mst = backup_sync._load_json(root / "data" / "Jim" / ".backup_state.json")
        assert "explode" in (mst.get("last_error") or "")


class TestRestoreVerifyStatus:
    def test_restore_to_empty_root(self, bk):
        root, fake, _ = bk
        fake.files = {
            "data/Family/ledger.db": b"db-bytes",
            "data/Family/receipts/2026-06/a.jpg": b"img",
            "config.json": b"{}",
        }
        r = backup_sync.restore("Jim")
        assert sorted(r["downloaded"]) == sorted(fake.files)
        assert (root / "data" / "Family" / "receipts" / "2026-06" / "a.jpg").read_bytes() == b"img"
        manifest = backup_sync._load_json(root / "data" / "Jim" / ".backup_manifest.json")
        assert set(manifest) == set(fake.files)

    def test_restore_refuses_non_empty(self, bk):
        root, fake, _ = bk
        (root / "data" / "Family" / "existing.jpg").write_bytes(b"x")
        fake.files = {"data/Family/other.jpg": b"y"}
        with pytest.raises(ValueError):
            backup_sync.restore("Jim")
        backup_sync.restore("Jim", force=True)
        assert (root / "data" / "Family" / "other.jpg").exists()

    def test_restore_unconfigured_raises(self, bk):
        root, fake, _ = bk
        fake._configured = False
        with pytest.raises(ValueError):
            backup_sync.restore("Jim")

    def test_restore_bootstrap_via_override(self, bk, monkeypatch):
        root, fake, _ = bk
        monkeypatch.setattr(backup_sync, "_backup_members", lambda: [])
        fake.files = {"data/members.json": b"{}", "config.json": b"{}"}
        override = {"provider": "google_drive", "cred_prefix": "GDRIVE",
                    "remote_root": "FamilyAssistant", "scopes": [], "dir": "Jim"}
        r = backup_sync.restore("Jim Zheng", override=override)
        assert "data/members.json" in r["downloaded"]
        assert (root / "data" / "members.json").exists()

    def test_verify_reports(self, bk):
        root, fake, _ = bk
        (root / "data" / "Family" / "a.jpg").write_bytes(b"aaaa")
        backup_sync.sync("Jim")
        fake.files["data/Family/ghost.jpg"] = b"zz"
        fake.files["data/Family/a.jpg"] = b"a"
        v = backup_sync.verify("Jim")
        assert "data/Family/ghost.jpg" in v["extra_remote"]
        assert "data/Family/a.jpg" in v["size_mismatch"]
        del fake.files["data/Family/a.jpg"]
        v = backup_sync.verify("Jim")
        assert "data/Family/a.jpg" in v["missing_remote"]

    def test_status_global_and_members(self, bk):
        s = backup_sync.status()
        assert s["enabled"] is True
        assert len(s["members"]) == 1
        row = s["members"][0]
        assert row["member"] == "Jim" and row["configured"] is True
        assert row["files_tracked"] == 0
        backup_sync.mark_dirty()
        s = backup_sync.status()
        assert s["dirty_since"] is not None

    def test_status_single_member_filter(self, bk):
        assert [m["member"] for m in backup_sync.status(member="Jim")["members"]] == ["Jim"]
        assert backup_sync.status(member="Nobody")["members"] == []


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


def _enabled_member_env(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"backup": {"enabled": True, "debounce_seconds": 60},
                               "data_root": "data"}), encoding="utf-8")
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({"Jim Zheng": {"dir": "Jim", "backup": {
        "provider": "google_drive", "cred_prefix": "GDRIVE",
        "remote_root": "FamilyAssistant", "enabled": True,
        "scopes": ["Jim", "Family"]}}}), encoding="utf-8")
    return {"BACKUP_STATE_DIR": str(tmp_path), "BACKUP_CONFIG": str(cfg),
            "BACKUP_MEMBERS": str(mp), "DATA_ROOT": str(tmp_path / "data")}


class TestCli:
    def test_backup_status_works_unconfigured(self, tmp_path):
        r = _run_cli("backup-status", env_extra=_disabled_cfg(tmp_path))
        assert r.returncode == 0
        assert "enabled" in r.stdout and "False" in r.stdout

    def test_backup_now_disabled_exits_1(self, tmp_path):
        r = _run_cli("backup-now", env_extra=_disabled_cfg(tmp_path))
        assert r.returncode == 1
        assert "未启用" in r.stderr

    def test_status_lists_member(self, tmp_path):
        env = _enabled_member_env(tmp_path)
        for var in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"):
            env[var] = ""
        r = _run_cli("backup-status", env_extra=env)
        assert r.returncode == 0
        assert "Jim" in r.stdout

    def test_now_skips_unconfigured_member(self, tmp_path):
        env = _enabled_member_env(tmp_path)
        for var in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"):
            env[var] = ""
        r = _run_cli("backup-now", env_extra=env)
        assert r.returncode == 0
        assert "跳过" in (r.stdout + r.stderr)

    def test_restore_requires_member(self, tmp_path):
        r = _run_cli("backup-restore", env_extra=_enabled_member_env(tmp_path))
        assert r.returncode == 1
        assert "member" in r.stderr.lower() or "成员" in r.stderr


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
