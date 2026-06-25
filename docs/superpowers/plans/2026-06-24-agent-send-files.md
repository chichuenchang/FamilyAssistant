# Agent Sends Documents / Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent send an archived document (`send_document(id)`) or an allowed data file (`send_file(path)`) back to the user over WeChat / Telegram / CLI test mode.

**Architecture:** Generalize the existing chart-image sentinel pipeline. Add a `\x01DOC:` sentinel and a `send_document`/`send_file` tool pair in `agent_core`; `split_reply` returns `(text, images, docs)`. A new read-only `doc-file` CLI in Document_Keeper resolves an archived doc's path; `send_file` validates an arbitrary data path in-process against a family-or-own-member gate. Each transport's `_send_reply` sends images, then docs, then text.

**Tech Stack:** Python 3.12+, pytest, urllib (Telegram multipart). No new deps.

## Global Constraints

- Sentinels are code-appended, never LLM text: `IMG_SENTINEL = "\x01IMG:"`, `DOC_SENTINEL = "\x01DOC:"`.
- `split_reply(reply) -> tuple[str, list[str], list[str]]` = `(text, images, docs)`.
- Both new tools are **member-forced** (member injected by code).
- `send_file` gate (gate A): resolved path must exist, be a file, be under `paths.data_root()`, AND within `paths.family_dir()` or `paths.member_dir(member)`.
- `send_document` resolves via read-only CLI `doc-file --id N`; Document_Keeper test override env `DOC_KEEPER_DB`.
- Transport re-checks `exists()` + under `data_root` before every send; failure logged + skipped, text still sent.
- Data-root test override env: `DATA_ROOT`.

---

## File Structure

- Modify: `.codewhale/skills/Document_Keeper/cli.py` — `doc-file` subcommand.
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` — `DOC_SENTINEL`, 3-tuple `split_reply`, `_resolve_sendable`, `send_document`/`send_file` handlers + schemas + wiring, `_IMAGE_TOOLS`/`_DOC_TOOLS`, `produced_docs`.
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py` — `_send_reply` 3-tuple (reply_file) + test mode.
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py` — `send_document` helper + `_send_reply` 3-tuple.
- Modify: `tests/test_chart.py` — update `split_reply` test to 3-tuple.
- Create: `tests/test_send_files.py` — doc-file CLI, gate, split_reply, transport tests.
- Modify: `.codewhale/skills/Document_Keeper/SKILL.md` — document `doc-file` + send tools.

---

### Task 1: `doc-file` CLI (Document_Keeper)

**Files:**
- Modify: `.codewhale/skills/Document_Keeper/cli.py`
- Test: `tests/test_send_files.py`

**Interfaces:**
- Produces: CLI `doc-file --id N` → prints data-relative `file_path` (exit 0); unknown id or no file → stderr `[错误] 文档无文件` + exit 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_send_files.py — agent 发文档/文件 测试
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC_SKILL = ROOT / ".codewhale" / "skills" / "Document_Keeper"
AR = ROOT / ".codewhale" / "skills" / "Agent_Runtime"
sys.path.insert(0, str(DOC_SKILL))
sys.path.insert(0, str(AR))


def _seed_doc(db, file_path):
    import doc_db
    doc_db.init_db(db_path=db)
    return doc_db.add_document(title="租约", doc_type="lease", file_path=file_path,
                               db_path=db)


def _doc_cli(db, *args):
    env = dict(os.environ, DOC_KEEPER_DB=db, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(DOC_SKILL / "cli.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_doc_file_prints_path(tmp_path):
    db = str(tmp_path / "doc.db")
    doc_id = _seed_doc(db, "Family/documents/lease/lease.pdf")
    r = _doc_cli(db, "doc-file", "--id", str(doc_id))
    assert r.returncode == 0
    assert r.stdout.strip() == "Family/documents/lease/lease.pdf"


def test_doc_file_no_file(tmp_path):
    db = str(tmp_path / "doc.db")
    doc_id = _seed_doc(db, "")
    assert _doc_cli(db, "doc-file", "--id", str(doc_id)).returncode == 1


def test_doc_file_unknown_id(tmp_path):
    db = str(tmp_path / "doc.db")
    import doc_db
    doc_db.init_db(db_path=db)
    assert _doc_cli(db, "doc-file", "--id", "999").returncode == 1
```

Note: confirm `doc_db.add_document` accepts `title`, `doc_type`, `file_path`, `db_path` (read `doc_db.py` signature). If a required arg is missing, add it to `_seed_doc` per the real signature.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_send_files.py -k doc_file -v`
Expected: FAIL — `doc-file` invalid choice (argparse exit 2).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add a command function before `def main()`:

```python
def cmd_doc_file(args):
    d = doc_db.get_document(args.id, db_path=_DB_OVERRIDE)
    if d is None or not d.get("file_path"):
        print("[错误] 文档无文件", file=sys.stderr)
        sys.exit(1)
    print(d["file_path"])
```

Register subparser inside `main()` after the `doc-show` parser:

```python
    p = sub.add_parser("doc-file", help="打印文档原件的 data 相对路径（用于发送）")
    p.add_argument("--id", type=int, required=True)
```

Add to `dispatch`:

```python
        "doc-file": cmd_doc_file,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_send_files.py -k doc_file -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Document_Keeper/cli.py tests/test_send_files.py
git commit -m "feat(docs): doc-file CLI prints archived doc path"
```

---

### Task 2: agent_core — DOC sentinel + 3-tuple split_reply + doc collection

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Modify: `tests/test_chart.py` (update existing split_reply test)
- Test: `tests/test_send_files.py`

**Interfaces:**
- Consumes: existing `IMG_SENTINEL`, `handle()` loop, `produced_images`.
- Produces:
  - `DOC_SENTINEL = "\x01DOC:"`.
  - `split_reply(reply) -> tuple[str, list[str], list[str]]` = `(text, images, docs)`.
  - `_IMAGE_TOOLS = {"visualize_data"}`, `_DOC_TOOLS = {"send_document", "send_file"}`.
  - `handle()` collects doc paths into `produced_docs`, appends `\x01DOC:` lines.

- [ ] **Step 1: Write the failing test**

In `tests/test_send_files.py` add:

```python
def test_split_reply_3tuple():
    import agent_core as ac
    r = ("hi\n" + ac.IMG_SENTINEL + "a.png\n" + ac.DOC_SENTINEL + "Family/x.pdf")
    text, imgs, docs = ac.split_reply(r)
    assert text == "hi" and imgs == ["a.png"] and docs == ["Family/x.pdf"]
    # passthrough
    assert ac.split_reply("plain") == ("plain", [], [])
    # doc only
    t, i, d = ac.split_reply(ac.DOC_SENTINEL + "p.pdf")
    assert t == "" and i == [] and d == ["p.pdf"]
```

Also UPDATE the existing 2-tuple test in `tests/test_chart.py` (`test_split_reply`) to unpack 3 values:

```python
def test_split_reply():
    import agent_core as ac
    text, imgs, docs = ac.split_reply("hello\n" + ac.IMG_SENTINEL + "Family/charts/a.png")
    assert text == "hello" and imgs == ["Family/charts/a.png"] and docs == []
    r = "line1\nline2\n" + ac.IMG_SENTINEL + "a.png\n" + ac.IMG_SENTINEL + "b.png"
    text, imgs, docs = ac.split_reply(r)
    assert text == "line1\nline2" and imgs == ["a.png", "b.png"] and docs == []
    text, imgs, docs = ac.split_reply("just text")
    assert text == "just text" and imgs == [] and docs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_send_files.py -k split_reply_3tuple -v`
Expected: FAIL — `AttributeError: ... 'DOC_SENTINEL'` (or unpack error).

- [ ] **Step 3: Write minimal implementation**

Replace the existing `IMG_SENTINEL`/`split_reply` block in `agent_core.py`:

```python
IMG_SENTINEL = "\x01IMG:"
DOC_SENTINEL = "\x01DOC:"


def split_reply(reply: str) -> tuple[str, list[str], list[str]]:
    """剥离 \\x01IMG:/\\x01DOC: 哨兵行，返回 (可见文本, [图片], [文档]) 三元组。"""
    imgs, docs, keep = [], [], []
    for line in (reply or "").split("\n"):
        if line.startswith(IMG_SENTINEL):
            p = line[len(IMG_SENTINEL):].strip()
            if p:
                imgs.append(p)
        elif line.startswith(DOC_SENTINEL):
            p = line[len(DOC_SENTINEL):].strip()
            if p:
                docs.append(p)
        else:
            keep.append(line)
    return "\n".join(keep).strip(), imgs, docs
```

Add tool-kind sets near `_SHEET_TOOLS` (after its definition):

```python
_IMAGE_TOOLS = {"visualize_data"}
_DOC_TOOLS = {"send_document", "send_file"}
```

In `handle()`, add a docs collector beside `produced_images`:

```python
        produced_docs: list[str] = []
```

Replace the existing image-collection line in the tool loop:

```python
                if name == "visualize_data" and result and not result.startswith("[错误]"):
                    produced_images.append(result.strip())
```

with:

```python
                if result and not result.startswith("[错误]"):
                    if name in _IMAGE_TOOLS:
                        produced_images.append(result.strip())
                    elif name in _DOC_TOOLS:
                        produced_docs.append(result.strip())
```

Replace the sentinel-append block before `return final`:

```python
        for p in produced_images:
            final += f"\n{IMG_SENTINEL}{p}"
        for p in produced_docs:
            final += f"\n{DOC_SENTINEL}{p}"
        return final
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_send_files.py -k split_reply_3tuple tests/test_chart.py -k split_reply -v`
(Or simply `python -m pytest tests/test_chart.py tests/test_send_files.py -q`.)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_chart.py tests/test_send_files.py
git commit -m "feat(agent): DOC sentinel + 3-tuple split_reply + doc collection"
```

---

### Task 3: agent_core — send_document / send_file tools + gate

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_send_files.py`

**Interfaces:**
- Consumes: `doc-file` CLI (Task 1), `_DOC_TOOLS` (Task 2), `paths` helpers.
- Produces:
  - `_resolve_sendable(path: str, member: str) -> str | None` — gate A; rel path or None.
  - `_tool_send_document(args)`, `_tool_send_file(args)`.
  - tools registered, routed, whitelisted, member-forced, with schemas + prompt.

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_sendable_gate(tmp_path, monkeypatch):
    # No members.json → member_dir_name falls back to slug: "Alex"->"alex", "Bob"->"bob".
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, paths as _p
    importlib.reload(_p)
    import agent_core as ac
    importlib.reload(ac)
    # build files (use slug dir names)
    fam = tmp_path / "Family" / "documents"; fam.mkdir(parents=True)
    (fam / "lease.pdf").write_bytes(b"x")
    alex = tmp_path / "alex" / "notes"; alex.mkdir(parents=True)
    (alex / "card.png").write_bytes(b"x")
    bob = tmp_path / "bob" / "notes"; bob.mkdir(parents=True)
    (bob / "secret.png").write_bytes(b"x")

    assert ac._resolve_sendable("Family/documents/lease.pdf", "Alex") == "Family/documents/lease.pdf"
    assert ac._resolve_sendable("alex/notes/card.png", "Alex") == "alex/notes/card.png"
    assert ac._resolve_sendable("bob/notes/secret.png", "Alex") is None        # cross-member
    assert ac._resolve_sendable("../outside.txt", "Alex") is None              # escape
    assert ac._resolve_sendable("Family/documents/missing.pdf", "Alex") is None  # missing
```

Note: `MEMBERS_PATH` is a fixed module constant in `members.py` (no env override); with no registry entry, `member_dir_name("Alex")` returns `_slug("Alex")` = `"alex"`. `paths` reads `DATA_ROOT`, so reload `paths` then `agent_core` after setting the env. `family_dir()` = `DATA_ROOT/"Family"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_send_files.py -k resolve_sendable -v`
Expected: FAIL — `AttributeError: ... '_resolve_sendable'`.

- [ ] **Step 3: Write minimal implementation**

Add the gate + handlers near the other `_tool_*` functions in `agent_core.py`:

```python
def _resolve_sendable(path: str, member: str) -> str | None:
    """送文件闸门：路径须存在、是文件、在 data_root 内，且属家庭共享或本成员目录。
    通过 → 返回 data 相对路径；否则 None。"""
    try:
        p = Path(path)
        ap = (p if p.is_absolute() else _paths.resolve_rel(str(path))).resolve()
        root = _paths.data_root().resolve()
        if not (ap.exists() and ap.is_file() and ap.is_relative_to(root)):
            return None
        allowed = [_paths.family_dir().resolve()]
        if member:
            allowed.append(_paths.member_dir(member).resolve())
        if not any(ap.is_relative_to(a) for a in allowed):
            return None
        return _paths.to_rel(ap)
    except (ValueError, OSError):
        return None


def _tool_send_document(args):
    return _run_cli("doc-file", {"id": args.get("id")})


def _tool_send_file(args):
    member = args.get("member", "")
    rel = _resolve_sendable(args.get("path", ""), member)
    return rel if rel else "[错误] 路径不允许或文件不存在"
```

Add command set + routing + whitelist. After `_CHART_COMMANDS = {...}`:

```python
_DOC_FILE_COMMANDS = {"doc-file"}
```

In `_cli_path`, extend the Document_Keeper branch:

```python
    if cmd in _DOC_COMMANDS or cmd in _DOC_FILE_COMMANDS:
        skill = "Document_Keeper"
```

After `ALLOWED_COMMANDS |= _CHART_COMMANDS`:

```python
ALLOWED_COMMANDS |= _DOC_FILE_COMMANDS
```

Register in the tool dispatch dict (after `"visualize_data": _tool_visualize_data,`):

```python
    "send_document": _tool_send_document,
    "send_file": _tool_send_file,
```

Add both to the member-forced set. The set used by `_apply_member` is `_SHEET_TOOLS`; extend it:

```python
                "pin_worksheet", "delete_worksheet", "visualize_data",
                "send_document", "send_file"}
```

Add tool schemas after the `visualize_data` `_fn(...)` entry:

```python
    _fn("send_document", "把已归档的文档原件（租约/保单/证件等）发给用户。"
        "先用 list_documents/show_document 找到对应文档的 id", {
        "id": _int("文档 id"),
    }, ["id"]),
    _fn("send_file", "把 data 目录内的一个文件发给用户（path 为 data 相对路径）。"
        "只能发家庭共享文件或你自己的文件", {
        "path": _s("文件的 data 相对路径"),
    }, ["path"]),
```

Add prompt guidance after the visualize line:

```
- 用户说"把我的租约/保单发给我""发我那个文件/那张图" → send_document（先 list/show 拿 id）或 send_file（data 内相对路径）；文件会自动发给用户
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_send_files.py -k resolve_sendable -v`
Expected: PASS.

- [ ] **Step 5: Verify registration**

Run: `python -c "import sys; sys.path.insert(0,'.codewhale/skills/Agent_Runtime'); import agent_core as a; assert {'send_document','send_file'} <= set(a._TOOL_MAP); assert {'send_document','send_file'} <= a._SHEET_TOOLS; assert 'doc-file' in a.ALLOWED_COMMANDS; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_send_files.py
git commit -m "feat(agent): send_document + send_file tools with family/member gate"
```

---

### Task 4: transports — deliver docs + Telegram sendDocument

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py`
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py`
- Modify: `.codewhale/skills/Document_Keeper/SKILL.md`
- Test: `tests/test_send_files.py`

**Interfaces:**
- Consumes: `split_reply` (3-tuple), `paths.resolve_rel`, `paths.data_root`.

- [ ] **Step 1: Write the failing test (Telegram sendDocument multipart)**

```python
def test_tg_send_document_multipart(tmp_path, monkeypatch):
    sys.path.insert(0, str(AR))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    import importlib, telegram_bot as tg
    importlib.reload(tg)
    f = tmp_path / "lease.pdf"; f.write_bytes(b"%PDF-1.4 test")
    captured = {}

    class FakeResp:
        def read(self): return b'{"ok":true}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["ct"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
        captured["body"] = req.data
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok = tg.send_document(123, str(f))
    assert ok is True
    assert b"multipart/form-data" in captured["ct"].encode()
    assert b'name="document"' in captured["body"]
    assert b'name="chat_id"' in captured["body"]
    assert b"%PDF-1.4 test" in captured["body"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_send_files.py -k tg_send_document -v`
Expected: FAIL — `AttributeError: module 'telegram_bot' has no attribute 'send_document'`.

- [ ] **Step 3: Telegram — add `send_document`, extend `_send_reply`**

In `telegram_bot.py`, add after `send_photo`:

```python
def send_document(chat_id: int | str, path: str, caption: str = "") -> bool:
    """sendDocument 多部分上传（urllib，无新依赖）。"""
    import mimetypes, urllib.request, uuid
    boundary = uuid.uuid4().hex
    p = Path(path)
    try:
        file_bytes = p.read_bytes()
    except OSError:
        return False
    parts = []

    def _field(name, value):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode())

    _field("chat_id", str(chat_id))
    if caption:
        _field("caption", caption)
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; "
                  f'name="document"; filename="{p.name}"\r\n'
                  f"Content-Type: {mimetypes.guess_type(p.name)[0] or 'application/octet-stream'}"
                  "\r\n\r\n").encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{BASE}/sendDocument", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        return bool(r and r.get("ok"))
    except Exception as e:
        print(f"[tg] sendDocument 错误: {e}", file=sys.stderr)
        return False
```

Replace the body of `_send_reply` in `telegram_bot.py`:

```python
def _send_reply(chat_id, reply: str) -> None:
    """拆出图片/文档哨兵：先发图，再发文档，最后发文字。失败仅记录，不影响文字。"""
    from agent_core import split_reply
    import paths as _paths
    text, imgs, docs = split_reply(reply or "")
    root = _paths.data_root().resolve()
    for rel in imgs:
        try:
            ap = _paths.resolve_rel(rel).resolve()
            if ap.exists() and ap.is_relative_to(root):
                send_photo(chat_id, str(ap))
        except Exception as e:
            print(f"[tg] 发图失败 {rel}: {e}", file=sys.stderr)
    for rel in docs:
        try:
            ap = _paths.resolve_rel(rel).resolve()
            if ap.exists() and ap.is_relative_to(root):
                send_document(chat_id, str(ap))
        except Exception as e:
            print(f"[tg] 发文件失败 {rel}: {e}", file=sys.stderr)
    if text:
        send_message(chat_id, text)
```

- [ ] **Step 4: WeChat — extend `_send_reply` for docs + test mode**

In `wechat_ilink.py`, replace the body of `_send_reply`:

```python
def _send_reply(msg, reply: str) -> None:
    """拆出图片/文档哨兵：先发图，再发文档，最后发文字。失败仅记录，不影响文字。"""
    text, imgs, docs = _split_reply(reply or "")
    root = _paths.data_root().resolve()
    for rel in imgs:
        try:
            ap = _paths.resolve_rel(rel).resolve()
            if ap.exists() and ap.is_relative_to(root):
                msg.reply_image(str(ap))
        except Exception:
            log.exception("发送图片失败（跳过）: %s", rel)
    for rel in docs:
        try:
            ap = _paths.resolve_rel(rel).resolve()
            if ap.exists() and ap.is_relative_to(root):
                msg.reply_file(str(ap))
        except Exception:
            log.exception("发送文件失败（跳过）: %s", rel)
    if text:
        msg.reply_text(text)
```

In `run_test()`, update the unpack to 3-tuple:

```python
        text, imgs, docs = _split_reply(reply)
        for rel in imgs:
            print(f"助手> [图片] {rel}")
        for rel in docs:
            print(f"助手> [文件] {rel}")
        if text:
            print(f"助手> {text}")
        print()
```

- [ ] **Step 5: Run the Telegram test + full suite**

Run: `python -m pytest tests/test_send_files.py -k tg_send_document -v`
Expected: PASS.

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Docs**

In `Document_Keeper/SKILL.md`, add a short section: `doc-file --id N` prints the
archived doc's data-relative path (read-only, for sending); the agent tools
`send_document(id)` / `send_file(path)` deliver files to the user; `send_file`
is gated to family-shared or the requesting member's own dir; delivery rides the
`\x01DOC:` sentinel + transport `_send_reply` (WeChat `reply_file`, Telegram
`sendDocument`).

- [ ] **Step 7: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/wechat_ilink.py .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Document_Keeper/SKILL.md tests/test_send_files.py
git commit -m "feat(transport): deliver documents over wechat/telegram (reply_file / sendDocument)"
```

---

## Self-Review

**Spec coverage:** `doc-file` CLI (T1), DOC sentinel + 3-tuple split_reply + doc collection (T2), `send_document`/`send_file` tools + gate A + wiring + schemas + prompt (T3), transports wechat reply_file + telegram sendDocument + test mode + docs (T4). All spec sections mapped.

**Placeholder scan:** No TBD/TODO; every code step shows full code. T1/T3 include "confirm signature / members lookup" verification notes — these are guardrails for environment-specific details (doc_db.add_document args, members.json location), not placeholders for code.

**Type consistency:** `split_reply -> (text, imgs, docs)` 3-tuple defined T2, consumed by both transports + both split tests (T2, T4) and the updated chart test (T2). `_resolve_sendable(path, member) -> str|None` (T3) used by `_tool_send_file`. `send_document` transport helper (T4) distinct from `send_document` agent tool (T3) — different modules, noted. `_DOC_TOOLS`/`_IMAGE_TOOLS` (T2) drive collection; tools added to `_SHEET_TOOLS` member-forced set + `_TOOL_MAP` (T3). `doc-file` routed via `_DOC_FILE_COMMANDS` (T3), matching the `cmd_doc_file` CLI (T1).
