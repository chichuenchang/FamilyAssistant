# Documents DB Split + Family Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the `documents` table from `data/Family/ledger.db` into its own family-shared `data/Family/documents.db`, and add a shared `profiles` table (member personal facts) with agent tools + auto-injection.

**Architecture:** `doc_models.DB_PATH` is the single choke point for the documents DB location — repoint it to a new `paths.family_documents_db()`. Idempotent auto-migration copies legacy rows out of ledger.db on first default-path connect. Profiles live in the same documents.db, exposed via Document_Keeper CLI subcommands, wired into agent_core as write tools with reads served by system-prompt injection.

**Tech Stack:** Python stdlib + SQLite (WAL). pytest.

**Spec:** `docs/superpowers/specs/2026-07-10-documents-db-split-design.md`

## Global Constraints

- Core stays stdlib-only; user-facing CLI output Chinese; errors `[错误] ` + exit 1.
- Privacy rule: only notes/tasks/schedule are member-private; documents + profiles family-shared.
- Profile target flag is `--member-name` (never `--member` — avoids agent_core sender-identity injection collision).
- `member` value in profiles = registered member display name or literal `Family`.
- Test hooks: `DOC_KEEPER_DB` env overrides doc CLI DB (skips migration AND registry validation); explicit `db_path=` in doc_db skips migration.
- Injection functions must never throw (log + return "" — same as `_notes_context`).
- Run `python -m pytest tests/ -q` before each commit; commit per task.

---

### Task 1: `paths.family_documents_db`

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/paths.py` (after `family_ledger`, ~line 66; docstring layout list ~line 10)
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Produces: `family_documents_db() -> Path` — `data/Family/documents.db` (no mkdir — doc_db.get_db already makedirs parent).

- [ ] **Step 1: Failing test** — append to `tests/test_paths.py`:

```python
def test_family_documents_db(env):
    assert paths.family_documents_db().as_posix().endswith("data/Family/documents.db")
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_paths.py -q` → new test FAILS (`AttributeError`)
- [ ] **Step 3: Implement** — in `paths.py` after `family_ledger()`:

```python
def family_documents_db() -> Path:
    """家庭文档库（documents + profiles 表，家庭共享）。"""
    return family_dir() / "documents.db"
```

And in the module docstring layout list, change

```
    data/Family/ledger.db                   家庭账本（收支/定期/划转/报税/汇率/文档）
```

to

```
    data/Family/ledger.db                   家庭账本（收支/定期/划转/报税/汇率，纯财务）
    data/Family/documents.db                家庭文档库（documents + profiles，家庭共享）
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_paths.py -q` → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(paths): family documents db location"`

---

### Task 2: Repoint `DB_PATH` + profiles DDL

**Files:**
- Modify: `.codewhale/skills/Document_Keeper/doc_models.py` (~line 48 `DB_PATH`, and `SCHEMA`)
- Test: `tests/test_profiles.py` (new — first test only)

**Interfaces:**
- Produces: `doc_models.DB_PATH == paths.family_documents_db()`; `SCHEMA` creates `profiles` table.

- [ ] **Step 1: Failing test** — create `tests/test_profiles.py`:

```python
# tests/test_profiles.py — 家庭成员资料（profiles 表，documents.db）。
import sqlite3

import pytest

import doc_db
import doc_models


def test_db_path_is_documents_db():
    assert str(doc_models.DB_PATH).replace("\\", "/").endswith("data/Family/documents.db")


def test_schema_creates_profiles_table(tmp_path):
    p = str(tmp_path / "documents.db")
    doc_db.init_db(p)
    conn = sqlite3.connect(p)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)")]
    assert cols == ["id", "member", "field", "value", "updated_at"]
    conn.close()
```

Note: conftest.py already puts `Document_Keeper` on sys.path (`DOC_DIR`).

- [ ] **Step 2: Run** — `python -m pytest tests/test_profiles.py -q` → FAIL
- [ ] **Step 3: Implement** — in `doc_models.py`:

Change `DB_PATH = _paths.family_ledger()` (comment included) to:

```python
DB_PATH = _paths.family_documents_db()             # data/Family/documents.db
```

Append to the `SCHEMA` string (inside it, after the documents indexes):

```sql
CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member     TEXT NOT NULL,
    field      TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(member, field)
);
CREATE INDEX IF NOT EXISTS idx_profiles_member ON profiles(member);
```

Update `doc_models.py` header comment and `doc_db.py` module docstring line
`表建在家庭账本 data/Family/ledger.db（...）` → `表建在家庭文档库 data/Family/documents.db（DB_PATH 经 doc_models → paths.family_documents_db()）。`

- [ ] **Step 4: Run** — `python -m pytest tests/test_profiles.py tests/test_document_keeper.py -q` → PASS (doc tests use explicit db_path, unaffected)
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(docs): repoint documents db, add profiles schema"`

---

### Task 3: Auto-migration from ledger.db

**Files:**
- Modify: `.codewhale/skills/Document_Keeper/doc_db.py` (`get_db`, ~line 49)
- Test: `tests/test_doc_migration.py` (new)

**Interfaces:**
- Consumes: `doc_models.DB_PATH`, `_paths.family_ledger()`.
- Produces: `_migrate_from_ledger(conn, ledger_path) -> int` (rows copied; internal), triggered inside `get_db` only when `db_path is None`.

- [ ] **Step 1: Failing tests** — `tests/test_doc_migration.py`:

```python
# tests/test_doc_migration.py — documents 表从 ledger.db 搬家到 documents.db。
import sqlite3

import pytest

import doc_db
import doc_models
import paths


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    (tmp_path / "data" / "Family").mkdir(parents=True)
    # DB_PATH 在 import 时算好 —— 迁移逻辑必须实时解析路径，这里同步补丁掉模块常量
    monkeypatch.setattr(doc_models, "DB_PATH", paths.family_documents_db())
    monkeypatch.setattr(doc_db, "DB_PATH", paths.family_documents_db())
    return tmp_path / "data"


def _make_legacy_ledger(data_root, rows=2):
    ledger = data_root / "Family" / "ledger.db"
    conn = sqlite3.connect(ledger)
    conn.executescript(doc_models.SCHEMA)  # 造出旧世界：documents 表在账本里
    for i in range(rows):
        conn.execute(
            "INSERT INTO documents (doc_type, title) VALUES (?, ?)",
            ("lease", f"旧文档{i}"))
    conn.commit()
    conn.close()
    return ledger


def test_migrates_rows_and_renames_legacy(env):
    _make_legacy_ledger(env, rows=2)
    conn = doc_db.get_db()          # 默认路径 → 触发迁移
    titles = [r["title"] for r in conn.execute(
        "SELECT title FROM documents ORDER BY id")]
    conn.close()
    assert titles == ["旧文档0", "旧文档1"]
    lg = sqlite3.connect(env / "Family" / "ledger.db")
    names = {r[0] for r in lg.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    lg.close()
    assert "documents_legacy" in names and "documents" not in names


def test_migration_idempotent(env):
    _make_legacy_ledger(env, rows=1)
    doc_db.get_db().close()
    doc_db.get_db().close()          # 第二次连接不得重复搬运
    conn = doc_db.get_db()
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    conn.close()
    assert n == 1


def test_fresh_install_no_ledger_table(env):
    # 账本存在但没有 documents 表（纯财务新世界）
    lg = sqlite3.connect(env / "Family" / "ledger.db")
    lg.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY)")
    lg.commit()
    lg.close()
    conn = doc_db.get_db()
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    conn.close()
    assert n == 0


def test_no_ledger_file_at_all(env):
    conn = doc_db.get_db()
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    conn.close()


def test_explicit_db_path_skips_migration(env, tmp_path):
    _make_legacy_ledger(env, rows=3)
    p = str(tmp_path / "elsewhere.db")
    conn = doc_db.get_db(p)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    conn.close()
    lg = sqlite3.connect(env / "Family" / "ledger.db")
    names = {r[0] for r in lg.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    lg.close()
    assert "documents" in names     # 原地未动
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_doc_migration.py -q` → FAIL
- [ ] **Step 3: Implement** — in `doc_db.py`, add after `get_db`'s SCHEMA executescript and before `return conn` a migration hook, plus the helper. Full new `get_db` + helper:

```python
def get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，自动启用 WAL 和 foreign keys，并确保 documents 表存在。

    幂等建表（CREATE ... IF NOT EXISTS）放在连接处：reminder 每轮轮询都读
    documents，但库在首次 doc-add 前从未 init_db，会 "no such table"。
    在所有读写经过的唯一入口建表，虚拟库上的读取返回空而非崩溃。

    默认路径连接同时兜底做一次账本迁移（documents 表历史上住在
    ledger.db，2026-07 起搬到 documents.db）；显式 db_path（测试）跳过。
    """
    path = db_path or str(_paths.family_documents_db())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    if db_path is None:
        try:
            _migrate_from_ledger(conn, str(_paths.family_ledger()))
        except Exception:
            conn.close()
            raise
    return conn


def _migrate_from_ledger(conn: sqlite3.Connection, ledger_path: str) -> int:
    """把历史 documents 行从账本搬进本库。幂等：本库已有行或账本无表则不动。

    先拷贝后改名：拷贝在本库单事务内；账本表改名 documents_legacy 作保险
    （改名失败下次也不会重搬——本库已有行）。返回搬运行数。
    """
    cur = conn.execute("SELECT COUNT(*) FROM documents")
    if cur.fetchone()[0] > 0 or not os.path.exists(ledger_path):
        return 0
    src = sqlite3.connect(ledger_path)
    src.row_factory = sqlite3.Row
    try:
        has = src.execute("SELECT name FROM sqlite_master "
                          "WHERE type='table' AND name='documents'").fetchone()
        if not has:
            return 0
        rows = src.execute("SELECT * FROM documents ORDER BY id").fetchall()
        if not rows:
            return 0
        cols = rows[0].keys()
        placeholders = ",".join("?" for _ in cols)
        with conn:                                  # 单事务，失败自动回滚
            conn.executemany(
                f"INSERT INTO documents ({','.join(cols)}) VALUES ({placeholders})",
                [tuple(r) for r in rows])
        src.execute("ALTER TABLE documents RENAME TO documents_legacy")
        src.commit()
        return len(rows)
    finally:
        src.close()
```

Note: `get_db` previously used `str(DB_PATH)`; switching to `str(_paths.family_documents_db())` makes the path live (respects DATA_ROOT set after import — the test fixture relies on it). Keep the module-level `DB_PATH` import for backward compat (cli/reminder may reference it), but `get_db` resolves live.

- [ ] **Step 4: Run** — `python -m pytest tests/test_doc_migration.py tests/test_document_keeper.py -q` → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(docs): auto-migrate documents table out of ledger.db"`

---

### Task 4: Profiles API in doc_db

**Files:**
- Modify: `.codewhale/skills/Document_Keeper/doc_db.py` (append)
- Test: `tests/test_profiles.py` (append)

**Interfaces:**
- Produces:
  - `set_profile(member: str, field: str, value: str, db_path=None) -> None` — upsert; ValueError on empty member/field/value.
  - `unset_profile(member: str, field: str, db_path=None) -> bool`
  - `list_profiles(member: str | None = None, db_path=None) -> list[dict]` — dicts `{member, field, value, updated_at}`, ordered by (member, field).

- [ ] **Step 1: Failing tests** — append to `tests/test_profiles.py`:

```python
@pytest.fixture
def pdb(tmp_path):
    p = str(tmp_path / "documents.db")
    doc_db.init_db(p)
    return p


class TestProfilesCRUD:
    def test_set_and_list(self, pdb):
        doc_db.set_profile("Jim Zheng", "生日", "1987-06-11", db_path=pdb)
        doc_db.set_profile("Family", "住址", "309-10530 56 Ave NW", db_path=pdb)
        rows = doc_db.list_profiles(db_path=pdb)
        assert [(r["member"], r["field"], r["value"]) for r in rows] == [
            ("Family", "住址", "309-10530 56 Ave NW"),
            ("Jim Zheng", "生日", "1987-06-11"),
        ]
        assert all(r["updated_at"] for r in rows)

    def test_upsert_overwrites(self, pdb):
        doc_db.set_profile("Jim Zheng", "电话", "111", db_path=pdb)
        doc_db.set_profile("Jim Zheng", "电话", "825-965-9090", db_path=pdb)
        rows = doc_db.list_profiles("Jim Zheng", db_path=pdb)
        assert len(rows) == 1 and rows[0]["value"] == "825-965-9090"

    def test_list_filter_by_member(self, pdb):
        doc_db.set_profile("A", "x", "1", db_path=pdb)
        doc_db.set_profile("B", "y", "2", db_path=pdb)
        assert [r["member"] for r in doc_db.list_profiles("B", db_path=pdb)] == ["B"]

    def test_unset(self, pdb):
        doc_db.set_profile("A", "x", "1", db_path=pdb)
        assert doc_db.unset_profile("A", "x", db_path=pdb) is True
        assert doc_db.unset_profile("A", "x", db_path=pdb) is False
        assert doc_db.list_profiles(db_path=pdb) == []

    def test_empty_rejected(self, pdb):
        for m, f, v in [("", "x", "1"), ("A", "", "1"), ("A", "x", "")]:
            with pytest.raises(ValueError):
                doc_db.set_profile(m, f, v, db_path=pdb)
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_profiles.py -q` → FAIL
- [ ] **Step 3: Implement** — append to `doc_db.py`:

```python
# ---------- 家庭成员资料（profiles，家庭共享） ----------

def set_profile(member: str, field: str, value: str,
                db_path: Optional[str] = None) -> None:
    """写/改一条成员资料（member+field 唯一，upsert）。空参数拒绝。"""
    member, field, value = (member or "").strip(), (field or "").strip(), \
        (str(value) if value is not None else "").strip()
    if not (member and field and value):
        raise ValueError("member/field/value 均不能为空")
    conn = get_db(db_path)
    with conn:
        conn.execute(
            "INSERT INTO profiles (member, field, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(member, field) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (member, field, value, date.today().isoformat()))
    conn.close()


def unset_profile(member: str, field: str,
                  db_path: Optional[str] = None) -> bool:
    """删一条成员资料。返回是否真的删了。"""
    conn = get_db(db_path)
    with conn:
        cur = conn.execute(
            "DELETE FROM profiles WHERE member = ? AND field = ?",
            (member, field))
    conn.close()
    return cur.rowcount > 0


def list_profiles(member: Optional[str] = None,
                  db_path: Optional[str] = None) -> list:
    """列资料（全部或单成员），按 (member, field) 排序。"""
    conn = get_db(db_path)
    if member:
        cur = conn.execute(
            "SELECT member, field, value, updated_at FROM profiles "
            "WHERE member = ? ORDER BY member, field", (member,))
    else:
        cur = conn.execute(
            "SELECT member, field, value, updated_at FROM profiles "
            "ORDER BY member, field")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_profiles.py -q` → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(docs): profiles crud in doc_db"`

---

### Task 5: Profile CLI subcommands

**Files:**
- Modify: `.codewhale/skills/Document_Keeper/cli.py` (new cmd functions + argparse entries)
- Test: `tests/test_profiles.py` (append CLI class)

**Interfaces:**
- Consumes: Task 4 API; `members.member_names()` (Agent_Runtime on sys.path already in doc cli? — check top of cli.py; it inserts Agent_Runtime for paths, so `import members` works).
- Produces subcommands:
  - `profile-set --member-name <n> --field <f> --value <v>` → `✅ <n>.<f> = <v>`
  - `profile-unset --member-name <n> --field <f>` → `已删除 <n>.<f>` or `[错误] 不存在: <n>.<f>`
  - `profile-list [--member-name <n>]` → grouped lines `【<member>】` then `  <field>: <value>`; empty → `没有成员资料。`
- Validation: member-name must be `Family` or in `members.member_names()`; skipped when `DOC_KEEPER_DB` override active (test hook). Failure → `[错误] 成员未登记: <n>（家庭层面请用 Family）`.

- [ ] **Step 1: Failing tests** — append to `tests/test_profiles.py`:

```python
import os
import subprocess
import sys as _sys
from pathlib import Path as _Path

_DOC_CLI = str(_Path(__file__).resolve().parent.parent
               / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")


def _run_cli(*args, db):
    env = {**os.environ, "DOC_KEEPER_DB": db}
    return subprocess.run([_sys.executable, _DOC_CLI, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


class TestProfileCLI:
    def test_set_list_unset_roundtrip(self, tmp_path):
        db = str(tmp_path / "documents.db")
        r = _run_cli("profile-set", "--member-name", "Family",
                     "--field", "住址", "--value", "309-10530 56 Ave NW", db=db)
        assert r.returncode == 0 and "✅" in r.stdout
        r = _run_cli("profile-list", db=db)
        assert "【Family】" in r.stdout and "住址: 309-10530 56 Ave NW" in r.stdout
        r = _run_cli("profile-unset", "--member-name", "Family",
                     "--field", "住址", db=db)
        assert "已删除" in r.stdout
        r = _run_cli("profile-list", db=db)
        assert "没有成员资料" in r.stdout

    def test_unset_missing_errors(self, tmp_path):
        db = str(tmp_path / "documents.db")
        r = _run_cli("profile-unset", "--member-name", "Family",
                     "--field", "无", db=db)
        assert r.returncode == 1 and "[错误]" in r.stdout


def test_cli_member_validation(monkeypatch):
    # 单元级：不经子进程，直接测校验函数（DOC_KEEPER_DB 会放行，所以这里单测）
    import importlib
    import cli_doc_validation_probe  # noqa: F401  (占位说明，见下)
```

Replace that last unit test with a direct import approach — `Document_Keeper/cli.py` is not importable as a module name (`cli` collides across skills). Instead test validation via subprocess WITHOUT the override:

```python
def test_cli_member_validation_without_override(tmp_path, monkeypatch):
    # 无 DOC_KEEPER_DB → 校验生效。未登记名字必须报错（不落库，DATA_ROOT 指向 tmp）。
    env = {**os.environ, "DATA_ROOT": str(tmp_path / "data")}
    env.pop("DOC_KEEPER_DB", None)
    r = subprocess.run([_sys.executable, _DOC_CLI, "profile-set",
                        "--member-name", "Nobody_XYZ", "--field", "x",
                        "--value", "1"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    assert r.returncode == 1
    assert "成员未登记" in r.stdout
```

(Do not include the `cli_doc_validation_probe` placeholder block — write only the subprocess version.)

- [ ] **Step 2: Run** — `python -m pytest tests/test_profiles.py -q` → new tests FAIL
- [ ] **Step 3: Implement** — in `Document_Keeper/cli.py` add (near other cmd_ functions):

```python
def _validate_profile_member(name: str) -> str:
    """profile 目标成员：登记成员显示名或字面 Family。DOC_KEEPER_DB 覆盖（测试）放行。"""
    name = (name or "").strip()
    if _DB_OVERRIDE or name == "Family":
        return name
    import members as _members
    if name not in _members.member_names():
        print(f"[错误] 成员未登记: {name}（家庭层面请用 Family）")
        sys.exit(1)
    return name


def cmd_profile_set(args):
    name = _validate_profile_member(args.member_name)
    try:
        doc_db.set_profile(name, args.field, args.value, db_path=_DB_OVERRIDE)
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    _mark_backup_dirty()
    print(f"✅ {name}.{args.field} = {args.value}")


def cmd_profile_unset(args):
    name = _validate_profile_member(args.member_name)
    if not doc_db.unset_profile(name, args.field, db_path=_DB_OVERRIDE):
        print(f"[错误] 不存在: {name}.{args.field}")
        sys.exit(1)
    _mark_backup_dirty()
    print(f"已删除 {name}.{args.field}")


def cmd_profile_list(args):
    rows = doc_db.list_profiles(args.member_name or None, db_path=_DB_OVERRIDE)
    if not rows:
        print("没有成员资料。")
        return
    cur = None
    for r in rows:
        if r["member"] != cur:
            cur = r["member"]
            print(f"【{cur}】")
        print(f"  {r['field']}: {r['value']}")
```

If `cli.py` lacks `_mark_backup_dirty`, copy the standard helper from Note_Keeper cli (try-import backup_sync.mark_dirty, silent failure). Check first — Document_Keeper cli likely already has one.

Argparse entries (in main/subparser section):

```python
    p = sub.add_parser("profile-set", help="写/改一条家庭成员资料（家庭共享）")
    p.add_argument("--member-name", required=True, help="登记成员显示名或 Family")
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.set_defaults(func=cmd_profile_set)

    p = sub.add_parser("profile-unset", help="删一条家庭成员资料")
    p.add_argument("--member-name", required=True)
    p.add_argument("--field", required=True)
    p.set_defaults(func=cmd_profile_unset)

    p = sub.add_parser("profile-list", help="列家庭成员资料")
    p.add_argument("--member-name", default="")
    p.set_defaults(func=cmd_profile_list)
```

Match the file's actual dispatch mechanism (it may use a dict of handlers instead of set_defaults — follow what's there).

- [ ] **Step 4: Run** — `python -m pytest tests/test_profiles.py -q` → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(docs): profile cli subcommands"`

---

### Task 6: Agent wiring — commands, tools, injection, prompt

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_profiles.py` (append agent class)

**Interfaces:**
- Consumes: Task 5 subcommands; Task 4 `doc_db.list_profiles`.
- Produces: tools `set_profile_field(member_name, field, value)`, `remove_profile_field(member_name, field)`; `_profiles_context() -> str`.

- [ ] **Step 1: Failing tests** — append to `tests/test_profiles.py`:

```python
class TestAgentWiring:
    def test_commands_allowed_and_routed(self):
        import agent_core
        for c in ("profile-set", "profile-unset", "profile-list"):
            assert c in agent_core.ALLOWED_COMMANDS
            assert agent_core._cli_path(c).parent.name == "Document_Keeper"

    def test_tools_registered(self):
        import agent_core
        names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert {"set_profile_field", "remove_profile_field"} <= names
        assert {"set_profile_field", "remove_profile_field"} <= set(agent_core._TOOL_MAP)

    def test_not_member_injected(self):
        # 目标成员是显式数据（member_name），不能被发送者身份覆盖
        import agent_core
        out = agent_core._apply_member(
            "set_profile_field",
            {"member_name": "Euphie", "field": "生日", "value": "2016-10-18"},
            "Jim Zheng")
        assert "member" not in out
        assert out["member_name"] == "Euphie"

    def test_profiles_context_renders(self, tmp_path, monkeypatch):
        import agent_core
        db = str(tmp_path / "documents.db")
        import doc_db as _doc_db
        _doc_db.init_db(db)
        _doc_db.set_profile("Jim Zheng", "生日", "1987-06-11", db_path=db)
        block = agent_core._profiles_context(db_path=db)
        assert "家庭成员资料" in block and "1987-06-11" in block
        assert agent_core._profiles_context(db_path=str(tmp_path / "empty.db")) == ""

    def test_system_prompt_mentions_profiles(self):
        import agent_core
        p = agent_core._build_system_prompt()
        assert "set_profile_field" in p
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_profiles.py -q` → FAIL
- [ ] **Step 3: Implement** — `agent_core.py` edits:

3a. After `_DOC_AGENT_COMMANDS` definition (~line 148):

```python
_PROFILE_COMMANDS = {"profile-set", "profile-unset", "profile-list"}
```

3b. After `ALLOWED_COMMANDS |= _BACKUP_AGENT_COMMANDS` block, add:

```python
ALLOWED_COMMANDS |= _PROFILE_COMMANDS
```

3c. `_cli_path` documents branch:

```python
    if cmd in _DOC_COMMANDS or cmd in _DOC_FILE_COMMANDS or cmd in _PROFILE_COMMANDS:
        skill = "Document_Keeper"
```

3d. Tool impls (after `_tool_ack_document` or nearby doc tools):

```python
def _tool_set_profile_field(args): return _run_cli("profile-set", args)
def _tool_remove_profile_field(args): return _run_cli("profile-unset", args)
```

(`member_name` arrives as `member_name` key → `_run_cli` renders `--member_name`; argparse expects `--member-name`. `_run_cli` builds flags verbatim from keys, so name the schema parameter `member-name`. See 3f.)

3e. `_TOOL_MAP` entries:

```python
    "set_profile_field": _tool_set_profile_field,
    "remove_profile_field": _tool_remove_profile_field,
```

3f. `TOOL_SCHEMAS` additions (before closing `]`) — parameter keys use the CLI flag spelling `member-name`:

```python
    _fn("set_profile_field", "写/改一条家庭成员资料（法定名/生日/电话/邮箱/住址/证件号等"
        "长期个人事实，全家共享，全家可见可改）。用户提供这类信息时随手存这里，别存备忘。", {
        "member-name": _s("这条事实属于谁：登记成员显示名；家庭层面（住址等）用 Family"),
        "field": _s("字段名，如 生日 / 电话 / LAP卡号"),
        "value": _s("值"),
    }, ["member-name", "field", "value"]),
    _fn("remove_profile_field", "删一条家庭成员资料。", {
        "member-name": _s("成员显示名或 Family"),
        "field": _s("字段名"),
    }, ["member-name", "field"]),
```

Adjust the wiring test's `_apply_member` call to use `"member-name"` key if you follow this spelling (update test accordingly — key consistency matters more than the exact name).

3g. Injection function after `_worksheets_context` (Document_Keeper path insert already needed — add alongside existing inserts at ~line 1075):

```python
sys.path.insert(0, str(ROOT / ".codewhale" / "skills" / "Document_Keeper"))
```

```python
def _profiles_context(db_path: str | None = None) -> str:
    """家庭成员资料块（家庭共享，注入所有成员对话）。失败/为空返回空串。"""
    try:
        import doc_db as _doc_db
        rows = _doc_db.list_profiles(db_path=db_path)
        if not rows:
            return ""
        lines, cur = [], None
        for r in rows:
            if r["member"] != cur:
                cur = r["member"]
                lines.append(f"【{cur}】")
            lines.append(f"  {r['field']}: {r['value']}")
        return ("\n\n## 家庭成员资料（全家共享，可用 set_profile_field 更新）\n"
                + "\n".join(lines))
    except Exception:
        _log.exception("成员资料上下文注入失败（已跳过）")
        return ""
```

3h. In `handle()`, system message assembly — add `_profiles_context()`:

```python
        msgs = [{"role": "system",
                 "content": self.system_prompt + member_note
                 + _profiles_context()
                 + _notes_context(member) + _worksheets_context(member)
                 + _schedule_context(member)}]
```

3i. System prompt behavior rules (in `_build_system_prompt` 行为准则 section, near the note-keeping rule):

```
- 用户提供长期个人事实（法定名/生日/电话/邮箱/住址/证件卡号等）→ set_profile_field 存到家庭成员资料（member-name 填事实属于谁，家庭层面如住址用 Family），不要存成备忘；这类信息全家共享
- 填 PDF 表格时建议值优先取自"家庭成员资料"块（仍然逐字段问用户确认）
```

- [ ] **Step 4: Run** — `python -m pytest tests/ -q` → ALL PASS (full suite — handle() shared code touched)
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(agent): family profiles tools and context injection"`

---

### Task 7: Docs + live data migration + verification

**Files:**
- Modify: `.codewhale/skills/Document_Keeper/SKILL.md`, `README.md`, `FamilyAssistant.md`
- Live data: `data/Family/ledger.db` → `data/Family/documents.db`; worksheet 「家庭成员信息」 → profiles

- [ ] **Step 1: SKILL.md** — Document_Keeper/SKILL.md: update storage line (documents.db, was ledger.db), add profiles table schema + 3 CLI commands + shared-visibility note + migration note (documents_legacy).
- [ ] **Step 2: README.md / FamilyAssistant.md** — update 目录结构/存储描述 mentions of ledger.db documents; add profiles blurb to Document Keeper feature section (README) and skill table (FamilyAssistant.md). Search both files for `ledger.db` and update affected lines.
- [ ] **Step 3: Live migration — documents rows** — run:

```powershell
python .codewhale/skills/Document_Keeper/cli.py doc-list
```

Expected: prints existing docs (may be empty); side effect = auto-migration ran. Verify: `data/Family/documents.db` exists; ledger has `documents_legacy`:

```powershell
python -c "import sqlite3; print([r[0] for r in sqlite3.connect('data/Family/ledger.db').execute(\"select name from sqlite_master where type='table'\")])"
python -c "import sqlite3; print(sqlite3.connect('data/Family/documents.db').execute('select count(*) from documents').fetchone())"
```

- [ ] **Step 4: Live migration — worksheet → profiles** — one-off script (not committed):

Mapping: `Jim 法定名/生日/LAP卡号` → member "Jim Zheng"; `Wenliang *` → "Wenliang Li"; `Euphie *` (incl. 证件) → "Euphie"; `家庭住址`、`2025年收入` → "Family"; `邮箱`、`电话` → "Jim Zheng". Field name = worksheet key minus member prefix (e.g. "Jim 生日" → "生日").

```powershell
python - <<'EOF'
import subprocess, sys
sys.path.insert(0, ".codewhale/skills/Note_Keeper")
sys.path.insert(0, ".codewhale/skills/Agent_Runtime")
import sheet_db, paths
CLI = ".codewhale/skills/Document_Keeper/cli.py"
# read worksheet kv…, for each: subprocess profile-set; then sheet-delete via Note_Keeper cli
EOF
```

(Exact script written at execution time — read the worksheet with sheet_db API present in Note_Keeper, then `profile-set` each mapped entry, then delete worksheet 「家庭成员信息」 via Note_Keeper CLI `sheet-delete`.)

- [ ] **Step 5: Verify end state**

```powershell
python .codewhale/skills/Document_Keeper/cli.py profile-list
```

Expected: 【Euphie】【Family】【Jim Zheng】【Wenliang Li】 groups with all 14 facts.

```powershell
python .codewhale/skills/Note_Keeper/cli.py sheet-list --member "Jim Zheng"
```

Expected: 「家庭成员信息」 gone.

- [ ] **Step 6: Full suite + commit**

```bash
python -m pytest tests/ -q   # all pass
git add -A && git commit -m "docs: documents.db split and family profiles documentation"
```

---

## Self-review notes

- Spec coverage: paths (T1), DB_PATH+schema (T2), migration (T3), profiles API (T4), CLI (T5), agent tools/injection/prompt (T6), docs + live migration (T7). ✔
- Task 5 Step 1 contains an intentionally-flagged placeholder block with explicit instruction to write only the subprocess variant — implementer must not copy the probe import.
- Type consistency: `member-name` flag spelling — schema keys use `member-name` (hyphen) so `_run_cli` emits `--member-name`; wiring test must use the same key. Called out in 3f.
- `get_db` live-path change (DB_PATH → `_paths.family_documents_db()` call) is deliberate; keep `DB_PATH` importable for cli/reminder compat.
