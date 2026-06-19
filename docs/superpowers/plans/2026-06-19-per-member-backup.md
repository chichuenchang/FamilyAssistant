# Per-Member Remote Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each family member back up to their own remote provider/account, selecting what they back up via a `backup` block in `members.json`, while Jim's existing Google Drive backup carries over with zero re-upload.

**Architecture:** Generalize the single monolithic backup engine into a per-member mirror over one shared debounce clock. `members.json` gains a per-member `backup` block (`provider` + `cred_prefix` + `remote_root` + rel-path `scopes`). `backup_provider.py` becomes a `GoogleDriveProvider(cred_prefix, remote_root)` class selected through a registry. `backup_sync.backup_tick()` loops members; each runs an isolated mirror with its own manifest + `last_sync`/`last_error`. `mark_dirty()` and the global `data/.backup_state.json` clock are unchanged.

**Tech Stack:** Python 3 (stdlib only — `urllib`, `sqlite3`, `hashlib`, `json`, `pathlib`), pytest, Google Drive REST v3 + OAuth refresh token.

## Global Constraints

- **Stdlib only** — no third-party packages. Network via `urllib.request`; the single HTTP seam is module-level `backup_provider._http`.
- **Credentials only via environment** — never write a literal token/key/secret in code. Provider reads `{cred_prefix}_CLIENT_ID`, `{cred_prefix}_CLIENT_SECRET`, `{cred_prefix}_REFRESH_TOKEN`. Default `cred_prefix` is `GDRIVE`.
- **Rel scheme is ROOT-relative posix** — every manifest/remote key is the file path relative to the project ROOT, e.g. `data/Jim/notes/n.db`, `data/members.json`, `config.json`. Do not change this scheme.
- **Hard excludes always apply** — `_HARD_EXCLUDE_NAMES` (`.backup_manifest.json`, `.backup_state.json`, `.sync_state.json`, `.telegram_offset`, `.doc_reminder_state`, `.calendar_state.json`, `.image_gc_state.json`) plus any name containing `creds`. Never uploaded even when their directory is in scope.
- **Atomic + crash-safe writes** — JSON state via the existing `_save_json`; `members.json` via the existing `members._save_members` (tempfile + `os.replace`). Manifest saved after each file so a mid-run crash only re-does the remainder.
- **Never-raise boundaries** — `mark_dirty()` and `backup_tick()` must never raise; per-member failures are isolated to that member's `last_error`.
- **Code comments/docstrings** follow the existing codebase style (Chinese prose comments, English identifiers).
- **Windows console** — CLI/provider entrypoints keep the existing `PYTHONIOENCODING`/`reconfigure` guard.
- **Test hooks** — `BACKUP_STATE_DIR` (clock dir), `BACKUP_CONFIG` (alt config.json), `BACKUP_MEMBERS` (alt members.json, added in Task 4), `DATA_ROOT` (paths.py). Tests rely on these for isolation — keep them working.

---

## File Structure

- `.codewhale/skills/Agent_Runtime/members.py` — **modify**: add `backup_pref()`.
- `.codewhale/skills/Remote_Backup/backup_provider.py` — **modify**: `GoogleDriveProvider` class + `--auth --prefix`; keep `_http` module-level seam.
- `.codewhale/skills/Remote_Backup/backup_sync.py` — **modify**: scope resolver, provider registry/factory, member enumeration, per-member `sync/verify/restore/status`, per-member loop in `backup_tick`; `mark_dirty` + global clock unchanged.
- `.codewhale/skills/Remote_Backup/cli.py` — **modify**: `--member` on all four commands + restore bootstrap flags.
- `.codewhale/skills/Agent_Runtime/migrate_backup.py` — **create**: one-time migration.
- `.codewhale/skills/Remote_Backup/SKILL.md` — **modify**: per-member model + setup/bootstrap/migration docs.
- `tests/test_remote_backup.py` — **modify**: rewrite provider + engine + CLI test classes.
- `tests/test_migrate_backup.py` — **create**: migration test.

Run the whole suite with: `python -m pytest tests/ -q` (from project root).

## Task Map & Coupling

- **Task 1** (`members.backup_pref`) and **Task 2** (scope resolver) are purely additive — the existing engine + tests stay green.
- **Task 3** (provider class) keeps a thin module shim so the old engine still runs.
- **Task 4** is the **per-member flip**: `backup_sync` (`sync`/`tick`/`restore`/`verify`/`status`) + `cli.py` + their tests change together. They share the new helper surface and the same `bk` fixture; splitting them would leave the suite red at a task boundary (e.g. the moment `sync()` takes a `member` arg, the old `backup_tick` and old CLI break). They are therefore one task with several test-first commits, green at the task boundary.
- **Task 5** (migration) and **Task 6** (docs) are independent tails.

---

## Task 1: `members.backup_pref()`

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/members.py`
- Test: `tests/test_members.py`

**Interfaces:**
- Consumes: existing `load_members()`, `member_dir_name()`.
- Produces: `backup_pref(name, members_path=None) -> dict | None` returning normalized
  `{"provider": str, "cred_prefix": str, "remote_root": str, "enabled": bool, "scopes": list[str]}`
  or `None` when the member has no `backup` block.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_members.py`:

```python
import json


def test_backup_pref_absent_is_none(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text('{"Jim Zheng": {"dir": "Jim"}}', encoding="utf-8")
    assert members.backup_pref("Jim Zheng", members_path=mp) is None


def test_backup_pref_unknown_member_is_none(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text('{}', encoding="utf-8")
    assert members.backup_pref("Nobody", members_path=mp) is None


def test_backup_pref_normalizes_and_defaults(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({"Jim Zheng": {"dir": "Jim", "backup": {
        "provider": "google_drive", "enabled": True,
        "scopes": ["Jim", "Family"]}}}), encoding="utf-8")
    p = members.backup_pref("Jim Zheng", members_path=mp)
    assert p["provider"] == "google_drive"
    assert p["cred_prefix"] == "GDRIVE"          # default
    assert p["remote_root"] == "Jim"             # default = dir name
    assert p["enabled"] is True
    assert p["scopes"] == ["Jim", "Family"]


def test_backup_pref_explicit_prefix_and_root(tmp_path):
    import members
    mp = tmp_path / "members.json"
    mp.write_text(json.dumps({"Wenliang Li": {"dir": "Wenliang", "backup": {
        "provider": "google_drive", "cred_prefix": "WLI_GDRIVE",
        "remote_root": "WenliangBackup", "enabled": False, "scopes": []}}}),
        encoding="utf-8")
    p = members.backup_pref("Wenliang Li", members_path=mp)
    assert p["cred_prefix"] == "WLI_GDRIVE"
    assert p["remote_root"] == "WenliangBackup"
    assert p["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_members.py -k backup_pref -v`
Expected: FAIL with `AttributeError: module 'members' has no attribute 'backup_pref'`.

- [ ] **Step 3: Implement `backup_pref`**

Add to `.codewhale/skills/Agent_Runtime/members.py` after `sync_pref`:

```python
def backup_pref(name: str, members_path: Path | None = None) -> dict | None:
    """成员的远程备份偏好。无 backup 块 → None（= 不备份，仅本地）。

    返回规范化 dict：{provider, cred_prefix, remote_root, enabled, scopes}。
    凭据永远不在此，走 {cred_prefix}_CLIENT_ID/SECRET/REFRESH_TOKEN 环境变量。
    remote_root 缺省取成员目录名；cred_prefix 缺省 GDRIVE（兼容现有单一备份）。
    """
    entry = load_members(members_path).get(name)
    if not isinstance(entry, dict):
        return None
    b = entry.get("backup")
    if not isinstance(b, dict):
        return None
    return {
        "provider": b.get("provider", "google_drive"),
        "cred_prefix": b.get("cred_prefix", "GDRIVE"),
        "remote_root": b.get("remote_root") or member_dir_name(name, members_path),
        "enabled": bool(b.get("enabled", False)),
        "scopes": list(b.get("scopes") or []),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_members.py -k backup_pref -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/members.py tests/test_members.py
git commit -m "feat(backup): members.backup_pref resolves per-member backup config"
```

---

## Task 2: Scope resolver in `backup_sync`

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_sync.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Consumes: existing module globals `ROOT`, `_excluded`.
- Produces:
  - `_DATA_DIRNAME: str` (config `data_root` name, default `"data"`).
  - `_scope_to_prefix(token: str) -> str` — `"config.json"` → `"config.json"`; else `"<data>/<token>"`.
  - `_member_files(scopes: list[str]) -> dict[str, Path]` — `{rel(posix): abs}` for in-scope files, hard-excludes applied.

This task is purely additive; the existing engine + tests stay green.

- [ ] **Step 1: Write the failing tests**

Add a new test class at the end of `tests/test_remote_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestScopeResolver -v`
Expected: FAIL with `AttributeError: ... has no attribute '_DATA_DIRNAME'` / `_scope_to_prefix`.

- [ ] **Step 3: Implement the resolver**

In `.codewhale/skills/Remote_Backup/backup_sync.py`, add a `_cfg_path()` helper and `_DATA_DIRNAME` near the config block, and the two functions after `_excluded`:

```python
def _cfg_path() -> Path:
    return Path(os.environ.get("BACKUP_CONFIG") or (ROOT / "config.json"))


def _data_dirname() -> str:
    """data_root 目录名（config.data_root，缺省 data）。备份 rel 的 <data>/ 段。"""
    try:
        raw = json.loads(_cfg_path().read_text(encoding="utf-8"))
        return raw.get("data_root") or "data"
    except Exception:
        return "data"


_DATA_DIRNAME = _data_dirname()


def _scope_to_prefix(token: str) -> str:
    """scope token → ROOT 相对前缀（posix）。

    config.json 是 data_root 外唯一备份文件，识别为别名；其余一律 data_root 相对。
    """
    if token == "config.json":
        return "config.json"
    return f"{_DATA_DIRNAME}/{token}"


def _member_files(scopes: list[str]) -> dict[str, Path]:
    """成员 scope 集合 → {rel(posix): 绝对路径}，应用硬排除。

    token 指向文件则收该文件；指向目录则递归；不存在则静默跳过（migrate 前的空目录）。
    前缀按目录边界匹配（rglob 天然如此），不会把 data/Jimbo 当成 data/Jim。
    """
    out: dict[str, Path] = {}
    for token in scopes:
        p = ROOT / _scope_to_prefix(token)
        if p.is_file():
            rel = p.relative_to(ROOT).as_posix()
            if not _excluded(rel):
                out[rel] = p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(ROOT).as_posix()
                if not _excluded(rel):
                    out[rel] = f
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py::TestScopeResolver -v`
Expected: PASS (5 tests). Then `python -m pytest tests/test_remote_backup.py -q` — all existing tests still green (additive change).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_sync.py tests/test_remote_backup.py
git commit -m "feat(backup): rel-path scope resolver (token -> ROOT-relative fileset)"
```

---

## Task 3: `GoogleDriveProvider` class + back-compat shim

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_provider.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Consumes: existing module-level `_http(method, url, data, headers) -> (status, bytes)` seam (kept).
- Produces:
  - `GoogleDriveProvider(cred_prefix="GDRIVE", remote_root="FamilyAssistant")` with methods
    `is_configured()`, `upload(local_path, remote_rel)`, `delete(remote_rel)`,
    `list_remote() -> dict`, `download(remote_rel, local_path)`.
  - A back-compat `_default` instance + module `is_configured/upload/delete/list_remote/download`
    delegating to it (so the not-yet-flipped engine keeps running until Task 4). Removed in Task 4.
  - `--auth --prefix PREFIX` on the CLI entrypoint.

- [ ] **Step 1: Rewrite the provider tests against the class**

Replace the `gdrive` fixture and `TestGdriveProvider` class in `tests/test_remote_backup.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -v`
Expected: FAIL with `AttributeError: module 'backup_provider' has no attribute 'GoogleDriveProvider'`.

- [ ] **Step 3: Rewrite `backup_provider.py` as a class + shim**

Keep the module docstring, imports, the Windows-console guard, and the constants
(`SCOPE`, `AUTH_URL`, `TOKEN_URL`, `API`, `UPLOAD_API`, `_FOLDER_MIME`, `ROOT`). Replace the rest
(the module-level `_remote_root`, caches, `_token`, `_api_json`, `_q`, `_folder_id`, `_find`,
`upload`, `delete`, `list_remote`, `download`) with the class below. Keep `_http` module-level:

```python
def _http(method: str, url: str, data: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    """唯一 HTTP 出口（测试在此打桩）。返回 (status, body)。"""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _q(value: str) -> str:
    """Drive 查询字符串里的单引号/反斜杠转义。"""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveProvider:
    """Google Drive 备份 provider（drive.file 最小权限）。

    凭据从 {cred_prefix}_CLIENT_ID/SECRET/REFRESH_TOKEN 环境变量读取，进程内缓存
    access token 与 remote_root 文件夹 id。多成员各持一个实例，缓存互不干扰。
    云端布局：文件平铺在 remote_root 文件夹，rel 存 appProperties.rel（上限 124B）。
    """

    def __init__(self, cred_prefix: str = "GDRIVE",
                 remote_root: str = "FamilyAssistant") -> None:
        self.cred_prefix = cred_prefix
        self.remote_root = remote_root
        self._token_cache: dict = {"access": None, "exp": 0.0}
        self._folder_cache: dict = {"id": None}

    def _env(self, suffix: str) -> str:
        return os.environ.get(f"{self.cred_prefix}_{suffix}", "")

    def is_configured(self) -> bool:
        return all(self._env(s) for s in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"))

    def _token(self) -> str:
        if self._token_cache["access"] and time.time() < self._token_cache["exp"]:
            return self._token_cache["access"]
        data = urllib.parse.urlencode({
            "client_id": self._env("CLIENT_ID"),
            "client_secret": self._env("CLIENT_SECRET"),
            "refresh_token": self._env("REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }).encode()
        status, body = _http("POST", TOKEN_URL, data,
                             {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            raise RuntimeError(f"Google OAuth token 刷新失败 {status}: {body[:200]!r}")
        tok = json.loads(body)
        self._token_cache["access"] = tok["access_token"]
        self._token_cache["exp"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        return self._token_cache["access"]

    def _api_json(self, method: str, url: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        status, body = _http(method, url, data, headers)
        if status >= 300:
            raise RuntimeError(f"Drive API {method} {url} → {status}: {body[:200]!r}")
        return json.loads(body) if body else {}

    def _folder_id(self) -> str:
        if self._folder_cache["id"]:
            return self._folder_cache["id"]
        query = (f"name = '{_q(self.remote_root)}' and mimeType = '{_FOLDER_MIME}' "
                 f"and trashed = false")
        r = self._api_json("GET", f"{API}/files?" + urllib.parse.urlencode(
            {"q": query, "fields": "files(id)", "pageSize": 10}))
        files = r.get("files") or []
        if files:
            self._folder_cache["id"] = files[0]["id"]
        else:
            created = self._api_json("POST", f"{API}/files",
                                     {"name": self.remote_root, "mimeType": _FOLDER_MIME})
            self._folder_cache["id"] = created["id"]
        return self._folder_cache["id"]

    def _find(self, remote_rel: str) -> str | None:
        query = (f"appProperties has {{ key='rel' and value='{_q(remote_rel)}' }} "
                 f"and '{self._folder_id()}' in parents and trashed = false")
        r = self._api_json("GET", f"{API}/files?" + urllib.parse.urlencode(
            {"q": query, "fields": "files(id)", "pageSize": 2}))
        files = r.get("files") or []
        return files[0]["id"] if files else None

    def upload(self, local_path, remote_rel: str) -> None:
        content = Path(local_path).read_bytes()
        existing = self._find(remote_rel)
        meta: dict = {"name": remote_rel.rsplit("/", 1)[-1],
                      "appProperties": {"rel": remote_rel}}
        if existing is None:
            meta["parents"] = [self._folder_id()]
            method, url = "POST", f"{UPLOAD_API}/files?uploadType=multipart"
        else:
            method, url = "PATCH", f"{UPLOAD_API}/files/{existing}?uploadType=multipart"
        boundary = "codewhale-backup-7e3f9d"
        body = ((f"--{boundary}\r\n"
                 f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                 f"{json.dumps(meta, ensure_ascii=False)}\r\n"
                 f"--{boundary}\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n").encode()
                + content + f"\r\n--{boundary}--".encode())
        headers = {"Authorization": f"Bearer {self._token()}",
                   "Content-Type": f"multipart/related; boundary={boundary}"}
        status, resp = _http(method, url, body, headers)
        if status >= 300:
            raise RuntimeError(f"Drive 上传失败 {remote_rel} → {status}: {resp[:200]!r}")

    def delete(self, remote_rel: str) -> None:
        fid = self._find(remote_rel)
        if fid is None:
            return
        headers = {"Authorization": f"Bearer {self._token()}"}
        status, body = _http("DELETE", f"{API}/files/{fid}", None, headers)
        if status >= 300 and status != 404:
            raise RuntimeError(f"Drive 删除失败 {remote_rel} → {status}: {body[:200]!r}")

    def list_remote(self) -> dict:
        out: dict = {}
        page_token = None
        while True:
            params = {"q": f"'{self._folder_id()}' in parents and trashed = false",
                      "fields": "nextPageToken, files(id, size, appProperties)",
                      "pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            r = self._api_json("GET", f"{API}/files?" + urllib.parse.urlencode(params))
            for f in r.get("files", []):
                rel = (f.get("appProperties") or {}).get("rel")
                if rel:
                    out[rel] = {"size": int(f.get("size") or 0)}
            page_token = r.get("nextPageToken")
            if not page_token:
                break
        return out

    def download(self, remote_rel: str, local_path) -> None:
        fid = self._find(remote_rel)
        if fid is None:
            raise RuntimeError(f"云端不存在: {remote_rel}")
        headers = {"Authorization": f"Bearer {self._token()}"}
        status, body = _http("GET", f"{API}/files/{fid}?alt=media", None, headers)
        if status >= 300:
            raise RuntimeError(f"Drive 下载失败 {remote_rel} → {status}: {body[:200]!r}")
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


# ── 向后兼容垫片：默认实例（GDRIVE / FamilyAssistant）。Task 4 删除最后一个调用方后移除。
_default = GoogleDriveProvider("GDRIVE", "FamilyAssistant")


def is_configured() -> bool:
    return _default.is_configured()


def upload(local_path, remote_rel: str) -> None:
    _default.upload(local_path, remote_rel)


def delete(remote_rel: str) -> None:
    _default.delete(remote_rel)


def list_remote() -> dict:
    return _default.list_remote()


def download(remote_rel: str, local_path) -> None:
    _default.download(remote_rel, local_path)
```

- [ ] **Step 4: Parameterize `--auth` by prefix**

In `_run_auth`, accept a `prefix` and use prefixed env + printed `setx` (the local-loopback OAuth
flow body is unchanged — only the three lines below change):

```python
def _run_auth(prefix: str = "GDRIVE") -> None:
    import http.server
    import threading
    import webbrowser

    client_id = os.environ.get(f"{prefix}_CLIENT_ID", "")
    client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print(f"先设置 {prefix}_CLIENT_ID / {prefix}_CLIENT_SECRET 环境变量"
              "（Google Cloud Console → OAuth 客户端，Desktop app 类型），然后开新终端重跑。")
        raise SystemExit(1)
    # ... unchanged local-loopback OAuth flow (server, browser, code exchange) ...
    # at the end, the printed setx line becomes:
    #     print(f'  setx {prefix}_REFRESH_TOKEN "{refresh}"')
```

Update the `__main__` block:

```python
if __name__ == "__main__":
    if "--auth" in sys.argv:
        prefix = "GDRIVE"
        if "--prefix" in sys.argv:
            prefix = sys.argv[sys.argv.index("--prefix") + 1]
        _run_auth(prefix)
    else:
        print(__doc__)
        print(f"configured (GDRIVE): {is_configured()}")
        print("一次性授权：python backup_provider.py --auth [--prefix PREFIX]")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py -q`
Expected: PASS — `TestGdriveProvider` (9) green against the class; the engine tests still green
because they monkeypatch `backup_sync.provider`, and the `_default` shim keeps the live engine
runnable.

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_provider.py tests/test_remote_backup.py
git commit -m "refactor(backup): GoogleDriveProvider class (cred_prefix + remote_root) + compat shim"
```

---

## Task 4: Per-member engine + CLI flip

This is the core task. `sync`/`backup_tick`/`restore`/`verify`/`status` and `cli.py` change
together (they share the new helper surface and the `bk` fixture). Work test-first, committing
after each coherent slice; the suite is green at the task boundary (Step 9).

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_sync.py`
- Modify: `.codewhale/skills/Remote_Backup/cli.py`
- Modify: `.codewhale/skills/Remote_Backup/backup_provider.py` (remove the `_default` shim — last caller gone)
- Test: `tests/test_remote_backup.py`

**Interfaces produced:**
- `_REGISTRY: dict[str, type]`, `_make_provider(pref) -> provider`.
- `_members_path() -> Path | None` (honours `BACKUP_MEMBERS` env hook).
- `_backup_members() -> list[tuple[str, dict]]` — each `pref` enriched with `"name"` + `"dir"`.
- `_resolve(member) -> dict | None`, `_member_manifest_file(pref)`, `_member_state_file(pref)`.
- `sync(member) -> {member, uploaded, deleted, skipped, errors, clean_write}`.
- `backup_tick(now=None) -> bool` (loops members; never raises).
- `restore(member, force=False, override=None) -> {member, downloaded}`.
- `verify(member) -> {member, ok, missing_remote, extra_remote, size_mismatch}`.
- `status(member=None) -> {enabled, dirty_since, last_write, members:[...]}`.
- CLI: `backup-now/-status/-verify [--member NAME]`, `backup-restore --member NAME [--force] [--provider P] [--prefix P] [--remote-root R] [--dir D]`.
- `mark_dirty()` and the global `STATE_FILE` clock are **unchanged**.

- [ ] **Step 1: Replace the engine + CLI test classes (failing tests)**

In `tests/test_remote_backup.py`: replace the `bk` fixture, `TestStateAndWalk`, `TestSync`,
`TestTick`, `TestRestoreVerifyStatus`, and `TestCli` with the versions below. (`TestScopeResolver`,
`TestGdriveProvider`, `TestDirtyHooks`, `TestAgentIntegration` stay as-is.)

```python
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
```

Then replace `TestCli` with (uses the `BACKUP_MEMBERS`/`DATA_ROOT` hooks for isolation; keep the
existing `_disabled_cfg`, `_run_cli`, and `_CLI` definitions from the file):

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestSync tests/test_remote_backup.py::TestTick tests/test_remote_backup.py::TestRestoreVerifyStatus -v`
Expected: FAIL — `sync()` takes no `member`, `_backup_members` missing, etc.

- [ ] **Step 3: Rewrite `backup_sync.py` top matter (imports, cfg, member helpers)**

In `.codewhale/skills/Remote_Backup/backup_sync.py`:

(a) After `import backup_provider`, add the Agent_Runtime path + members import:

```python
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Agent_Runtime"))
import members as _members
```

(b) Shrink config to subsystem knobs and **remove** module-level `MANIFEST_FILE` and the old
`_iter_local_files`, `_provider_ready`, and the `import backup_provider as provider` alias:

```python
_FALLBACK_CFG = {"enabled": False, "debounce_seconds": 60}


def _load_cfg() -> dict:
    try:
        raw = json.loads(_cfg_path().read_text(encoding="utf-8"))
        cfg = raw.get("backup")
        if isinstance(cfg, dict):
            return {**_FALLBACK_CFG,
                    **{k: cfg[k] for k in ("enabled", "debounce_seconds") if k in cfg}}
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


CFG = _load_cfg()
```

Keep `_STATE_DIR` + `STATE_FILE` (global clock) and `mark_dirty` exactly as they are.
**Ordering:** the new `_load_cfg` calls `_cfg_path()`, and `CFG = _load_cfg()` runs at import
time — so `_cfg_path` (added in Task 2) MUST be defined before `_load_cfg`. If Task 2 placed it
after `CFG = _load_cfg()`, move it up to just before `_load_cfg`, otherwise import hits a
forward-reference `NameError` (silently caught → `CFG` always falls back to disabled).

(c) Add the registry + member-context helpers (after the scope helpers from Task 2):

```python
_REGISTRY = {"google_drive": backup_provider.GoogleDriveProvider}


def _make_provider(pref: dict):
    cls = _REGISTRY.get(pref["provider"])
    if cls is None:
        raise RuntimeError(f"未知备份 provider: {pref['provider']}")
    return cls(pref["cred_prefix"], pref["remote_root"])


def _members_path():
    """测试钩子：BACKUP_MEMBERS 指向替代 members.json，否则用 members 默认。"""
    p = os.environ.get("BACKUP_MEMBERS")
    return Path(p) if p else None


def _backup_members() -> list[tuple[str, dict]]:
    """有 backup 块的成员 [(name, pref)]；pref 富化 name + dir（成员目录名）。"""
    mp = _members_path()
    out: list[tuple[str, dict]] = []
    for name in _members.member_names(mp):
        pref = _members.backup_pref(name, mp)
        if pref:
            out.append((name, {**pref, "name": name,
                               "dir": _members.member_dir_name(name, mp)}))
    return out


def _resolve(member: str) -> dict | None:
    for name, pref in _backup_members():
        if name == member:
            return pref
    return None


def _member_state_dir(pref: dict) -> Path:
    return ROOT / _DATA_DIRNAME / pref["dir"]


def _member_manifest_file(pref: dict) -> Path:
    return _member_state_dir(pref) / ".backup_manifest.json"


def _member_state_file(pref: dict) -> Path:
    return _member_state_dir(pref) / ".backup_state.json"
```

- [ ] **Step 4: Rewrite `sync` (member-scoped)**

Replace `sync()` in `backup_sync.py`:

```python
def sync(member: str) -> dict:
    """镜像一成员 scope 集合：上传 新/变更，删除 本地已无的远端项。

    清单逐项落盘——中途崩溃只补剩余。返回结果含该成员维度与 clean_write。
    """
    pref = _resolve(member)
    if pref is None:
        raise ValueError(f"成员 {member} 无 backup 配置")
    prov = _make_provider(pref)
    manifest_file = _member_manifest_file(pref)
    started_last_write = _load_json(STATE_FILE).get("last_write")
    manifest = _load_json(manifest_file)
    local = _member_files(pref["scopes"])
    uploaded: list[str] = []
    deleted: list[str] = []
    errors: list[str] = []
    skipped = 0

    for rel, abs_p in local.items():
        snap: Path | None = None
        try:
            if abs_p.suffix == ".db":
                snap = _snapshot_sqlite(abs_p)
                src = snap
            else:
                src = abs_p
            digest = _sha256(src)
            if manifest.get(rel, {}).get("sha256") == digest:
                skipped += 1
            else:
                prov.upload(src, rel)
                manifest[rel] = {"sha256": digest, "size": src.stat().st_size,
                                 "uploaded_at": _now_iso()}
                _save_json(manifest_file, manifest)
                uploaded.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")
        finally:
            if snap is not None:
                try:
                    snap.unlink()
                except OSError:
                    pass

    for rel in [r for r in list(manifest) if r not in local]:
        try:
            prov.delete(rel)
            del manifest[rel]
            _save_json(manifest_file, manifest)
            deleted.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")

    mst = _load_json(_member_state_file(pref))
    mst["last_sync"] = _now_iso()
    mst["last_error"] = "; ".join(errors[:5]) if errors else None
    _save_json(_member_state_file(pref), mst)
    return {"member": member, "uploaded": uploaded, "deleted": deleted,
            "skipped": skipped, "errors": errors,
            "clean_write": _load_json(STATE_FILE).get("last_write") == started_last_write}
```

- [ ] **Step 5: Rewrite `backup_tick` (member loop)**

Replace `backup_tick()` in `backup_sync.py`:

```python
def backup_tick(now: datetime | None = None) -> bool:
    """传输层轮询调用。脏 + 防抖到点则遍历成员各同步一轮。永不抛异常。

    全局时钟（dirty_since/last_write）共享；仅当所有尝试过的成员都成功且期间无新写入
    才清脏，否则保持脏，下轮重试（sha 闸门 → 不重复上传）。返回是否至少跑了一个成员。
    """
    try:
        if not CFG["enabled"]:
            return False
        st = _load_json(STATE_FILE)
        if not st.get("dirty_since"):
            return False
        last_write = st.get("last_write")
        now = now or datetime.now()
        if last_write:
            elapsed = (now - datetime.fromisoformat(last_write)).total_seconds()
            if elapsed < CFG["debounce_seconds"]:
                return False
        ran = False
        all_clean = True
        for name, pref in _backup_members():
            if not pref["enabled"]:
                continue
            try:
                prov = _make_provider(pref)
                if not prov.is_configured():
                    continue                       # 未配置：不算失败，不阻塞清脏
                res = sync(name)
                ran = True
                if res["errors"] or not res["clean_write"]:
                    all_clean = False
            except Exception as e:
                all_clean = False
                try:
                    mst = _load_json(_member_state_file(pref))
                    mst["last_error"] = str(e)
                    _save_json(_member_state_file(pref), mst)
                except Exception:
                    pass
        if ran and all_clean:
            st = _load_json(STATE_FILE)
            if st.get("last_write") == last_write:
                st["dirty_since"] = None
                _save_json(STATE_FILE, st)
        return ran
    except Exception as e:
        try:
            st = _load_json(STATE_FILE)
            st["last_error"] = str(e)
            _save_json(STATE_FILE, st)
        except Exception:
            pass
        return False
```

- [ ] **Step 6: Rewrite `restore` / `verify` / `status` (member-scoped)**

Replace `restore()`, `verify()`, `status()` in `backup_sync.py`:

```python
def restore(member: str, force: bool = False, override: dict | None = None) -> dict:
    """从某成员的云端拉回其文件并重建该成员清单。

    override（dict: provider/cred_prefix/remote_root/scopes/dir）用于新设备引导：
    members.json 尚未恢复、成员无 backup 块时，由 CLI 旗标提供 provider 配置。
    """
    pref = _resolve(member) or override
    if pref is None:
        raise ValueError(f"成员 {member} 无 backup 配置，且未提供 override（--prefix/--remote-root）。")
    if "dir" not in pref:
        pref = {**pref, "dir": _members.member_dir_name(member, _members_path())}
    prov = _make_provider(pref)
    if not prov.is_configured():
        raise ValueError("provider 未配置（检查环境变量），无法恢复。")
    if not force:
        existing = [rel for rel in _member_files(pref.get("scopes") or [])
                    if rel != "config.json"]
        if existing:
            raise ValueError(
                f"本地已有 {len(existing)} 个文件（如 {existing[0]}）。确认覆盖请加 --force。")
    remote = prov.list_remote()
    manifest: dict = {}
    downloaded: list[str] = []
    for rel in sorted(remote):
        if _excluded(rel):
            continue
        target = ROOT / rel
        prov.download(rel, target)
        manifest[rel] = {"sha256": _sha256(target), "size": target.stat().st_size,
                         "uploaded_at": _now_iso()}
        downloaded.append(rel)
    _save_json(_member_manifest_file(pref), manifest)
    mst = _load_json(_member_state_file(pref))
    mst["last_sync"] = _now_iso()
    mst["last_error"] = None
    _save_json(_member_state_file(pref), mst)
    return {"member": member, "downloaded": downloaded}


def verify(member: str) -> dict:
    """某成员清单 vs 其云端列表。"""
    pref = _resolve(member)
    if pref is None:
        raise ValueError(f"成员 {member} 无 backup 配置")
    prov = _make_provider(pref)
    if not prov.is_configured():
        raise ValueError("provider 未配置，无法校验。")
    manifest = _load_json(_member_manifest_file(pref))
    remote = prov.list_remote()
    ok, missing, mismatch = [], [], []
    for rel, meta in manifest.items():
        if rel not in remote:
            missing.append(rel)
        elif remote[rel].get("size") not in (None, meta.get("size")):
            mismatch.append(rel)
        else:
            ok.append(rel)
    extra = [r for r in remote if r not in manifest]
    return {"member": member, "ok": ok, "missing_remote": missing,
            "extra_remote": extra, "size_mismatch": mismatch}


def status(member: str | None = None) -> dict:
    """全局时钟 + 每成员一行（启用/配置/provider/last_sync/last_error/已跟踪文件数）。"""
    clock = _load_json(STATE_FILE)
    rows = []
    for name, pref in _backup_members():
        if member and name != member:
            continue
        try:
            configured = _make_provider(pref).is_configured()
        except Exception:
            configured = False
        mst = _load_json(_member_state_file(pref))
        rows.append({
            "member": name,
            "enabled": pref["enabled"],
            "configured": configured,
            "provider": pref["provider"],
            "remote_root": pref["remote_root"],
            "last_sync": mst.get("last_sync"),
            "last_error": mst.get("last_error"),
            "files_tracked": len(_load_json(_member_manifest_file(pref))),
        })
    return {"enabled": bool(CFG["enabled"]),
            "dirty_since": clock.get("dirty_since"),
            "last_write": clock.get("last_write"),
            "members": rows}
```

- [ ] **Step 7: Remove the provider shim**

In `.codewhale/skills/Remote_Backup/backup_provider.py`, delete the `_default` instance and the
five module-level delegate functions (`is_configured/upload/delete/list_remote/download`) — the
engine now builds instances via `_make_provider`, so the shim has no callers.

- [ ] **Step 8: Rewrite `cli.py`**

Replace the command functions + `main` in `.codewhale/skills/Remote_Backup/cli.py` (keep the
header docstring, the Windows guard, the `sys.path` insert, and `import backup_sync`; delete the
old `_require_ready`):

```python
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
```

- [ ] **Step 9: Run the full backup suite**

Run: `python -m pytest tests/test_remote_backup.py -q`
Expected: PASS — `TestGdriveProvider`, `TestScopeResolver`, `TestStateClock`, `TestSync`,
`TestTick`, `TestRestoreVerifyStatus`, `TestCli`, `TestDirtyHooks` (unchanged `mark_dirty`),
`TestAgentIntegration` (command names unchanged) all green.

- [ ] **Step 10: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_sync.py .codewhale/skills/Remote_Backup/cli.py .codewhale/skills/Remote_Backup/backup_provider.py tests/test_remote_backup.py
git commit -m "feat(backup): per-member engine + CLI (sync/tick/restore/verify/status by member)"
```

---

## Task 5: One-time migration `migrate_backup.py`

**Files:**
- Create: `.codewhale/skills/Agent_Runtime/migrate_backup.py`
- Create: `tests/test_migrate_backup.py`

**Interfaces:**
- Produces: `migrate(root: Path) -> dict` — adds Jim's `backup` block, shrinks `config.json`,
  moves the global manifest → `data/Jim/.backup_manifest.json`, leaves the global clock in place;
  idempotent; aborts if another member's dir holds files. Returns
  `{added_block, config_shrunk, manifest_moved, empty_ok}`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migrate_backup.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_migrate_backup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migrate_backup'`.

- [ ] **Step 3: Implement `migrate_backup.py`**

Create `.codewhale/skills/Agent_Runtime/migrate_backup.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_migrate_backup.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/migrate_backup.py tests/test_migrate_backup.py
git commit -m "feat(backup): one-time migration to per-member backup (idempotent, snapshot)"
```

---

## Task 6: Update `SKILL.md`

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/SKILL.md`

- [ ] **Step 1: Rewrite the relevant sections**

Update `.codewhale/skills/Remote_Backup/SKILL.md`:

- **工作方式**: backup set is per-member; each member's `backup` block in `members.json` lists
  `scopes` (rel-path prefixes; `config.json` recognized as an infra alias). One global debounce
  clock (`data/.backup_state.json`); per-member manifest + state under `data/<Member>/`.
- **CLI table**: add `[--member NAME]` to `backup-now`/`backup-status`/`backup-verify`
  (default = all enabled members) and document `backup-restore --member NAME [--force]
  [--prefix P] [--remote-root R] [--dir D]`.
- **用户开启备份**: per member — add a `backup` block; set `{cred_prefix}_CLIENT_ID/SECRET/
  REFRESH_TOKEN` (default prefix `GDRIVE`); authorize with
  `python backup_provider.py --auth [--prefix PREFIX]`.
- **新设备恢复 (bootstrap)**: document the chicken-and-egg — restore Jim first with
  `backup-restore --member "Jim Zheng" --prefix GDRIVE --remote-root FamilyAssistant`
  (recovers `members.json` + `config.json`), then restore other members normally.
- **配置**: `config.json backup` = `{enabled, debounce_seconds}` only; `include`/`remote_root`
  moved per-member into `members.json`.
- **迁移**: `python .codewhale/skills/Agent_Runtime/migrate_backup.py` (bot stopped).
- **边界**: keep existing (no two-way sync, no pre-upload encryption, no version history); add
  "per-member, each to their own provider/account; only Jim wired live, others local-only until
  they add a `backup` block + creds".

- [ ] **Step 2: Verify the doc matches reality**

Re-read the section against the shipped `cli.py` flags and `members.json` schema. Confirm every
command/flag named exists.

- [ ] **Step 3: Commit**

```bash
git add .codewhale/skills/Remote_Backup/SKILL.md
git commit -m "docs(backup): SKILL.md for per-member backup model + bootstrap/migration"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all green, including `TestDirtyHooks` (unchanged `mark_dirty`) and
`TestAgentIntegration` (command names unchanged).

- [ ] **Smoke-check the live CLI (read-only)**

Run: `python .codewhale/skills/Remote_Backup/cli.py backup-status`
Expected: prints the global clock line + one row per member with a `backup` block. No traceback.

- [ ] **Run the migration on real data (bot stopped), then confirm zero re-upload**

```bash
python .codewhale/skills/Agent_Runtime/migrate_backup.py
python .codewhale/skills/Remote_Backup/cli.py backup-now --member "Jim Zheng"
python .codewhale/skills/Remote_Backup/cli.py backup-verify --member "Jim Zheng"
```
Expected: `backup-now` reports everything skipped (0 uploaded), `backup-verify` reports consistent
— confirming Jim's existing Drive carried over with zero churn.

---

## Notes for the implementer

- **Why mark_dirty stays global**: a single write touches one cheap state file with no member
  enumeration, keeps the dirty-hook contract + tests intact, and the per-member sync still
  isolates failures via per-member manifest + `last_error`.
- **Prefix-boundary matching**: scopes match on a directory boundary (`rglob` does this
  naturally) so `data/Jim` never captures `data/Jimbo`.
- **No secrets in code**: provider reads only `os.environ`; never log token/secret values.
- **Task 4 is deliberately one task**: its five engine functions + the CLI share the `bk` fixture
  and the new helper surface; splitting them leaves the suite red at a task boundary. Commit after
  the test rewrite is green (Step 9), not mid-flip.
```