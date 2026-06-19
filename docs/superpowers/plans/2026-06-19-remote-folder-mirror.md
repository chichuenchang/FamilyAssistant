# Remote Drive Folder-Tree Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Google Drive backup browse as a mirror of the local tree (real nested folders) instead of one flat pile, and re-parent the existing flat files into that tree with zero byte re-upload.

**Architecture:** `appProperties.rel` stays the engine's source of truth for every lookup. Only `upload` *placement* changes (files land in a nested folder chain), `_find`/`list_remote` drop their direct-child `in parents` clause so nested files are found, and a one-time idempotent `reorganize()` re-parents the existing flat files via Drive metadata moves. The `backup_sync` engine and the provider contract are unchanged.

**Tech Stack:** Python 3 stdlib (`urllib`, `json`, `pathlib`), pytest, Google Drive REST v3.

## Global Constraints

- **Stdlib only.** Network goes through the module-level `_http` seam.
- **Credentials only via env** (`{cred_prefix}_*`); no literal secrets; nothing secret logged.
- **`appProperties.rel` is the source of truth** — ROOT-relative posix path; every file carries it; lookups key off it. The folder tree is cosmetic-but-real (for human browsing).
- **`drive.file` scope unchanged** — creating folders and moving files stay within the app's own files; no new permissions.
- **Provider contract unchanged** — `is_configured`, `upload`, `delete`, `list_remote`, `download` keep their signatures. `reorganize` is an OPTIONAL extra method (CLI feature-detects it).
- **`reorganize` is idempotent and zero-byte** — re-parenting only; a second run moves nothing.
- **Chinese prose comments, English identifiers** (match the file).
- Test seam: the `gdrive` fixture yields `(prov, fake, calls, tmp_path)` where `fake` is a fake `_http` whose `.responses` list is popped per call and `calls` records each call; `_files_resp(*files, next_token=None)` builds a Drive `files.list` response; `prov._folder_cache["id"]` is pre-set to `"FOLDER1"` (remote_root) and `prov._token` is stubbed.

---

## File Structure

- `.codewhale/skills/Remote_Backup/backup_provider.py` — **modify**: add `_path_cache` + `_child_folder` + `_ensure_folder_path`; nest `upload`; drop `in parents` from `_find`/`list_remote`; add `reorganize`.
- `.codewhale/skills/Remote_Backup/cli.py` — **modify**: add `backup-reorg --member NAME` (local-only).
- `.codewhale/skills/Remote_Backup/SKILL.md` — **modify**: document the folder-mirror layout + the one-time `backup-reorg` step.
- `tests/test_remote_backup.py` — **modify**: new provider tests; update the two `upload` tests; CLI reorg tests.

Run the suite: `python -m pytest tests/ -q`.

---

## Task 1: `_ensure_folder_path` + folder-path cache

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_provider.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Consumes: existing `_api_json`, `_q`, `_folder_id`, constants `API`, `_FOLDER_MIME`; the `gdrive` fixture.
- Produces:
  - `GoogleDriveProvider._path_cache: dict` (instance, set in `__init__`).
  - `_child_folder(parent_id: str, name: str) -> str` — id of the named child folder under `parent_id`, creating it if absent.
  - `_ensure_folder_path(remote_rel: str) -> str` — id of the leaf directory for `remote_rel` (`mkdir -p`); a dir-less rel returns the `remote_root` id.

- [ ] **Step 1: Write the failing tests**

Append to `class TestGdriveProvider` in `tests/test_remote_backup.py`:

```python
    def test_ensure_folder_path_creates_chain(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [
            _files_resp(),                  # data: query miss
            (200, b'{"id": "F_data"}'),     # data: create
            _files_resp(),                  # Jim: query miss
            (200, b'{"id": "F_Jim"}'),      # Jim: create
            _files_resp(),                  # notes: query miss
            (200, b'{"id": "F_notes"}'),    # notes: create
        ]
        leaf = prov._ensure_folder_path("data/Jim/notes/x.jpg")
        assert leaf == "F_notes"

    def test_ensure_folder_path_caches(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [_files_resp(), (200, b'{"id": "F_data"}')]
        assert prov._ensure_folder_path("data/a.txt") == "F_data"
        n = len(calls)
        assert prov._ensure_folder_path("data/b.txt") == "F_data"  # cached
        assert len(calls) == n                                     # no new HTTP

    def test_ensure_folder_path_dirless_is_root(self, gdrive):
        prov, fake, calls, _ = gdrive
        assert prov._ensure_folder_path("config.json") == "FOLDER1"
        assert calls == []                                         # no folder lookups

    def test_child_folder_reuses_existing(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [_files_resp({"id": "EXIST"})]            # query hit
        assert prov._child_folder("FOLDER1", "data") == "EXIST"
        assert len(calls) == 1                                     # no create call
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k "ensure_folder_path or child_folder" -v`
Expected: FAIL — `AttributeError: 'GoogleDriveProvider' object has no attribute '_ensure_folder_path'`.

- [ ] **Step 3: Implement the cache + helpers**

In `.codewhale/skills/Remote_Backup/backup_provider.py`:

(a) In `__init__`, add the path cache right after `self._folder_cache = {"id": None}`:

```python
        self._path_cache: dict = {}
```

(b) Add these two methods to the class (place them immediately before `def upload`):

```python
    def _child_folder(self, parent_id: str, name: str) -> str:
        """parent 下名为 name 的子文件夹 id，不存在则创建。"""
        query = (f"name = '{_q(name)}' and mimeType = '{_FOLDER_MIME}' "
                 f"and '{parent_id}' in parents and trashed = false")
        r = self._api_json("GET", f"{API}/files?" + urllib.parse.urlencode(
            {"q": query, "fields": "files(id)", "pageSize": 1}))
        files = r.get("files") or []
        if files:
            return files[0]["id"]
        created = self._api_json("POST", f"{API}/files",
                                 {"name": name, "mimeType": _FOLDER_MIME,
                                  "parents": [parent_id]})
        return created["id"]

    def _ensure_folder_path(self, remote_rel: str) -> str:
        """rel 的目录链在云端建好（mkdir -p），返回叶目录 id；无目录则 remote_root。

        路径段逐级缓存（path → id），同目录重复上传零额外查询。
        """
        parts = remote_rel.split("/")[:-1]            # 去掉文件名
        parent = self._folder_id()
        path = ""
        for part in parts:
            path = f"{path}/{part}" if path else part
            cached = self._path_cache.get(path)
            if cached:
                parent = cached
                continue
            parent = self._child_folder(parent, part)
            self._path_cache[path] = parent
        return parent
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k "ensure_folder_path or child_folder" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_provider.py tests/test_remote_backup.py
git commit -m "feat(backup): GoogleDriveProvider _ensure_folder_path (mkdir -p + cache)"
```

---

## Task 2: Drop `in parents` from `_find` and `list_remote`

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_provider.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Produces: `_find` and `list_remote` keyed purely off `appProperties.rel` (no parent constraint), so nested files (not direct children of `remote_root`) are found.

- [ ] **Step 1: Write the failing tests**

Append to `class TestGdriveProvider`:

```python
    def test_find_has_no_parent_constraint(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [_files_resp({"id": "NESTED"})]
        assert prov._find("data/Jim/notes/deep/x.jpg") == "NESTED"
        assert "parents" not in calls[-1]["url"]      # query no longer parent-scoped

    def test_list_remote_finds_nested_and_drops_parent(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [
            _files_resp({"id": "1", "size": "7",
                         "appProperties": {"rel": "data/Jim/notes/n.db"}}),
        ]
        out = prov.list_remote()
        assert out == {"data/Jim/notes/n.db": {"size": 7}}
        assert "parents" not in calls[-1]["url"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k "no_parent_constraint or finds_nested" -v`
Expected: FAIL — the current queries contain `'<folder>' in parents`, so `"parents"` IS in the URL.

- [ ] **Step 3: Update the queries**

In `.codewhale/skills/Remote_Backup/backup_provider.py`, replace the body of `_find`:

```python
    def _find(self, remote_rel: str) -> str | None:
        query = (f"appProperties has {{ key='rel' and value='{_q(remote_rel)}' }} "
                 f"and trashed = false")
        r = self._api_json("GET", f"{API}/files?" + urllib.parse.urlencode(
            {"q": query, "fields": "files(id)", "pageSize": 2}))
        files = r.get("files") or []
        return files[0]["id"] if files else None
```

And replace the query line inside `list_remote` — change the `params["q"]` from
`f"'{self._folder_id()}' in parents and trashed = false"` to:

```python
            params = {"q": f"mimeType != '{_FOLDER_MIME}' and trashed = false",
                      "fields": "nextPageToken, files(id, size, appProperties)",
                      "pageSize": 1000}
```

(Leave the rest of `list_remote` — pagination + the `rel` extraction loop — unchanged. `drive.file` scope already limits results to the app's own files; the `mimeType != folder` filter drops the folder entries the new layout creates.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -v`
Expected: PASS — the two new query tests pass and the existing `_find`/`list_remote`/`delete`/`download` tests stay green (they mock the HTTP layer and don't assert the removed clause).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_provider.py tests/test_remote_backup.py
git commit -m "feat(backup): _find/list_remote locate files tree-wide (drop in-parents clause)"
```

---

## Task 3: `upload` places files in the nested folder

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_provider.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Consumes: `_ensure_folder_path` (Task 1), `_find` (Task 2).
- Produces: on create, a file is parented to its leaf folder (not `remote_root`); a dir-less rel parents to `remote_root`; PATCH of an existing file does not re-parent. `appProperties.rel` still set.

- [ ] **Step 1: Update the two existing upload tests + add a dir-less test**

In `tests/test_remote_backup.py`, REPLACE `test_upload_new_file_multipart_create` with the version below (it now feeds the folder query/create round-trips), and ADD `test_upload_dirless_parents_to_root`. Leave `test_upload_existing_file_patches` unchanged (PATCH path does not nest).

```python
    def test_upload_new_file_multipart_create(self, gdrive):
        prov, fake, calls, tmp_path = gdrive
        f = tmp_path / "a.jpg"
        f.write_bytes(b"IMGBYTES")
        fake.responses = [
            _files_resp(),                       # _find: no existing
            _files_resp(), (200, b'{"id": "Fd"}'),   # folder: data
            _files_resp(), (200, b'{"id": "FF"}'),   # folder: Family
            _files_resp(), (200, b'{"id": "Fr"}'),   # folder: receipts
            _files_resp(), (200, b'{"id": "Fm"}'),   # folder: 2026-06
            (200, b'{"id": "NEW1"}'),            # multipart create
        ]
        prov.upload(f, "data/Family/receipts/2026-06/a.jpg")
        up = calls[-1]
        assert up["method"] == "POST"
        assert "uploadType=multipart" in up["url"]
        assert b"IMGBYTES" in up["data"]
        assert b'"rel": "data/Family/receipts/2026-06/a.jpg"' in up["data"]
        assert b'"parents": ["Fm"]' in up["data"]      # leaf folder, not remote_root

    def test_upload_dirless_parents_to_root(self, gdrive):
        prov, fake, calls, tmp_path = gdrive
        f = tmp_path / "config.json"
        f.write_bytes(b"{}")
        fake.responses = [_files_resp(), (200, b'{"id": "C1"}')]   # _find miss, create
        prov.upload(f, "config.json")
        up = calls[-1]
        assert up["method"] == "POST"
        assert b'"parents": ["FOLDER1"]' in up["data"]   # remote_root
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k upload -v`
Expected: FAIL — current `upload` parents new files to `_folder_id()` (`FOLDER1`), so the nested test's `"parents": ["Fm"]` assertion fails (and the response queue desyncs).

- [ ] **Step 3: Nest the create path**

In `.codewhale/skills/Remote_Backup/backup_provider.py`, in `upload`, change ONLY the create branch's parent. Replace:

```python
        if existing is None:
            meta["parents"] = [self._folder_id()]
            method, url = "POST", f"{UPLOAD_API}/files?uploadType=multipart"
```

with:

```python
        if existing is None:
            meta["parents"] = [self._ensure_folder_path(remote_rel)]
            method, url = "POST", f"{UPLOAD_API}/files?uploadType=multipart"
```

(The PATCH branch is unchanged — an existing file keeps its folder because its `rel`, hence target folder, has not changed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k upload -v`
Expected: PASS (3 upload tests). Then `python -m pytest tests/test_remote_backup.py -q` — full file green.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_provider.py tests/test_remote_backup.py
git commit -m "feat(backup): upload places files in nested folder mirroring rel"
```

---

## Task 4: `reorganize()` — one-time re-parenter

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_provider.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Consumes: `_ensure_folder_path`, `_api_json`, constants `API`, `_FOLDER_MIME`.
- Produces: `reorganize() -> dict` with keys `moved` (list of rels) and `skipped` (int). Moves each flat file into its `rel` folder via `files.update` (`addParents`/`removeParents`); already-nested files are skipped; idempotent.

- [ ] **Step 1: Write the failing tests**

Append to `class TestGdriveProvider`:

```python
    def test_reorganize_moves_flat_to_folders(self, gdrive):
        prov, fake, calls, _ = gdrive
        fake.responses = [
            _files_resp({"id": "X", "parents": ["FOLDER1"],
                         "appProperties": {"rel": "data/Jim/x.jpg"}}),  # list page
            _files_resp(), (200, b'{"id": "Fd"}'),   # folder: data
            _files_resp(), (200, b'{"id": "FJ"}'),   # folder: Jim
            (200, b'{"id": "X"}'),                    # PATCH move
        ]
        r = prov.reorganize()
        assert r["moved"] == ["data/Jim/x.jpg"]
        move = calls[-1]
        assert move["method"] == "PATCH"
        assert "addParents=FJ" in move["url"]
        assert "removeParents=FOLDER1" in move["url"]

    def test_reorganize_skips_already_nested(self, gdrive):
        prov, fake, calls, _ = gdrive
        prov._path_cache = {"data": "Fd", "data/Jim": "FJ"}   # leaf resolves with no HTTP
        fake.responses = [
            _files_resp({"id": "X", "parents": ["FJ"],
                         "appProperties": {"rel": "data/Jim/x.jpg"}}),
        ]
        r = prov.reorganize()
        assert r["moved"] == [] and r["skipped"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k reorganize -v`
Expected: FAIL — `AttributeError: ... has no attribute 'reorganize'`.

- [ ] **Step 3: Implement `reorganize`**

Add this method to `GoogleDriveProvider` (place it right after `download`):

```python
    def reorganize(self) -> dict:
        """把现有平铺文件按 rel 重新归入文件夹树（元数据移动，零字节重传）。幂等。

        遍历全部带 rel 的文件（drive.file 作用域即本应用文件）；当前父 ≠ 目标叶目录
        则用 files.update 改 parents（addParents/removeParents），已就位的跳过。
        """
        moved: list[str] = []
        skipped = 0
        page_token = None
        while True:
            params = {"q": f"mimeType != '{_FOLDER_MIME}' and trashed = false",
                      "fields": "nextPageToken, files(id, parents, appProperties)",
                      "pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            r = self._api_json("GET", f"{API}/files?" + urllib.parse.urlencode(params))
            for f in r.get("files", []):
                rel = (f.get("appProperties") or {}).get("rel")
                if not rel:
                    continue
                target = self._ensure_folder_path(rel)
                current = (f.get("parents") or [None])[0]
                if current == target:
                    skipped += 1
                    continue
                url = f"{API}/files/{f['id']}?" + urllib.parse.urlencode(
                    {"addParents": target, "removeParents": current or "",
                     "fields": "id"})
                self._api_json("PATCH", url)
                moved.append(rel)
            page_token = r.get("nextPageToken")
            if not page_token:
                break
        return {"moved": moved, "skipped": skipped}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py::TestGdriveProvider -k reorganize -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_provider.py tests/test_remote_backup.py
git commit -m "feat(backup): reorganize() re-parents flat files into the folder tree"
```

---

## Task 5: `backup-reorg --member` CLI command

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/cli.py`
- Test: `tests/test_remote_backup.py`

**Interfaces:**
- Consumes: `backup_sync._resolve`, `backup_sync._make_provider`; provider `reorganize`.
- Produces: `backup-reorg --member NAME` (local-only; not agent-whitelisted). Feature-detects `reorganize` (graceful skip if absent).

- [ ] **Step 1: Write the tests**

Add to `tests/test_remote_backup.py`. The first is in-process (imports `cli`, monkeypatches `backup_sync`); the second is the subprocess error path (uses the existing `_enabled_member_env`, `_run_cli`).

```python
def test_cmd_reorg_invokes_provider(monkeypatch, capsys):
    import cli
    class P:
        def is_configured(self):
            return True
        def reorganize(self):
            return {"moved": ["data/Jim/x.jpg"], "skipped": 2}
    monkeypatch.setattr(cli.backup_sync, "_resolve",
                        lambda m: {"provider": "google_drive", "name": m})
    monkeypatch.setattr(cli.backup_sync, "_make_provider", lambda p: P())

    class Args:
        member = "Jim Zheng"
    cli.cmd_reorg(Args())
    out = capsys.readouterr().out
    assert "data/Jim/x.jpg" in out
    assert "移动 1" in out and "已就位 2" in out


class TestReorgCli:
    def test_reorg_requires_member(self, tmp_path):
        r = _run_cli("backup-reorg", env_extra=_enabled_member_env(tmp_path))
        assert r.returncode == 1
        assert "member" in r.stderr.lower() or "成员" in r.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py -k "cmd_reorg or TestReorgCli" -v`
Expected: FAIL — `cli` has no `cmd_reorg`; `backup-reorg` is not a known subcommand.

- [ ] **Step 3: Add the command**

In `.codewhale/skills/Remote_Backup/cli.py`, add the handler (place it after `cmd_restore`):

```python
def cmd_reorg(args):
    if not args.member:
        raise ValueError("backup-reorg 需要 --member NAME。")
    pref = backup_sync._resolve(args.member)
    if pref is None:
        raise ValueError(f"成员 {args.member} 无 backup 配置。")
    prov = backup_sync._make_provider(pref)
    if not prov.is_configured():
        raise ValueError("provider 未配置，无法重组。")
    if not hasattr(prov, "reorganize"):
        print(f"provider {pref['provider']} 不支持重组（无需操作）。")
        return
    r = prov.reorganize()
    for rel in r["moved"]:
        print(f"⇄ {rel}")
    print(f"[{args.member}] 重组完成：移动 {len(r['moved'])}，已就位 {r['skipped']}。")
```

Register the subparser in `main()` (add after the `backup-restore` parser block):

```python
    pr = sub.add_parser("backup-reorg",
                        help="一次性：把云端平铺文件归入镜像文件夹树（仅本机）")
    pr.add_argument("--member", help="目标成员")
```

And add to the `dispatch` dict:

```python
                "backup-reorg": cmd_reorg,
```

(`backup-reorg` is local-only — do NOT add it to `wechat.allowed_commands` / the agent whitelist.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py -k "cmd_reorg or TestReorgCli" -v`
Expected: PASS (2 tests). Then `python -m pytest tests/ -q` — whole suite green.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/cli.py tests/test_remote_backup.py
git commit -m "feat(backup): backup-reorg CLI command (one-time remote folder reorg)"
```

---

## Task 6: Document the folder mirror in `SKILL.md`

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/SKILL.md`

- [ ] **Step 1: Add the layout note + command**

Update `.codewhale/skills/Remote_Backup/SKILL.md`:

- In the section describing the cloud layout (the line currently saying files are flat in
  `remote_root` with `appProperties.rel`), replace it with: the remote now mirrors the local
  tree as **nested folders** under `remote_root`; `appProperties.rel` is still stored on each
  file as the engine's source of truth (lookups/list/delete/restore key off it, so the mirror is
  robust even if files are hand-moved); `drive.file` scope is unchanged.
- In the CLI section, add a row/example for the one-time command:
  `python .codewhale/skills/Remote_Backup/cli.py backup-reorg --member "Jim Zheng"` — re-parents
  existing flat files into the folder tree (metadata move, zero re-upload); local-only, idempotent,
  not agent-callable.
- Add a one-line note under 用户开启备份 or 迁移: after upgrading to the folder-mirror version,
  run `backup-reorg --member NAME` once per member that already has remote data.

- [ ] **Step 2: Verify the doc matches reality**

Re-read against the shipped `cli.py`: confirm `backup-reorg --member` exists and is not in any
agent whitelist. Confirm the layout description matches `upload`'s nested placement.

- [ ] **Step 3: Commit**

```bash
git add .codewhale/skills/Remote_Backup/SKILL.md
git commit -m "docs(backup): document folder-tree mirror layout + backup-reorg"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all green (the prior 291 + the new provider/CLI tests).

- [ ] **Smoke-check the CLI registers the command**

Run: `python .codewhale/skills/Remote_Backup/cli.py backup-reorg 2>&1 | head -3`
Expected: exits non-zero with the "需要 --member" error (proves the subcommand is wired, no traceback).

- [ ] **One-time live step (manual, user-run when ready)**

After deploy, with creds set: `python .codewhale/skills/Remote_Backup/cli.py backup-reorg --member "Jim Zheng"` re-nests the existing flat files; a following `backup-verify --member "Jim Zheng"` stays consistent. (Not run by the implementer — needs real Drive creds.)

---

## Notes for the implementer

- **Why lookups drop `in parents`:** once files nest, they are no longer direct children of
  `remote_root`, so a `'<root>' in parents` query returns nothing. `appProperties.rel` is unique
  within the app's `drive.file`-scoped files, so it locates a file at any depth.
- **Zero re-upload:** `reorganize` only changes a file's `parents` (metadata); bytes never move.
  `upload`'s PATCH path likewise never re-parents — a changed file stays in its folder.
- **Idempotency:** `reorganize` compares each file's current parent to its target leaf and skips
  matches, so re-running is a no-op.
- **No secrets:** provider reads only `os.environ`; never log token/secret values.
