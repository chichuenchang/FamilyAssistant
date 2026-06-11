# Remote Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New `.codewhale/skills/Remote_Backup/` skill: real sync engine (manifest, dirty-flag, debounced mirror, restore) over a stub provider that the user's own agent implements privately; hooked into both CLIs and both transports.

**Architecture:** `backup_sync.py` holds all engine logic and is the only module callers import; `backup_provider.py` is the documented placeholder (every call unimplemented); `cli.py` exposes `backup-now/-status/-verify/-restore`. Write paths call `mark_dirty()` (instant, never raises); transport loops call `backup_tick()` which syncs after ~60s of quiet. Disabled by default in config.

**Tech Stack:** Python 3.10+ stdlib only (hashlib, sqlite3, json, tempfile), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-11-remote-backup-design.md`

**Delegation note:** Per user request, the executor should use `mcp__deepseek__delegate_to_deepseek` where sensible — recommended: independent review of `backup_sync.py` after Task 6 and of the hook edits after Task 9 (prompt: paste module + provider contract, ask for correctness review against the spec bullet list). Code in this plan is authoritative; deepseek output is advisory.

**Test-isolation hooks (deliberate, mirrors `DOC_KEEPER_DB`):** `backup_sync` reads env `BACKUP_STATE_DIR` to relocate its two state files so subprocess tests never touch `data/`. In-process tests monkeypatch module attributes (`ROOT`, `CFG`, `STATE_FILE`, `MANIFEST_FILE`, `provider`).

---

### Task 1: Config keys + gitignore

**Files:**
- Modify: `config.json`
- Modify: `.gitignore`

- [ ] **Step 1: Add backup section to config.json**

After the `"reminder_lead_days": 30,` line insert:

```json
  "backup": {
    "enabled": false,
    "debounce_seconds": 60,
    "include": ["data/ledger.db", "receipts", "documents", "config.json"],
    "remote_root": "FamilyAssistant"
  },
```

Extend `wechat.allowed_commands` (note: `backup-restore` deliberately absent — overwrites local data, local-only like `doc-remove` / `member-*`):

```json
      "doc-add", "doc-list", "doc-show", "doc-due", "doc-update", "doc-ack",
      "backup-now", "backup-status", "backup-verify"
```

- [ ] **Step 2: Ignore engine state files**

Append to `.gitignore` after the `data/.doc_reminder_state` line:

```
# 备份引擎状态（本机的镜像清单，不入库）
data/.backup_manifest.json
data/.backup_state.json
```

- [ ] **Step 3: Validate JSON**

Run: `python -c "import json; json.load(open('config.json', encoding='utf-8')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add config.json .gitignore
git commit -m "feat: remote backup config section and command whitelist"
```

---

### Task 2: backup_provider.py stub + conftest wiring

**Files:**
- Create: `.codewhale/skills/Remote_Backup/backup_provider.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_remote_backup.py` (new)

- [ ] **Step 1: Add Remote_Backup to test path**

In `tests/conftest.py`, after the `DOC_DIR` block, add:

```python
BACKUP_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Remote_Backup"
)
sys.path.insert(0, str(BACKUP_DIR))
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_remote_backup.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backup_provider'`

- [ ] **Step 4: Write backup_provider.py**

Create `.codewhale/skills/Remote_Backup/backup_provider.py`:

```python
"""
Remote Backup — 云盘 Provider 占位实现（用户私有部分）

这是整个 skill 里**唯一**留给用户自己实现的文件。云盘选择和凭据高度私密，
由用户让自己的编码 Agent 按下面的契约实现（Google Drive / Dropbox / OneDrive /
S3 / WebDAV 均可），替换本文件内容。实现指南见同目录 SKILL.md。

契约（backup_sync 只按此调用，不关心云盘细节）：

- 所有 remote_rel 为相对路径、正斜杠（如 "receipts/2026-06/x.jpg"）。
  云端实际位置 = config.json backup.remote_root + "/" + remote_rel，
  remote_root 由本模块自己读取并拼接。
- 凭据一律走环境变量，不写进代码、不打日志。
- 上传必须覆盖同名远端文件（镜像语义）。
- list_remote() 返回 {remote_rel: {"size": int}}，列出 remote_root 下全部文件。
- 任何网络/认证错误：抛普通 Exception（引擎会记录并在下一轮重试）。

未实现状态：is_configured() 返回 False，其余抛 NotImplementedError——
引擎据此优雅跳过，整个 skill 处于"已接线、未启用"的安全状态。
"""

from pathlib import Path

_MSG = "backup_provider 未实现 — 见 .codewhale/skills/Remote_Backup/SKILL.md 设置指南"


def is_configured() -> bool:
    """凭据就绪且可用时返回 True。未实现时必须返回 False。"""
    return False


def upload(local_path: Path, remote_rel: str) -> None:
    """把本地文件上传到云端 remote_root/remote_rel，覆盖已存在的。"""
    raise NotImplementedError(_MSG)


def delete(remote_rel: str) -> None:
    """删除云端 remote_root/remote_rel。文件不存在视为成功。"""
    raise NotImplementedError(_MSG)


def list_remote() -> dict:
    """列出 remote_root 下全部文件：{remote_rel: {"size": int}}。"""
    raise NotImplementedError(_MSG)


def download(remote_rel: str, local_path: Path) -> None:
    """把云端 remote_root/remote_rel 下载到本地 local_path（覆盖）。"""
    raise NotImplementedError(_MSG)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: 1 PASSED

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_provider.py tests/conftest.py tests/test_remote_backup.py
git commit -m "feat: remote backup provider stub with documented contract"
```

---

### Task 3: backup_sync.py — state, mark_dirty, file walk, hashing

**Files:**
- Create: `.codewhale/skills/Remote_Backup/backup_sync.py`
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_remote_backup.py`:

```python
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
        monkeypatch_include = backup_sync.CFG["include"] + ["data"]
        backup_sync.CFG["include"] = monkeypatch_include
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backup_sync'`

- [ ] **Step 3: Write backup_sync.py (state + walk parts; sync/tick/restore come in Tasks 4–6 but write the whole module now — later tasks only add tests)**

Create `.codewhale/skills/Remote_Backup/backup_sync.py`:

```python
"""
Remote Backup — 同步引擎（真实实现）

本模块是调用方唯一入口：脏标记、防抖触发、镜像同步、恢复、校验、状态。
云盘操作全部走同目录 backup_provider（占位，由用户私有实现）。

调用契约：
    mark_dirty()    任何用户数据写入后调用。亚毫秒、零网络、永不抛异常。
    backup_tick()   传输层轮询里反复调用。enabled + provider 就绪 + 脏 +
                    距最后写入 >= debounce_seconds 时跑一次 sync()。
    sync()/restore()/verify()/status()  CLI 与 Agent 使用。

状态文件（均不入备份、不入 git）：
    data/.backup_manifest.json  引擎认为云端已有的内容 {rel: {sha256,size,uploaded_at}}
    data/.backup_state.json     {dirty_since, last_write, last_sync, last_error}
测试钩子：环境变量 BACKUP_STATE_DIR 重定位这两个文件（仅测试用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import backup_provider as provider

_FALLBACK_CFG = {
    "enabled": False,
    "debounce_seconds": 60,
    "include": ["data/ledger.db", "receipts", "documents", "config.json"],
    "remote_root": "FamilyAssistant",
}


def _load_cfg() -> dict:
    try:
        raw = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cfg = raw.get("backup")
        if isinstance(cfg, dict):
            return {**_FALLBACK_CFG, **cfg}
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


CFG = _load_cfg()

_STATE_DIR = Path(os.environ.get("BACKUP_STATE_DIR") or (ROOT / "data"))
MANIFEST_FILE = _STATE_DIR / ".backup_manifest.json"
STATE_FILE = _STATE_DIR / ".backup_state.json"

# 永不进备份的路径（即使用户把 data 整个加进 include）
_HARD_EXCLUDE_NAMES = {".telegram_offset", ".doc_reminder_state",
                       ".backup_manifest.json", ".backup_state.json"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _excluded(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name in _HARD_EXCLUDE_NAMES:
        return True
    if "creds" in name:
        return True
    return False


def mark_dirty() -> None:
    """用户数据写入后调用。绝不抛异常——备份问题不能影响写入本身。"""
    try:
        st = _load_json(STATE_FILE)
        now = _now_iso()
        if not st.get("dirty_since"):
            st["dirty_since"] = now
        st["last_write"] = now
        _save_json(STATE_FILE, st)
    except Exception:
        pass


def _iter_local_files() -> dict[str, Path]:
    """include 集合展开为 {rel(posix): 绝对路径}，应用硬排除。"""
    out: dict[str, Path] = {}
    for entry in CFG["include"]:
        p = ROOT / entry
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_sqlite(path: Path) -> Path:
    """SQLite 一致性快照到临时文件（备份 API，连接打开中也安全）。调用方负责删除。"""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(str(path))
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return Path(tmp)


def _provider_ready() -> bool:
    try:
        return bool(provider.is_configured())
    except Exception:
        return False


def sync() -> dict:
    """镜像一轮：上传 新/变更，删除 本地已不存在的远端项。

    清单逐项落盘——中途崩溃只补剩余部分。
    返回 {"uploaded": [...], "deleted": [...], "skipped": int, "errors": [...]}。
    """
    started_last_write = _load_json(STATE_FILE).get("last_write")
    manifest = _load_json(MANIFEST_FILE)
    local = _iter_local_files()
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
                provider.upload(src, rel)
                manifest[rel] = {"sha256": digest, "size": src.stat().st_size,
                                 "uploaded_at": _now_iso()}
                _save_json(MANIFEST_FILE, manifest)
                uploaded.append(rel)
        except NotImplementedError:
            raise
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
            provider.delete(rel)
            del manifest[rel]
            _save_json(MANIFEST_FILE, manifest)
            deleted.append(rel)
        except NotImplementedError:
            raise
        except Exception as e:
            errors.append(f"{rel}: {e}")

    st = _load_json(STATE_FILE)
    st["last_sync"] = _now_iso()
    if errors:
        st["last_error"] = "; ".join(errors[:5])
    else:
        st["last_error"] = None
        # 同步期间没有新写入才算干净（有新写入则保持脏，下一轮再跑）
        if st.get("last_write") == started_last_write:
            st["dirty_since"] = None
    _save_json(STATE_FILE, st)
    return {"uploaded": uploaded, "deleted": deleted,
            "skipped": skipped, "errors": errors}


def backup_tick(now: datetime | None = None) -> bool:
    """传输层轮询调用。条件满足则同步一轮，返回是否跑了 sync。永不抛异常。"""
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
        if not _provider_ready():
            return False
        sync()
        return True
    except Exception as e:
        try:
            st = _load_json(STATE_FILE)
            st["last_error"] = str(e)
            _save_json(STATE_FILE, st)
        except Exception:
            pass
        return False


def restore(force: bool = False) -> dict:
    """新设备引导：从云端拉回全部文件并重建清单。

    本地已有用户数据时拒绝（除非 force）——多半是跑错机器了。
    """
    if not _provider_ready():
        raise ValueError("backup_provider 未实现/未配置，无法恢复。见 SKILL.md 设置指南。")
    if not force:
        existing = [rel for rel in _iter_local_files()
                    if rel != "config.json"]
        if existing:
            raise ValueError(
                f"本地已有 {len(existing)} 个用户数据文件（如 {existing[0]}）。"
                f"确认要覆盖请加 --force。")
    remote = provider.list_remote()
    manifest: dict = {}
    downloaded: list[str] = []
    for rel in sorted(remote):
        if _excluded(rel):
            continue
        target = ROOT / rel
        provider.download(rel, target)
        manifest[rel] = {"sha256": _sha256(target), "size": target.stat().st_size,
                         "uploaded_at": _now_iso()}
        downloaded.append(rel)
    _save_json(MANIFEST_FILE, manifest)
    st = _load_json(STATE_FILE)
    st["last_sync"] = _now_iso()
    st["dirty_since"] = None
    st["last_error"] = None
    _save_json(STATE_FILE, st)
    return {"downloaded": downloaded}


def verify() -> dict:
    """清单 vs 云端列表：{"ok": [...], "missing_remote": [...], "extra_remote": [...],
    "size_mismatch": [...]}。"""
    if not _provider_ready():
        raise ValueError("backup_provider 未实现/未配置，无法校验。")
    manifest = _load_json(MANIFEST_FILE)
    remote = provider.list_remote()
    ok, missing, mismatch = [], [], []
    for rel, meta in manifest.items():
        if rel not in remote:
            missing.append(rel)
        elif remote[rel].get("size") not in (None, meta.get("size")):
            mismatch.append(rel)
        else:
            ok.append(rel)
    extra = [r for r in remote if r not in manifest]
    return {"ok": ok, "missing_remote": missing,
            "extra_remote": extra, "size_mismatch": mismatch}


def status() -> dict:
    st = _load_json(STATE_FILE)
    return {
        "enabled": bool(CFG["enabled"]),
        "configured": _provider_ready(),
        "dirty_since": st.get("dirty_since"),
        "last_write": st.get("last_write"),
        "last_sync": st.get("last_sync"),
        "last_error": st.get("last_error"),
        "files_tracked": len(_load_json(MANIFEST_FILE)),
    }
```

- [ ] **Step 4: Run tests to verify the Task 3 tests pass**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/backup_sync.py tests/test_remote_backup.py
git commit -m "feat: backup sync engine - state, dirty flag, file walk, hashing"
```

---

### Task 4: sync() behavior tests

(Engine written in Task 3; these tests lock the mirror semantics.)

**Files:**
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_remote_backup.py`:

```python
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
        import sqlite3 as sq
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
            import tempfile, os
            fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
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
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: all PASSED. If a sync test fails, fix `backup_sync.py` — the tests encode the spec.

- [ ] **Step 3: Commit**

```bash
git add tests/test_remote_backup.py
git commit -m "test: mirror sync semantics - diff upload, delete propagation, sqlite snapshot"
```

---

### Task 5: backup_tick() behavior tests

**Files:**
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_remote_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: all PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/test_remote_backup.py
git commit -m "test: debounced backup tick gating and failure isolation"
```

---

### Task 6: restore / verify / status tests

**Files:**
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_remote_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests + full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASSED (new + existing 83).

- [ ] **Step 3 (delegation): deepseek review of backup_sync.py**

Use `mcp__deepseek__delegate_to_deepseek`: paste `backup_sync.py` + the provider contract docstring, ask "review for correctness bugs against these requirements: debounce gating, incremental manifest persistence, dirty-flag semantics on failure and concurrent writes, sqlite snapshot safety, hard excludes". Apply any real findings; ignore style nits.

- [ ] **Step 4: Commit**

```bash
git add tests/test_remote_backup.py
git commit -m "test: restore bootstrap, verify report, status fields"
```

---

### Task 7: cli.py

**Files:**
- Create: `.codewhale/skills/Remote_Backup/cli.py`
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the failing tests (subprocess, real stub provider)**

Append to `tests/test_remote_backup.py`:

```python
import subprocess
import sys as _sys

_CLI = str(Path(__file__).resolve().parent.parent
           / ".codewhale" / "skills" / "Remote_Backup" / "cli.py")


def _run_cli(*args, env_extra=None):
    import os as _os
    env = dict(_os.environ)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestCli -v`
Expected: FAIL — CLI file does not exist (returncode 2, asserts fail).

- [ ] **Step 3: Write cli.py**

Create `.codewhale/skills/Remote_Backup/cli.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_remote_backup.py -v`
Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Remote_Backup/cli.py tests/test_remote_backup.py
git commit -m "feat: remote backup CLI - now/status/verify/restore"
```

---

### Task 8: mark_dirty hooks in both CLIs

**Files:**
- Modify: `.codewhale/skills/Expense_Tracker/cli.py` (end of `main()`)
- Modify: `.codewhale/skills/Document_Keeper/cli.py` (end of `main()`)
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_remote_backup.py`:

```python
_EXPENSE_CLI = str(Path(__file__).resolve().parent.parent
                   / ".codewhale" / "skills" / "Expense_Tracker" / "cli.py")
_DOC_CLI = str(Path(__file__).resolve().parent.parent
               / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")


class TestDirtyHooks:
    def test_doc_add_marks_dirty(self, tmp_path):
        import json as _json
        env = {"BACKUP_STATE_DIR": str(tmp_path),
               "DOC_KEEPER_DB": str(tmp_path / "d.db")}
        r = subprocess.run(
            [_sys.executable, _DOC_CLI, "doc-add", "--type", "lease",
             "--title", "t"],
            capture_output=True, text=True, encoding="utf-8",
            env={**__import__("os").environ, **env})
        assert r.returncode == 0, r.stderr
        st = _json.loads((tmp_path / ".backup_state.json").read_text(encoding="utf-8"))
        assert st["dirty_since"]

    def test_doc_list_does_not_mark_dirty(self, tmp_path):
        env = {"BACKUP_STATE_DIR": str(tmp_path),
               "DOC_KEEPER_DB": str(tmp_path / "d.db")}
        subprocess.run(
            [_sys.executable, _DOC_CLI, "doc-list"],
            capture_output=True, text=True, encoding="utf-8",
            env={**__import__("os").environ, **env})
        assert not (tmp_path / ".backup_state.json").exists()
```

Note: the Expense_Tracker CLI has no test db override, so its hook is verified by code review + the shared helper being identical; do not write a subprocess test that mutates the real ledger.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestDirtyHooks -v`
Expected: FAIL — state file never created.

- [ ] **Step 3: Hook Document_Keeper/cli.py**

In `.codewhale/skills/Document_Keeper/cli.py`, add at module level (after the `_DB_OVERRIDE = ...` line):

```python
# 备份脏标记：写入类命令成功后调用（Remote_Backup skill；失败静默，绝不影响写入）
_BACKUP_WRITE_COMMANDS = {"doc-add", "doc-update", "doc-ack", "doc-remove"}


def _mark_backup_dirty() -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass
```

And in `main()`, replace the dispatch block

```python
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
```

with:

```python
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    if args.command in _BACKUP_WRITE_COMMANDS:
        _mark_backup_dirty()
```

- [ ] **Step 4: Hook Expense_Tracker/cli.py (same pattern)**

In `.codewhale/skills/Expense_Tracker/cli.py`, add at module level (after the `import members as members_registry` line):

```python
# 备份脏标记：写入类命令成功后调用（Remote_Backup skill；失败静默，绝不影响写入）
_BACKUP_WRITE_COMMANDS = {"add", "delete", "deposit-add", "transfer-add",
                          "tax-add", "fx-set", "member-add", "member-remove"}


def _mark_backup_dirty() -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass
```

And in its `main()`, replace

```python
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
```

with:

```python
    try:
        dispatch[args.command](args)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    if args.command in _BACKUP_WRITE_COMMANDS:
        _mark_backup_dirty()
```

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -v`
Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Document_Keeper/cli.py .codewhale/skills/Expense_Tracker/cli.py tests/test_remote_backup.py
git commit -m "feat: write commands mark backup dirty in both CLIs"
```

---

### Task 9: transport hooks (tick + image mark_dirty)

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py`
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py`

- [ ] **Step 1: Telegram**

After the existing Document_Keeper import block

```python
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Document_Keeper"))
from reminder import check_and_push as _doc_reminder_check
```

add:

```python
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Remote_Backup"))
from backup_sync import mark_dirty as _backup_mark_dirty, backup_tick as _backup_tick
```

In `download_photo()`, after the successful `dest.write_bytes(...)` line (inside the `try`, before `return dest`), add:

```python
        _backup_mark_dirty()
```

In `run()`, right after the doc-reminder check block, add:

```python
        # 用户数据备份：脏 + 静默期满则镜像一轮（backup_sync 内部把关，永不抛）
        _backup_tick()
```

- [ ] **Step 2: WeChat**

After its Document_Keeper import block add the same two-line import (identical to Telegram's).

In `handle_image()`, after the `msg.save(str(img_path))` line, add:

```python
            _backup_mark_dirty()
```

In the `_reminder_loop()` background thread, after the `_doc_reminder_check(...)` try/except block (inside the `while True:`), add:

```python
            _backup_tick()
```

- [ ] **Step 3: Import smoke check + full suite**

Run: `python -c "import sys; sys.path.insert(0, '.codewhale/skills/Agent_Runtime'); import telegram_bot, wechat_ilink; print('ok')"`
Expected: `ok`

Run: `python -m pytest tests/ -q`
Expected: all PASSED.

- [ ] **Step 4 (delegation): deepseek review of all hook edits**

Use `mcp__deepseek__delegate_to_deepseek`: paste the diffs of both CLIs and both transports, ask "verify no hook can raise into message handling or CLI writes, and dirty marking covers every user-data write path". Fix real findings.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Agent_Runtime/wechat_ilink.py
git commit -m "feat: transports mark backup dirty on image save and run debounced tick"
```

---

### Task 10: agent_core integration

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_remote_backup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_remote_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_remote_backup.py::TestAgentIntegration -v`
Expected: FAIL — routing and tools missing.

- [ ] **Step 3: Modify agent_core.py**

3a. Replace the `_cli_path` block:

```python
# doc-* 命令属于 Document_Keeper skill，其余走 Expense_Tracker
_DOC_COMMANDS = {"doc-add", "doc-list", "doc-show", "doc-due",
                 "doc-update", "doc-ack", "doc-remove"}


def _cli_path(cmd: str) -> Path:
    """子命令 → 所属 skill 的 CLI 路径。"""
    skill = "Document_Keeper" if cmd in _DOC_COMMANDS else "Expense_Tracker"
    return ROOT / ".codewhale" / "skills" / skill / "cli.py"
```

with:

```python
# 子命令 → 所属 skill（未列出的归 Expense_Tracker）
_DOC_COMMANDS = {"doc-add", "doc-list", "doc-show", "doc-due",
                 "doc-update", "doc-ack", "doc-remove"}
_BACKUP_COMMANDS = {"backup-now", "backup-status", "backup-verify", "backup-restore"}


def _cli_path(cmd: str) -> Path:
    """子命令 → 所属 skill 的 CLI 路径。"""
    if cmd in _DOC_COMMANDS:
        skill = "Document_Keeper"
    elif cmd in _BACKUP_COMMANDS:
        skill = "Remote_Backup"
    else:
        skill = "Expense_Tracker"
    return ROOT / ".codewhale" / "skills" / skill / "cli.py"
```

3b. After the `def _tool_ack_document...` line, add:

```python
def _tool_backup_now(args): return _run_cli("backup-now", args)
def _tool_backup_status(args): return _run_cli("backup-status", args)
def _tool_backup_verify(args): return _run_cli("backup-verify", args)
```

3c. Extend `_TOOL_MAP` with:

```python
    "backup_now": _tool_backup_now,
    "backup_status": _tool_backup_status,
    "backup_verify": _tool_backup_verify,
```

3d. Append to `TOOL_SCHEMAS` (before the closing `]`):

```python
    _fn("backup_now", "立即把用户数据镜像到云盘（需用户已配置 backup provider）", {}),
    _fn("backup_status", "查看云盘备份状态（是否启用/已配置/待同步/上次同步/错误）", {}),
    _fn("backup_verify", "校验云端镜像与本地清单是否一致", {}),
```

3e. In `_build_system_prompt`, after the `## 文档管理…` section, insert:

```python
## 数据备份（可选功能）
- 用户问"备份了吗""上次备份什么时候"→ backup_status
- 用户说"立刻备份""把数据同步到云盘"→ backup_now
- 用户问"云端和本地一致吗"→ backup_verify
- backup_status 显示未启用/未实现时：告知备份是可选功能，需要在电脑上让编码
  Agent 按 Remote_Backup/SKILL.md 实现 provider 并启用；不要反复推销
- 数据恢复（backup-restore）只能在电脑上手动执行，你调不到
```

(plain text inside the f-string; no `{}` interpolation in this section).

- [ ] **Step 4: Run full suite**

Run: `python -m pytest tests/ -q`
Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_remote_backup.py
git commit -m "feat: agent backup tools, routing, prompt guidance"
```

---

### Task 11: Documentation — SKILL.md (lean), FamilyAssistant.md, README

**Files:**
- Create: `.codewhale/skills/Remote_Backup/SKILL.md`
- Modify: `FamilyAssistant.md`
- Modify: `README.md`

- [ ] **Step 1: Write SKILL.md (lean — this skill is a tool invoked by agents)**

Create `.codewhale/skills/Remote_Backup/SKILL.md`:

```markdown
# Remote Backup

> 用户数据云盘镜像。git 管代码，本 skill 管用户数据（账本/票据/文档/配置）。
> 可选功能，默认关闭。本 skill 是被 Agent 调用的工具，自己不做决策。

## 代码位置

```
.codewhale/skills/Remote_Backup/
├── SKILL.md            ← 本文件
├── backup_sync.py      ← 同步引擎（真实实现）：清单、脏标记、防抖、镜像、恢复
├── backup_provider.py  ← 云盘占位（用户私有实现，契约见文件头注释）
└── cli.py              ← backup-now / backup-status / backup-verify / backup-restore
```

## 工作方式

- 写入路径调 `backup_sync.mark_dirty()`（已接好：两个 CLI 的写命令 + 传输层存图）。
- 传输层轮询调 `backup_sync.backup_tick()`：启用 + provider 就绪 + 脏 + 距最后写入
  ≥ `debounce_seconds` → 跑一轮镜像。失败自动下轮重试，本地永远是事实源。
- 镜像 = 哈希比对增量上传 + 本地删除同步删远端。SQLite 用一致性快照上传。
- 备份集 = config.json `backup.include`；凭据类文件硬排除，永不上传。

## CLI

| 命令 | 行为 | Agent 可调 |
|------|------|-----------|
| `backup-now` | 立即同步（忽略防抖） | ✅ |
| `backup-status` | 状态：启用/配置/待同步/上次同步/错误 | ✅ |
| `backup-verify` | 清单 vs 云端一致性 | ✅ |
| `backup-restore [--force]` | 新设备从云端拉回全部数据 | ❌ 仅本机 |

```bash
python .codewhale/skills/Remote_Backup/cli.py backup-status
python .codewhale/skills/Remote_Backup/cli.py backup-now
python .codewhale/skills/Remote_Backup/cli.py backup-restore          # 新设备
```

## 用户开启备份（设置清单，给编码 Agent 执行）

1. 读 `backup_provider.py` 文件头契约（5 个函数）。
2. 按用户选的云盘（Google Drive / Dropbox / OneDrive / S3 / WebDAV…）实现这 5 个
   函数，替换文件内容。凭据走环境变量，不进代码、不进日志。
3. `config.json` 设 `backup.enabled: true`。
4. `backup-now` 做首次全量上传，`backup-verify` 确认一致。
5. 新设备恢复：克隆代码库 → 实现/复制 provider → `backup-restore`。

## 配置（config.json `backup` 段）

`enabled`（默认 false）/ `debounce_seconds`（60）/ `include`（备份集）/
`remote_root`（云端根目录名）。改后重启进程生效。

## 边界

- ❌ 双向同步/冲突解决（单向 本地→云端；恢复是显式手动操作）
- ❌ 上传前加密（依赖云盘自身的静态加密；未来可加）
- ❌ 版本历史（用云盘自带的）
```

- [ ] **Step 2: FamilyAssistant.md**

Skills table, add row after Document Keeper:

```markdown
| **Remote Backup** | 用户数据云盘镜像（可选，provider 由用户私有实现） | [SKILL.md](.codewhale/skills/Remote_Backup/SKILL.md) | 备份、同步、云盘、恢复数据 |
```

Loading strategy, add:

```markdown
- 用户意图涉及备份/恢复/云盘同步 → 加载 Remote Backup
```

Config table, add row:

```markdown
| `backup`（enabled/debounce/include/remote_root） | `Remote_Backup/backup_sync.py`（CFG，读一次） |
```

Key files, add:

```markdown
- `.codewhale/skills/Remote_Backup/` — 用户数据云盘镜像 skill（backup_provider.py 留给用户私有实现）
```

- [ ] **Step 3: README.md**

Tree: after the Document_Keeper block add:

```
│       ├── Remote_Backup/    ← 用户数据云盘镜像（可选）
│       │   ├── SKILL.md
│       │   ├── backup_sync.py    ← 同步引擎
│       │   ├── backup_provider.py← 云盘占位（用户自己实现）
│       │   └── cli.py            ← 备份 CLI 入口
```

After the documents/ tree line's section, no change. In 手机端 section after the reminder sentence add:

```markdown
（可选）配置云盘备份后，所有数据自动镜像到你自己的网盘；换电脑 `backup-restore` 一键恢复。
```

- [ ] **Step 4: Final full suite + commit**

Run: `python -m pytest tests/ -q`
Expected: all PASSED.

```bash
git add .codewhale/skills/Remote_Backup/SKILL.md FamilyAssistant.md README.md
git commit -m "docs: remote backup skill doc, tree entry, loading strategy"
```

---

## Self-Review (completed)

- **Spec coverage:** layout §1 → Tasks 2/3/7; config §2 → Task 1; engine §3 → Tasks 3–6; provider contract §4 → Task 2; hooks §5 → Tasks 8/9; CLI §6 → Task 7; agent behavior §7 → Tasks 10/11; error handling §8 → tests in Tasks 4–7; testing §9 → Tasks 2–8/10.
- **Placeholder scan:** the only intentional placeholder is `backup_provider.py` — that is the feature, documented as such. All other steps carry full code.
- **Type consistency:** `provider` interface names match between stub, fake (tests), and engine call sites (`is_configured/upload/delete/list_remote/download`); CLI command names match whitelist, `_BACKUP_COMMANDS`, and tool executors.
```
