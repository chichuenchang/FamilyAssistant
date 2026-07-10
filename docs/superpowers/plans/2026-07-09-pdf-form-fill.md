# PDF Form Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent receives a PDF form over WeChat/Telegram, asks the user one field per message, and returns the completed PDF.

**Architecture:** New self-contained skill `.codewhale/skills/Form_Filler/` (session store + AcroForm fill via pypdf + flat-PDF overlay via pypdfium2/Pillow), exposed to the LLM as `_run_cli` tool wrappers in `agent_core.py`. Session state is JSON on disk under `data/<member>/forms/` so the one-field-per-message ping-pong survives history trimming, `/clear`, and restarts.

**Tech Stack:** Python stdlib core; optional deps pypdf (AcroForm), pypdfium2 (page render), Pillow (stamping). Tencent OCR (existing skill) for flat-form label coordinates. pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-pdf-form-fill-design.md`

## Global Constraints

- Core runtime is stdlib-only; pypdf/pypdfium2/Pillow are OPTIONAL deps that degrade gracefully with a `pip install …` hint (same pattern as matplotlib in Note_Keeper).
- All user-facing CLI output is Chinese, plain text; errors start with `[错误] `.
- File links stored in sessions are data_root-relative posix paths (`paths.to_rel` / `paths.resolve_rel`).
- Member privacy: form sessions live under `data/<member>/forms/`; agent_core injects `member` by code (`_apply_member`), LLM never chooses it. Source PDFs must resolve inside data_root AND inside the sender's member dir or Family dir.
- Comments/docstrings in Chinese, matching existing files.
- Windows console encoding guard block at top of every CLI (copy from Note_Keeper cli.py).
- Test hook: `DATA_ROOT` env var overrides the data root (already supported by `paths.py`).
- Flat-form page cap: 6 pages (`MAX_FLAT_PAGES`), render scale 2.0, min font 8 px.
- Commit after every task; run `python -m pytest tests/ -x -q` before each commit.

---

### Task 1: `paths.member_forms_dir`

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/paths.py` (after `member_inbox_dir`, ~line 112)
- Test: `tests/test_paths.py` (append)

**Interfaces:**
- Produces: `member_forms_dir(member: str) -> Path` — `data/<member>/forms/`, mkdir'd.

- [ ] **Step 1: Write the failing test** — append to `tests/test_paths.py`:

```python
def test_member_forms_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    import importlib
    import paths
    importlib.reload(paths)
    d = paths.member_forms_dir("Jim")
    assert d == tmp_path / "data" / "jim" / "forms"
    assert d.is_dir()
```

Note: match the existing style in `tests/test_paths.py` — if existing tests reload or import `paths` differently, follow that file's convention instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_paths.py::test_member_forms_dir -q`
Expected: FAIL — `AttributeError: module 'paths' has no attribute 'member_forms_dir'`

- [ ] **Step 3: Implement** — add to `paths.py` after `member_inbox_dir`:

```python
def member_forms_dir(member: str) -> Path:
    """填表会话与产出 data/<成员>/forms/，不存在则创建。"""
    d = member_dir(member) / "forms"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

Also add `data/<成员目录>/forms/` line to the module docstring's layout list.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_paths.py -q` → all PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(paths): member forms dir"`

---

### Task 2: OCR word coordinates — `ocr_image_words`

**Files:**
- Modify: `.codewhale/skills/OCR/ocr.py` (after `ocr_image`, ~line 168)
- Test: `tests/test_ocr.py` (append)

**Interfaces:**
- Produces: `ocr_image_words(image_path: str) -> Optional[list[dict]]` — `[{"text","x","y","w","h"}]` in image pixels; `None` = OCR unavailable/failed. Images only (caller renders PDF pages first).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_ocr.py`:

```python
def test_ocr_image_words_item_polygon(monkeypatch, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: {"TextDetections": [
        {"DetectedText": "姓名", "ItemPolygon": {"X": 10, "Y": 20, "Width": 60, "Height": 18}},
        {"DetectedText": "", "ItemPolygon": {"X": 0, "Y": 0, "Width": 1, "Height": 1}},
    ]})
    assert ocr.ocr_image_words(str(f)) == [
        {"text": "姓名", "x": 10, "y": 20, "w": 60, "h": 18}]


def test_ocr_image_words_polygon_fallback(monkeypatch, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: {"TextDetections": [
        {"DetectedText": "地址", "Polygon": [
            {"X": 5, "Y": 8}, {"X": 105, "Y": 8}, {"X": 105, "Y": 28}, {"X": 5, "Y": 28}]},
    ]})
    assert ocr.ocr_image_words(str(f)) == [
        {"text": "地址", "x": 5, "y": 8, "w": 100, "h": 20}]


def test_ocr_image_words_unavailable(monkeypatch, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: None)
    assert ocr.ocr_image_words(str(f)) is None
    assert ocr.ocr_image_words(str(tmp_path / "missing.png")) is None
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_ocr.py -q` → new tests FAIL (`AttributeError`)
- [ ] **Step 3: Implement** — add to `ocr.py` after `ocr_image`:

```python
def ocr_image_words(image_path: str) -> Optional[list]:
    """通用识别（带坐标）。返回 [{"text","x","y","w","h"}]（图片像素坐标）；
    None = OCR 不可用/失败。仅支持图片——PDF 先由调用方逐页渲染成图。
    Form_Filler 平面表格的标签定位用。
    """
    p = Path(image_path)
    if not p.exists():
        return None
    b64 = base64.b64encode(p.read_bytes()).decode()
    data = _call_ocr({"ImageBase64": b64, "LanguageType": "zh"})
    if not data:
        return None
    words = []
    for d in data.get("TextDetections", []):
        text = d.get("DetectedText")
        if not text:
            continue
        ip = d.get("ItemPolygon") or {}
        if {"X", "Y", "Width", "Height"} <= set(ip):
            x, y, w, h = ip["X"], ip["Y"], ip["Width"], ip["Height"]
        else:
            poly = d.get("Polygon") or []
            xs = [pt.get("X", 0) for pt in poly]
            ys = [pt.get("Y", 0) for pt in poly]
            if not xs or not ys:
                continue
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
        words.append({"text": text, "x": int(x), "y": int(y), "w": int(w), "h": int(h)})
    return words
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_ocr.py -q` → PASS
- [ ] **Step 5: Commit** — `git commit -am "feat(ocr): word-level coordinates for form anchoring"`

---

### Task 3: Session store — `form_session.py`

**Files:**
- Create: `.codewhale/skills/Form_Filler/form_session.py`
- Modify: `tests/conftest.py` (add Form_Filler to sys.path, same pattern as other skill dirs)
- Test: `tests/test_form_session.py` (new)

**Interfaces:**
- Consumes: `paths.member_forms_dir` (Task 1).
- Produces (all raise `ValueError` with Chinese message on invalid input):
  - `new_session(member, source_pdf, kind, fields=None, status="collecting", pages=None) -> dict` — creates + saves; `kind` in `("acroform","flat")`; `source_pdf` is data-relative posix.
  - `load(member, session_id) -> dict` — `FileNotFoundError` if missing.
  - `save(session) -> None`
  - `list_sessions(member) -> list[dict]` — newest first.
  - `define_fields(session, fields) -> None` — flat only, status `defining`→`collecting`, validates anchors against `session["pages"]`.
  - `next_unanswered(session) -> dict | None`
  - `set_value(session, name, value) -> dict` — validates by type, saves, returns the field.
  - `progress(session) -> tuple[int, int]` — (answered, total).
  - Field dict: `{"name","label","type","options","page","anchor","value","asked"}`; `value is None` = unanswered; `value == ""` = answered-blank.
  - Session dict: `{"id","member","source_pdf","kind","status","created","pages","fields","render_path"}`; `pages` = `[{"page","image","width","height"}]` (flat only, `image` data-relative).

- [ ] **Step 1: conftest** — in `tests/conftest.py`, after the OCR/WEBREACH blocks, add:

```python
FORM_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Form_Filler"
)
sys.path.insert(0, str(FORM_DIR))
```

- [ ] **Step 2: Write the failing tests** — `tests/test_form_session.py`:

```python
# tests/test_form_session.py — Form_Filler 会话存储。
import json

import pytest

import form_session


@pytest.fixture
def data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    return tmp_path / "data"


def _mk(member="Jim", kind="acroform", fields=None, **kw):
    return form_session.new_session(
        member, "jim/inbox/2026-07/f.pdf", kind,
        fields=fields if fields is not None else [
            {"name": "family_name", "label": "姓", "type": "text"},
            {"name": "agree", "label": "同意条款", "type": "checkbox", "options": ["/Yes"]},
            {"name": "province", "label": "省份", "type": "choice", "options": ["AB", "BC"]},
        ], **kw)


class TestCRUD:
    def test_new_session_persists_and_loads(self, data_root):
        s = _mk()
        assert (data_root / "jim" / "forms" / f"{s['id']}.json").exists()
        loaded = form_session.load("Jim", s["id"])
        assert loaded == s
        assert loaded["status"] == "collecting"
        assert loaded["kind"] == "acroform"
        assert all(f["value"] is None for f in loaded["fields"])

    def test_bad_kind_rejected(self, data_root):
        with pytest.raises(ValueError):
            _mk(kind="xfa")

    def test_load_missing_raises(self, data_root):
        with pytest.raises(FileNotFoundError):
            form_session.load("Jim", "20260709_000000_dead")

    def test_load_rejects_malformed_id(self, data_root):
        # 路径遍历防线：id 不匹配格式直接拒绝
        with pytest.raises(ValueError):
            form_session.load("Jim", "../evil")

    def test_list_sessions_newest_first(self, data_root):
        a = _mk()
        b = _mk()
        ids = [s["id"] for s in form_session.list_sessions("Jim")]
        assert set(ids) == {a["id"], b["id"]}
        assert form_session.list_sessions("Wenliang") == []


class TestFields:
    def test_field_requires_known_type(self, data_root):
        with pytest.raises(ValueError):
            _mk(fields=[{"name": "x", "label": "x", "type": "date"}])

    def test_next_and_set_and_progress(self, data_root):
        s = _mk()
        f = form_session.next_unanswered(s)
        assert f["name"] == "family_name"
        form_session.set_value(s, "family_name", "Zheng")
        assert form_session.progress(s) == (1, 3)
        # 落盘了
        again = form_session.load("Jim", s["id"])
        assert again["fields"][0]["value"] == "Zheng"
        assert form_session.next_unanswered(again)["name"] == "agree"

    def test_set_blank_counts_as_answered(self, data_root):
        s = _mk()
        form_session.set_value(s, "family_name", "")
        assert form_session.progress(s)[0] == 1

    def test_checkbox_normalization(self, data_root):
        s = _mk()
        for raw, want in [("on", "on"), ("yes", "on"), ("是", "on"), ("勾", "on"),
                          ("off", "off"), ("no", "off"), ("否", "off")]:
            form_session.set_value(s, "agree", raw)
            assert form_session.load("Jim", s["id"])["fields"][1]["value"] == want
        with pytest.raises(ValueError):
            form_session.set_value(s, "agree", "maybe")

    def test_choice_must_be_in_options(self, data_root):
        s = _mk()
        form_session.set_value(s, "province", "AB")
        with pytest.raises(ValueError):
            form_session.set_value(s, "province", "ON")

    def test_set_unknown_field_raises(self, data_root):
        s = _mk()
        with pytest.raises(ValueError):
            form_session.set_value(s, "nope", "x")


class TestFlatDefine:
    def test_define_fields_validates_anchor_and_activates(self, data_root):
        s = form_session.new_session(
            "Jim", "jim/inbox/2026-07/flat.pdf", "flat", status="defining",
            pages=[{"page": 0, "image": "jim/forms/x/page_0.png",
                    "width": 1000, "height": 1400}])
        assert form_session.next_unanswered(s) is None
        form_session.define_fields(s, [
            {"name": "name", "label": "姓名", "type": "text", "page": 0,
             "anchor": {"x": 100, "y": 200, "w": 300, "h": 24}}])
        assert s["status"] == "collecting"
        assert form_session.next_unanswered(s)["name"] == "name"

    def test_define_rejects_out_of_bounds_anchor(self, data_root):
        s = form_session.new_session(
            "Jim", "jim/inbox/2026-07/flat.pdf", "flat", status="defining",
            pages=[{"page": 0, "image": "p.png", "width": 1000, "height": 1400}])
        with pytest.raises(ValueError):
            form_session.define_fields(s, [
                {"name": "n", "label": "n", "type": "text", "page": 0,
                 "anchor": {"x": 900, "y": 200, "w": 300, "h": 24}}])
        with pytest.raises(ValueError):
            form_session.define_fields(s, [
                {"name": "n", "label": "n", "type": "text", "page": 5,
                 "anchor": {"x": 1, "y": 1, "w": 5, "h": 5}}])

    def test_define_only_when_defining(self, data_root):
        s = _mk()
        with pytest.raises(ValueError):
            form_session.define_fields(s, [])
```

- [ ] **Step 3: Run** — `python -m pytest tests/test_form_session.py -q` → FAIL (`ModuleNotFoundError: form_session`)
- [ ] **Step 4: Implement** — `.codewhale/skills/Form_Filler/form_session.py`:

```python
"""
Family Assistant — 填表会话存储（Form_Filler skill）

会话 = 一次"帮我填 PDF 表"的全过程状态，JSON 落盘 data/<成员>/forms/<id>.json。
一字段一问的 ping-pong 跨消息 /clear/重启不丢：每答必写盘，恢复时读盘。

会话/字段 schema 见 docs/superpowers/specs/2026-07-09-pdf-form-fill-design.md。
成员隔离靠目录：路径永远经 paths.member_forms_dir(member)，调用方
（agent_core）注入已解析成员名。
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import paths as _paths

FIELD_TYPES = ("text", "checkbox", "choice")
KINDS = ("acroform", "flat")
STATUSES = ("defining", "collecting", "done", "cancelled")
_ID_RE = re.compile(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{4}")

# checkbox 用户口语 → 规范值
_CHECK_ON = {"on", "yes", "true", "1", "是", "勾", "勾选", "选", "√", "✓", "对"}
_CHECK_OFF = {"off", "no", "false", "0", "否", "不勾", "不选", "不", "×"}


def _session_path(member: str, session_id: str) -> Path:
    if not _ID_RE.fullmatch(session_id or ""):
        raise ValueError(f"非法会话 id: {session_id}")
    return _paths.member_forms_dir(member) / f"{session_id}.json"


def _norm_field(f: dict) -> dict:
    name = str(f.get("name") or "").strip()
    if not name:
        raise ValueError("字段缺少 name")
    ftype = f.get("type") or "text"
    if ftype not in FIELD_TYPES:
        raise ValueError(f"字段 {name} 类型必须是 {FIELD_TYPES}，收到: {ftype}")
    options = [str(o) for o in (f.get("options") or [])]
    if ftype == "choice" and not options:
        raise ValueError(f"choice 字段 {name} 缺少 options")
    anchor = f.get("anchor")
    if anchor is not None:
        try:
            anchor = {k: float(anchor[k]) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"字段 {name} 的 anchor 需要数值 x/y/w/h")
        if any(v < 0 for v in anchor.values()) or anchor["w"] <= 0 or anchor["h"] <= 0:
            raise ValueError(f"字段 {name} 的 anchor 数值非法")
    return {"name": name, "label": str(f.get("label") or name),
            "type": ftype, "options": options,
            "page": int(f.get("page") or 0), "anchor": anchor,
            "value": None, "asked": False}


def new_session(member: str, source_pdf: str, kind: str,
                fields: list | None = None, status: str = "collecting",
                pages: list | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {KINDS}，收到: {kind}")
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {STATUSES}，收到: {status}")
    now = datetime.now()
    session = {
        "id": now.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4],
        "member": member,
        "source_pdf": source_pdf,
        "kind": kind,
        "status": status,
        "created": now.isoformat(timespec="seconds"),
        "pages": pages or [],
        "fields": [_norm_field(f) for f in (fields or [])],
        "render_path": None,
    }
    save(session)
    return session


def load(member: str, session_id: str) -> dict:
    p = _session_path(member, session_id)
    if not p.exists():
        raise FileNotFoundError(f"会话不存在: {session_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(session: dict) -> None:
    p = _session_path(session["member"], session["id"])
    p.write_text(json.dumps(session, ensure_ascii=False, indent=1),
                 encoding="utf-8")


def list_sessions(member: str) -> list:
    out = []
    for p in _paths.member_forms_dir(member).glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(out, key=lambda s: s.get("id", ""), reverse=True)


def define_fields(session: dict, fields: list) -> None:
    """平面表：LLM 从 OCR 布局推断出的字段定义写入会话，锚点须落在页面内。"""
    if session.get("status") != "defining":
        raise ValueError("仅 defining 状态的平面表会话可定义字段")
    dims = {int(p["page"]): (float(p["width"]), float(p["height"]))
            for p in session.get("pages", [])}
    normed = []
    for f in fields:
        nf = _norm_field(f)
        if nf["anchor"] is None:
            raise ValueError(f"平面表字段 {nf['name']} 必须带 anchor")
        if nf["page"] not in dims:
            raise ValueError(f"字段 {nf['name']} 的 page {nf['page']} 不存在")
        w, h = dims[nf["page"]]
        a = nf["anchor"]
        if a["x"] + a["w"] > w or a["y"] + a["h"] > h:
            raise ValueError(f"字段 {nf['name']} 的 anchor 超出页面范围")
        normed.append(nf)
    session["fields"] = normed
    session["status"] = "collecting"
    save(session)


def next_unanswered(session: dict) -> dict | None:
    for f in session.get("fields", []):
        if f.get("value") is None:
            return f
    return None


def set_value(session: dict, name: str, value: str) -> dict:
    for f in session.get("fields", []):
        if f["name"] == name:
            f["value"] = _validate_value(f, value)
            save(session)
            return f
    raise ValueError(f"字段不存在: {name}")


def _validate_value(field: dict, value: str) -> str:
    value = "" if value is None else str(value).strip()
    if value == "":                     # 显式留空
        return ""
    if field["type"] == "checkbox":
        low = value.lower()
        if low in _CHECK_ON:
            return "on"
        if low in _CHECK_OFF:
            return "off"
        raise ValueError(f"勾选框 {field['name']} 只接受 是/否（on/off），收到: {value}")
    if field["type"] == "choice" and value not in field["options"]:
        raise ValueError(
            f"{field['name']} 必须从选项中选: {' / '.join(field['options'])}，收到: {value}")
    return value


def progress(session: dict) -> tuple:
    fields = session.get("fields", [])
    return (sum(1 for f in fields if f.get("value") is not None), len(fields))
```

- [ ] **Step 5: Run** — `python -m pytest tests/test_form_session.py -q` → PASS
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(form): session store for form fill ping-pong"`

---

### Task 4: AcroForm scan + fill — `form_fill.py`

**Files:**
- Create: `.codewhale/skills/Form_Filler/form_fill.py`
- Test: `tests/test_form_fill.py` (new)

**Interfaces:**
- Produces:
  - `is_available() -> bool` — pypdf importable.
  - `scan_fields(pdf_path: str) -> list[dict]` — `[{"name","label","type","options"}]`; `[]` = no AcroForm fields (flat candidate); raises `ValueError("PDF 已加密，无法读取")` on undecryptable PDF.
  - `render(pdf_path: str, values: dict[str, str], out_path: str) -> None` — checkbox values are `"on"`/`"off"`; blank/None skipped.
- Consumed by: Task 6 CLI.

- [ ] **Step 1: Install dev dep** — `python -m pip install pypdf` (runtime keeps it optional; tests skip without it).
- [ ] **Step 2: Write the failing tests** — `tests/test_form_fill.py`:

```python
# tests/test_form_fill.py — AcroForm 扫描与填写（pypdf 缺席则跳过）。
import pytest

pypdf = pytest.importorskip("pypdf")

import form_fill
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                           NumberObject, TextStringObject)


@pytest.fixture
def acro_pdf(tmp_path):
    """最小 AcroForm：一个文本域 + 一个勾选框。"""
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.add_annotation(0, _text_field("family_name", 100, 700))
    w.add_annotation(0, _checkbox("agree", 100, 650))
    # add_annotation 不建 /AcroForm —— 手动挂 AcroForm 指向两个 widget
    page = w.pages[0]
    fields = ArrayObject(page["/Annots"])
    acro = DictionaryObject({NameObject("/Fields"): fields})
    w._root_object[NameObject("/AcroForm")] = acro
    p = tmp_path / "form.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    return str(p)


def _text_field(name, x, y):
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/Rect"): ArrayObject(
            [NumberObject(x), NumberObject(y), NumberObject(x + 200), NumberObject(y + 20)]),
    })


def _checkbox(name, x, y):
    off = DictionaryObject()
    on = DictionaryObject()
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/V"): NameObject("/Off"),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
            NameObject("/Yes"): on, NameObject("/Off"): off})}),
        NameObject("/Rect"): ArrayObject(
            [NumberObject(x), NumberObject(y), NumberObject(x + 15), NumberObject(y + 15)]),
    })


def test_scan_fields(acro_pdf):
    fields = form_fill.scan_fields(acro_pdf)
    by_name = {f["name"]: f for f in fields}
    assert by_name["family_name"]["type"] == "text"
    cb = by_name["agree"]
    assert cb["type"] == "checkbox"
    assert cb["options"] == ["/Yes"]


def test_scan_fields_flat_pdf_returns_empty(tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    p = tmp_path / "flat.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    assert form_fill.scan_fields(str(p)) == []


def test_render_fills_values(acro_pdf, tmp_path):
    out = tmp_path / "filled.pdf"
    form_fill.render(acro_pdf, {"family_name": "Zheng", "agree": "on"}, str(out))
    got = PdfReader(str(out)).get_fields()
    assert got["family_name"]["/V"] == "Zheng"
    assert got["agree"]["/V"] == "/Yes"


def test_render_skips_blank(acro_pdf, tmp_path):
    out = tmp_path / "filled.pdf"
    form_fill.render(acro_pdf, {"family_name": "", "agree": "off"}, str(out))
    got = PdfReader(str(out)).get_fields()
    assert not got["family_name"].get("/V")
    assert got["agree"]["/V"] == "/Off"


def test_is_available_true_here():
    assert form_fill.is_available()
```

- [ ] **Step 3: Run** — `python -m pytest tests/test_form_fill.py -q` → FAIL (`ModuleNotFoundError: form_fill`)
- [ ] **Step 4: Implement** — `.codewhale/skills/Form_Filler/form_fill.py`:

```python
"""
Family Assistant — AcroForm（可填写 PDF）扫描与填写（pypdf）。

依赖 pypdf（可选依赖）：缺席时 is_available()=False，调用方（cli.py）
给 pip install 提示，绝不崩溃。输出保持矢量（tier 1）。
"""

from __future__ import annotations


def _import_pypdf():
    try:
        from pypdf import PdfReader, PdfWriter
        return PdfReader, PdfWriter
    except ImportError:
        return None


def is_available() -> bool:
    return _import_pypdf() is not None


def _open(pdf_path: str):
    PdfReader, _ = _import_pypdf()
    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("PDF 已加密，无法读取")
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法读取")
    return reader


def _on_state(field) -> str:
    states = [str(s) for s in (field.get("/_States_") or []) if str(s) != "/Off"]
    return states[0] if states else "/Yes"


def scan_fields(pdf_path: str) -> list:
    """读 AcroForm 字段 → [{"name","label","type","options"}]。

    [] = 无表单字段（平面表候选）。签名域（/Sig）跳过。
    加密不可解 → ValueError("PDF 已加密，无法读取")。
    """
    reader = _open(pdf_path)
    raw = reader.get_fields() or {}
    fields = []
    for name, f in raw.items():
        ft = str(f.get("/FT") or "")
        if ft == "/Tx":
            fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                           "type": "text", "options": []})
        elif ft == "/Btn":
            fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                           "type": "checkbox", "options": [_on_state(f)]})
        elif ft == "/Ch":
            opts = [str(o) for o in (f.get("/Opt") or [])]
            fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                           "type": "choice", "options": opts})
    return fields


def render(pdf_path: str, values: dict, out_path: str) -> None:
    """填值输出。values: 字段名→字符串；checkbox 用 "on"/"off"；空值跳过。

    NeedAppearances=True 让阅读器重算外观，填的值才会显示。
    """
    _, PdfWriter = _import_pypdf()
    reader = _open(pdf_path)
    raw = reader.get_fields() or {}
    fill = {}
    for name, v in values.items():
        f = raw.get(name)
        if f is None or v in (None, ""):
            continue
        if str(f.get("/FT") or "") == "/Btn":
            fill[name] = _on_state(f) if v == "on" else "/Off"
        else:
            fill[name] = str(v)
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        if "/Annots" in page:
            writer.update_page_form_field_values(page, fill, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
```

Note: if `writer.set_need_appearances_writer` doesn't exist in the installed pypdf version, fall back to setting it manually:

```python
    try:
        writer.set_need_appearances_writer(True)
    except AttributeError:
        from pypdf.generic import BooleanObject, NameObject
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
```

If the `acro_pdf` fixture construction fails on the installed pypdf version (private `_root_object` API), adapt the fixture (e.g. `w.root_object`), not the implementation.

- [ ] **Step 5: Run** — `python -m pytest tests/test_form_fill.py -q` → PASS
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(form): acroform scan and fill via pypdf"`

---

### Task 5: Flat PDF overlay — `form_overlay.py`

**Files:**
- Create: `.codewhale/skills/Form_Filler/form_overlay.py`
- Test: `tests/test_form_overlay.py` (new)

**Interfaces:**
- Produces:
  - `is_available() -> bool` — pypdfium2 + Pillow importable.
  - `MAX_FLAT_PAGES = 6`, `RENDER_SCALE = 2.0`, `MIN_FONT_PX = 8`
  - `render_pages(pdf_path: str, out_dir: str) -> list[dict]` — `[{"page": int, "image": str(abs path), "width": int, "height": int}]`, capped at MAX_FLAT_PAGES.
  - `stamp(pages: list[dict], fields: list[dict], out_pdf: str) -> list[str]` — draws answered fields (session field dicts, `image` paths absolute) onto page images, writes multipage PDF, returns warning strings (truncations).
- Consumed by: Task 6 CLI.

- [ ] **Step 1: Install dev deps** — `python -m pip install pypdfium2 Pillow`
- [ ] **Step 2: Write the failing tests** — `tests/test_form_overlay.py`:

```python
# tests/test_form_overlay.py — 平面 PDF 渲染与盖字（缺依赖跳过）。
import pytest

pytest.importorskip("pypdfium2")
PIL = pytest.importorskip("PIL")

import form_overlay
from PIL import Image


@pytest.fixture
def flat_pdf(tmp_path):
    """一页空白 PDF（用 Pillow 生成，不依赖 pypdf）。"""
    img = Image.new("RGB", (612, 792), "white")
    p = tmp_path / "flat.pdf"
    img.save(p, "PDF")
    return str(p)


def test_render_pages(flat_pdf, tmp_path):
    pages = form_overlay.render_pages(flat_pdf, str(tmp_path / "out"))
    assert len(pages) == 1
    pg = pages[0]
    assert pg["page"] == 0
    im = Image.open(pg["image"])
    assert (im.width, im.height) == (pg["width"], pg["height"])
    assert pg["width"] > 612  # scale 2.0 放大


def test_render_pages_caps_at_max(tmp_path):
    imgs = [Image.new("RGB", (100, 100), "white") for _ in range(8)]
    p = tmp_path / "big.pdf"
    imgs[0].save(p, "PDF", save_all=True, append_images=imgs[1:])
    pages = form_overlay.render_pages(str(p), str(tmp_path / "out"))
    assert len(pages) == form_overlay.MAX_FLAT_PAGES


def _page_fixture(tmp_path, w=800, h=600):
    img = Image.new("RGB", (w, h), "white")
    p = tmp_path / "page_0.png"
    img.save(p)
    return [{"page": 0, "image": str(p), "width": w, "height": h}]


def test_stamp_draws_text_and_outputs_pdf(tmp_path):
    pages = _page_fixture(tmp_path)
    fields = [
        {"name": "n", "label": "姓名", "type": "text", "options": [], "page": 0,
         "anchor": {"x": 100, "y": 100, "w": 300, "h": 24}, "value": "Zheng", "asked": True},
        {"name": "skip", "label": "留空", "type": "text", "options": [], "page": 0,
         "anchor": {"x": 100, "y": 200, "w": 300, "h": 24}, "value": "", "asked": True},
        {"name": "cb", "label": "勾", "type": "checkbox", "options": [], "page": 0,
         "anchor": {"x": 100, "y": 300, "w": 24, "h": 24}, "value": "on", "asked": True},
    ]
    out = tmp_path / "filled.pdf"
    warnings = form_overlay.stamp(pages, fields, str(out))
    assert warnings == []
    assert out.exists() and out.stat().st_size > 0
    # 盖字后与原白页不同
    stamped = Image.open(pages[0]["image"]).convert("RGB")
    region = stamped.crop((100, 100, 400, 124))
    assert region.getextrema() != ((255, 255), (255, 255), (255, 255))


def test_stamp_truncates_overlong_value(tmp_path):
    pages = _page_fixture(tmp_path)
    fields = [{"name": "n", "label": "n", "type": "text", "options": [], "page": 0,
               "anchor": {"x": 10, "y": 10, "w": 40, "h": 12},
               "value": "a-very-long-value-that-cannot-fit", "asked": True}]
    warnings = form_overlay.stamp(pages, fields, str(tmp_path / "o.pdf"))
    assert warnings and "截断" in warnings[0]
```

Note: `stamp` mutates the page image files in place (draws on them) before assembling the PDF — the test relies on that; keep it (the images are session-scoped scratch copies, not user files).

- [ ] **Step 3: Run** — `python -m pytest tests/test_form_overlay.py -q` → FAIL (`ModuleNotFoundError: form_overlay`)
- [ ] **Step 4: Implement** — `.codewhale/skills/Form_Filler/form_overlay.py`:

```python
"""
Family Assistant — 平面/扫描 PDF 盖字（tier 2）。

无 AcroForm 字段的 PDF：pypdfium2 逐页渲染成图 → （调用方经 OCR+LLM 定字段
锚点）→ Pillow 把答案画在锚点处 → 重组多页 PDF。输出是栅格（输入本就是扫描件）。

依赖 pypdfium2 + Pillow（可选）：缺席时 is_available()=False，cli 层给安装提示。
"""

from __future__ import annotations

from pathlib import Path

MAX_FLAT_PAGES = 6      # 平面表页数上限（渲染+OCR 逐页花时间/额度，表格通常很短）
RENDER_SCALE = 2.0      # 渲染放大倍数（OCR 与盖字共用同一坐标系）
MIN_FONT_PX = 8         # 缩字下限，再放不下就截断

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑（中文）
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def is_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def render_pages(pdf_path: str, out_dir: str) -> list:
    """逐页渲染 PNG（上限 MAX_FLAT_PAGES）→ [{"page","image","width","height"}]。"""
    import pypdfium2 as pdfium
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    pages = []
    try:
        for i in range(min(len(pdf), MAX_FLAT_PAGES)):
            pil = pdf[i].render(scale=RENDER_SCALE).to_pil().convert("RGB")
            p = out / f"page_{i}.png"
            pil.save(p)
            pages.append({"page": i, "image": str(p),
                          "width": pil.width, "height": pil.height})
    finally:
        pdf.close()
    return pages


def _load_font(px: int):
    from PIL import ImageFont
    for cand in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, px)
        except OSError:
            continue
    return ImageFont.load_default()


def stamp(pages: list, fields: list, out_pdf: str) -> list:
    """把已答字段画到页面图上（就地改图），输出多页 PDF。返回警告（截断等）。

    checkbox：on 画 ✓，off 不画。文本：先按锚高取字号，放不下逐级缩到
    MIN_FONT_PX，仍放不下则截断加 …。
    """
    from PIL import Image, ImageDraw
    warnings = []
    by_page: dict = {}
    for f in fields:
        if f.get("value") and f.get("anchor"):
            by_page.setdefault(int(f.get("page", 0)), []).append(f)
    images = []
    for pg in pages:
        img = Image.open(pg["image"]).convert("RGB")
        draw = ImageDraw.Draw(img)
        for f in by_page.get(int(pg["page"]), []):
            if f["type"] == "checkbox":
                if f["value"] != "on":
                    continue
                text = "✓"
            else:
                text = str(f["value"])
            a = f["anchor"]
            px = max(int(a["h"] * 0.8), MIN_FONT_PX)
            font = _load_font(px)
            while px > MIN_FONT_PX and draw.textlength(text, font=font) > a["w"]:
                px -= 1
                font = _load_font(px)
            if draw.textlength(text, font=font) > a["w"]:
                while text and draw.textlength(text + "…", font=font) > a["w"]:
                    text = text[:-1]
                text += "…"
                warnings.append(f"{f['name']}: 值过长已截断")
            draw.text((a["x"], a["y"]), text, font=font, fill=(0, 0, 0))
        img.save(pg["image"])
        images.append(img)
    images[0].save(out_pdf, "PDF", save_all=True, append_images=images[1:],
                   resolution=72 * RENDER_SCALE)
    return warnings
```

- [ ] **Step 5: Run** — `python -m pytest tests/test_form_overlay.py -q` → PASS
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(form): flat pdf render and stamp overlay"`

---

### Task 6: CLI — `cli.py`

**Files:**
- Create: `.codewhale/skills/Form_Filler/cli.py`
- Test: `tests/test_form_cli.py` (new)

**Interfaces:**
- Consumes: `form_session`, `form_fill`, `form_overlay`, `paths`, `ocr.ocr_image_words` + `ocr.is_available`.
- Produces subcommands (all take `--member`, required):
  - `form-scan --file <path> --member <m>` — path gate → AcroForm scan → session; or flat flow (render + OCR, prints OCR words per page). stdout formats below.
  - `form-define --session <id> --fields <json-array> --member <m>`
  - `form-next --session <id> --member <m>`
  - `form-set --session <id> --field <name> --value <v> --member <m>` (`--value` may be empty = leave blank)
  - `form-render --session <id> --member <m>` — **stdout line 1 = data-relative path of filled PDF** (sentinel contract), following lines = `警告: …`.
  - `form-list --member <m>`
  - `form-cancel --session <id> --member <m>`
- Errors: print `[错误] …` to stdout, `sys.exit(1)`.

**stdout formats (exact):**

`form-scan` acroform:
```
会话: <id>
类型: acroform（可填写 PDF）
字段 (<N>):
1. family_name | 姓 | text
2. agree | 同意条款 | checkbox
3. province | 省份 | choice[AB / BC]
用 fill_form_next 逐个提问。
```

`form-scan` flat:
```
会话: <id>
类型: flat（平面/扫描 PDF，共 <N> 页）
下面是每页 OCR 文本及坐标（页面像素，页宽x高见页头）。
请根据布局推断需要填写的字段（标签 + 填写区域锚点 anchor，锚点是标签右侧
或下方的空白区），然后调 fill_form_define_fields 提交字段定义。
--- 第 1 页 1224x1584 ---
[x=100,y=200,w=60,h=20] 姓名
[x=100,y=260,w=90,h=20] 出生日期
```

`form-next`: `下一个字段: <name> | <label> | <type>` (+ `，选项: A / B` for choice, + `，回答 是/否` for checkbox); when none left: `全部字段已回答 (<n>/<n>)。可调 fill_form_render 生成 PDF。`; cancelled/done session: `[错误] 会话状态是 <status>`.

`form-set`: `✅ <name> = <value>（进度 <a>/<n>）`; blank value: `✅ <name> = （留空）（进度 <a>/<n>）`.

`form-list`: one line per session: `<id> | <status> | <kind> | 进度 <a>/<n> | <created>`; none: `没有填表会话。`

`form-cancel`: `已取消会话 <id>。`

- [ ] **Step 1: Write the failing tests** — `tests/test_form_cli.py`:

```python
# tests/test_form_cli.py — Form_Filler CLI（子进程，DATA_ROOT 隔离）。
import json
import os
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

pytest.importorskip("pypdf")

_CLI = str(_Path(__file__).resolve().parent.parent
           / ".codewhale" / "skills" / "Form_Filler" / "cli.py")


def _run(*args, data_root):
    env = {**os.environ, "DATA_ROOT": str(data_root)}
    return subprocess.run([_sys.executable, _CLI, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


@pytest.fixture
def data_root(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def inbox_pdf(data_root, tmp_path):
    """member inbox 里的最小 AcroForm PDF（借用 test_form_fill 的构造）。"""
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from test_form_fill import _text_field, _checkbox
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.add_annotation(0, _text_field("family_name", 100, 700))
    w.add_annotation(0, _checkbox("agree", 100, 650))
    page = w.pages[0]
    acro = DictionaryObject({NameObject("/Fields"): ArrayObject(page["/Annots"])})
    w._root_object[NameObject("/AcroForm")] = acro
    d = data_root / "jim" / "inbox" / "2026-07"
    d.mkdir(parents=True)
    p = d / "form.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    return p


def _sid(scan_stdout):
    return scan_stdout.splitlines()[0].split("会话:")[1].strip()


def test_scan_next_set_render_roundtrip(data_root, inbox_pdf):
    r = _run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "acroform" in r.stdout
    assert "family_name" in r.stdout
    sid = _sid(r.stdout)

    r = _run("form-next", "--session", sid, "--member", "Jim", data_root=data_root)
    assert "family_name" in r.stdout

    r = _run("form-set", "--session", sid, "--field", "family_name",
             "--value", "Zheng", "--member", "Jim", data_root=data_root)
    assert "✅" in r.stdout and "1/2" in r.stdout

    r = _run("form-set", "--session", sid, "--field", "agree",
             "--value", "是", "--member", "Jim", data_root=data_root)
    assert "2/2" in r.stdout

    r = _run("form-next", "--session", sid, "--member", "Jim", data_root=data_root)
    assert "全部字段已回答" in r.stdout

    r = _run("form-render", "--session", sid, "--member", "Jim", data_root=data_root)
    assert r.returncode == 0, r.stdout + r.stderr
    rel = r.stdout.splitlines()[0].strip()
    assert rel == f"jim/forms/{sid}_filled.pdf"
    assert (data_root / rel).exists()
    from pypdf import PdfReader
    got = PdfReader(str(data_root / rel)).get_fields()
    assert got["family_name"]["/V"] == "Zheng"
    assert got["agree"]["/V"] == "/Yes"


def test_scan_rejects_foreign_member_path(data_root, inbox_pdf):
    r = _run("form-scan", "--file", str(inbox_pdf), "--member", "Wenliang",
             data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout


def test_scan_rejects_outside_data_root(data_root, tmp_path):
    outside = tmp_path / "x.pdf"
    outside.write_bytes(b"%PDF")
    r = _run("form-scan", "--file", str(outside), "--member", "Jim",
             data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout


def test_set_invalid_choice_reports_error(data_root, inbox_pdf):
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    r = _run("form-set", "--session", sid, "--field", "agree",
             "--value", "maybe", "--member", "Jim", data_root=data_root)
    assert r.returncode == 1
    assert "[错误]" in r.stdout


def test_list_and_cancel(data_root, inbox_pdf):
    sid = _sid(_run("form-scan", "--file", str(inbox_pdf), "--member", "Jim",
                    data_root=data_root).stdout)
    r = _run("form-list", "--member", "Jim", data_root=data_root)
    assert sid in r.stdout and "collecting" in r.stdout
    r = _run("form-cancel", "--session", sid, "--member", "Jim",
             data_root=data_root)
    assert "已取消" in r.stdout
    r = _run("form-next", "--session", sid, "--member", "Jim", data_root=data_root)
    assert r.returncode == 1 and "[错误]" in r.stdout


def test_flat_scan_without_ocr_reports_hint(data_root, monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("pypdfium2")
    from PIL import Image
    d = data_root / "jim" / "inbox" / "2026-07"
    d.mkdir(parents=True)
    p = d / "flat.pdf"
    Image.new("RGB", (612, 792), "white").save(p, "PDF")
    env_no_ocr = {"TENCENT_SECRET_ID": "", "TENCENT_SECRET_KEY": ""}
    env = {**os.environ, **env_no_ocr, "DATA_ROOT": str(data_root)}
    r = subprocess.run([_sys.executable, _CLI, "form-scan", "--file", str(p),
                        "--member", "Jim"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    assert r.returncode == 1
    assert "OCR" in r.stdout
```

Note: check `.codewhale/skills/OCR/ocr.py` for the exact env var names used by `is_available()` (likely `TENCENT_SECRET_ID`/`TENCENT_SECRET_KEY` — read lines around `def is_available`) and adjust `env_no_ocr` accordingly.

- [ ] **Step 2: Run** — `python -m pytest tests/test_form_cli.py -q` → FAIL (CLI missing)
- [ ] **Step 3: Implement** — `.codewhale/skills/Form_Filler/cli.py`:

```python
"""
Family Assistant — Form Filler CLI（PDF 表格填写）

Agent 经白名单子命令调用，输出纯文本。一次填表 = 一个会话
（data/<成员>/forms/<id>.json），一字段一问跨消息不丢。

用法: python .codewhale/skills/Form_Filler/cli.py <command> [args]

依赖（可选，缺席优雅降级）:
    pypdf      — AcroForm 可填写 PDF（tier 1）
    pypdfium2  — 平面 PDF 逐页渲染（tier 2）
    Pillow     — 盖字 + 重组 PDF（tier 2）
    腾讯云 OCR — 平面表标签坐标（OCR skill）
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Windows 控制台编码容错
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "OCR"))

import form_fill
import form_overlay
import form_session
import paths as _paths


def _die(msg: str):
    print(f"[错误] {msg}")
    sys.exit(1)


def _resolve_source(path: str, member: str) -> Path:
    """来源 PDF 闸门：须存在、在 data_root 内、且属本成员目录或家庭共享目录。
    与 agent_core._resolve_sendable 同一套规则（防 LLM 跨成员读文件）。"""
    try:
        p = Path(path)
        ap = (p if p.is_absolute() else _paths.resolve_rel(str(p))).resolve()
        root = _paths.data_root().resolve()
        if not (ap.exists() and ap.is_file() and ap.is_relative_to(root)):
            _die("路径不允许或文件不存在")
        allowed = [_paths.family_dir().resolve(), _paths.member_dir(member).resolve()]
        if not any(ap.is_relative_to(a) for a in allowed):
            _die("路径不允许或文件不存在")
        return ap
    except (ValueError, OSError):
        _die("路径不允许或文件不存在")


def _load_session(args) -> dict:
    try:
        s = form_session.load(args.member, args.session)
    except FileNotFoundError as e:
        _die(str(e))
    except ValueError as e:
        _die(str(e))
    return s


def _mark_backup_dirty() -> None:
    """写入后通知备份引擎（失败静默，绝不影响写入本身）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Remote_Backup"))
        from backup_sync import mark_dirty
        mark_dirty()
    except Exception:
        pass


def _fmt_field_line(i: int, f: dict) -> str:
    t = f["type"]
    if t == "choice":
        t = f"choice[{' / '.join(f['options'])}]"
    return f"{i}. {f['name']} | {f['label']} | {t}"


def cmd_form_scan(args):
    src = _resolve_source(args.file, args.member)
    if not form_fill.is_available():
        _die("缺少 pypdf 依赖，无法读取 PDF 表单。pip install pypdf")
    try:
        fields = form_fill.scan_fields(str(src))
    except ValueError as e:
        _die(str(e))
    except Exception as e:
        _die(f"PDF 解析失败: {e}")
    rel_src = _paths.to_rel(src)

    if fields:
        s = form_session.new_session(args.member, rel_src, "acroform",
                                     fields=fields)
        _mark_backup_dirty()
        print(f"会话: {s['id']}")
        print("类型: acroform（可填写 PDF）")
        print(f"字段 ({len(fields)}):")
        for i, f in enumerate(s["fields"], 1):
            print(_fmt_field_line(i, f))
        print("用 fill_form_next 逐个提问。")
        return

    # ── 平面/扫描 PDF（tier 2）──
    if not form_overlay.is_available():
        _die("此 PDF 没有可填写字段（平面/扫描表格），需要 pypdfium2+Pillow。"
             "pip install pypdfium2 Pillow")
    import ocr as _ocr
    if not _ocr.is_available():
        _die("此 PDF 没有可填写字段（平面/扫描表格），需要腾讯云 OCR 定位标签。"
             "请配置 TENCENT_SECRET_ID/TENCENT_SECRET_KEY")
    s = form_session.new_session(args.member, rel_src, "flat", status="defining")
    pages_dir = _paths.member_forms_dir(args.member) / f"{s['id']}_pages"
    try:
        pages = form_overlay.render_pages(str(src), str(pages_dir))
    except Exception as e:
        _die(f"PDF 渲染失败: {e}")
    if not pages:
        _die("PDF 没有可渲染页面")
    lines = []
    for pg in pages:
        words = _ocr.ocr_image_words(pg["image"])
        if words is None:
            _die("OCR 识别失败（额度/网络），稍后再试")
        lines.append(f"--- 第 {pg['page'] + 1} 页 {pg['width']}x{pg['height']} ---")
        for w in words:
            lines.append(f"[x={w['x']},y={w['y']},w={w['w']},h={w['h']}] {w['text']}")
        pg["image"] = _paths.to_rel(pg["image"])   # 会话里存 data 相对路径
    s["pages"] = pages
    form_session.save(s)
    _mark_backup_dirty()
    print(f"会话: {s['id']}")
    print(f"类型: flat（平面/扫描 PDF，共 {len(pages)} 页）")
    print("下面是每页 OCR 文本及坐标（页面像素，页宽x高见页头）。")
    print("请根据布局推断需要填写的字段（标签 + 填写区域锚点 anchor，锚点是标签右侧")
    print("或下方的空白区），然后调 fill_form_define_fields 提交字段定义。")
    print("\n".join(lines))


def cmd_form_define(args):
    s = _load_session(args)
    try:
        fields = json.loads(args.fields)
        if not isinstance(fields, list) or not fields:
            _die("fields 必须是非空 JSON 数组")
        form_session.define_fields(s, fields)
    except json.JSONDecodeError as e:
        _die(f"fields 不是合法 JSON: {e}")
    except ValueError as e:
        _die(str(e))
    _mark_backup_dirty()
    print(f"已定义 {len(s['fields'])} 个字段:")
    for i, f in enumerate(s["fields"], 1):
        print(_fmt_field_line(i, f))
    print("用 fill_form_next 逐个提问。")


def cmd_form_next(args):
    s = _load_session(args)
    if s["status"] not in ("collecting",):
        _die(f"会话状态是 {s['status']}")
    f = form_session.next_unanswered(s)
    if f is None:
        a, n = form_session.progress(s)
        print(f"全部字段已回答 ({a}/{n})。可调 fill_form_render 生成 PDF。")
        return
    extra = ""
    if f["type"] == "choice":
        extra = f"，选项: {' / '.join(f['options'])}"
    elif f["type"] == "checkbox":
        extra = "，回答 是/否"
    print(f"下一个字段: {f['name']} | {f['label']} | {f['type']}{extra}")


def cmd_form_set(args):
    s = _load_session(args)
    if s["status"] != "collecting":
        _die(f"会话状态是 {s['status']}")
    try:
        f = form_session.set_value(s, args.field, args.value)
    except ValueError as e:
        _die(str(e))
    _mark_backup_dirty()
    a, n = form_session.progress(s)
    shown = f["value"] if f["value"] != "" else "（留空）"
    print(f"✅ {f['name']} = {shown}（进度 {a}/{n}）")


def cmd_form_render(args):
    s = _load_session(args)
    if s["status"] == "cancelled":
        _die("会话已取消")
    values = {f["name"]: f["value"] for f in s["fields"]
              if f["value"] is not None}
    if not values:
        _die("还没有任何已回答字段")
    out = _paths.member_forms_dir(args.member) / f"{s['id']}_filled.pdf"
    warnings = []
    if s["kind"] == "acroform":
        if not form_fill.is_available():
            _die("缺少 pypdf 依赖。pip install pypdf")
        src = _paths.resolve_rel(s["source_pdf"])
        if not src.exists():
            _die(f"原始 PDF 不存在: {s['source_pdf']}")
        try:
            form_fill.render(str(src), values, str(out))
        except ValueError as e:
            _die(str(e))
    else:
        if not form_overlay.is_available():
            _die("缺少 pypdfium2/Pillow 依赖。pip install pypdfium2 Pillow")
        pages = [dict(p, image=str(_paths.resolve_rel(p["image"])))
                 for p in s["pages"]]
        missing = [p["image"] for p in pages if not Path(p["image"]).exists()]
        if missing:
            _die("页面图缺失，请重新 form-scan")
        warnings = form_overlay.stamp(pages, s["fields"], str(out))
    s["status"] = "done"
    s["render_path"] = _paths.to_rel(out)
    form_session.save(s)
    _mark_backup_dirty()
    print(s["render_path"])                      # 第一行 = 路径（哨兵契约）
    for w in warnings:
        print(f"警告: {w}")


def cmd_form_list(args):
    sessions = form_session.list_sessions(args.member)
    if not sessions:
        print("没有填表会话。")
        return
    for s in sessions:
        a, n = form_session.progress(s)
        print(f"{s['id']} | {s['status']} | {s['kind']} | 进度 {a}/{n} | {s['created']}")


def cmd_form_cancel(args):
    s = _load_session(args)
    s["status"] = "cancelled"
    form_session.save(s)
    _mark_backup_dirty()
    print(f"已取消会话 {s['id']}。")


def main() -> int:
    ap = argparse.ArgumentParser(prog="form", description="Family Assistant Form Filler")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("form-scan", help="识别 PDF 表格字段并建会话")
    p.add_argument("--file", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_scan)

    p = sub.add_parser("form-define", help="平面表：提交 LLM 推断的字段定义")
    p.add_argument("--session", required=True)
    p.add_argument("--fields", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_define)

    p = sub.add_parser("form-next", help="下一个未回答字段")
    p.add_argument("--session", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_next)

    p = sub.add_parser("form-set", help="记录一个字段的答案")
    p.add_argument("--session", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_set)

    p = sub.add_parser("form-render", help="生成填好的 PDF")
    p.add_argument("--session", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_render)

    p = sub.add_parser("form-list", help="列出填表会话")
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_list)

    p = sub.add_parser("form-cancel", help="取消填表会话")
    p.add_argument("--session", required=True)
    p.add_argument("--member", required=True)
    p.set_defaults(func=cmd_form_cancel)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_form_cli.py -q` → PASS; then full suite `python -m pytest tests/ -q` → PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(form): form filler cli"`

---

### Task 7: Agent integration — tools, schemas, timeouts, prompt

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_form_agent.py` (new)

**Interfaces:**
- Consumes: Task 6 CLI subcommands.
- Produces LLM tools: `fill_form_scan(file)`, `fill_form_define_fields(session, fields)`, `fill_form_next(session)`, `fill_form_set_answer(session, field, value)`, `fill_form_render(session)`, `fill_form_list()`, `fill_form_cancel(session)` — member always injected by code.

- [ ] **Step 1: Write the failing tests** — `tests/test_form_agent.py`:

```python
# tests/test_form_agent.py — Form_Filler 的 agent_core 接线。
import agent_core


FORM_TOOLS = {"fill_form_scan", "fill_form_define_fields", "fill_form_next",
              "fill_form_set_answer", "fill_form_render", "fill_form_list",
              "fill_form_cancel"}


def test_commands_allowed_and_routed():
    for c in ("form-scan", "form-define", "form-next", "form-set",
              "form-render", "form-list", "form-cancel"):
        assert c in agent_core.ALLOWED_COMMANDS
        assert agent_core._cli_path(c).parent.name == "Form_Filler"


def test_tools_registered_with_schema():
    names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
    assert FORM_TOOLS <= names
    assert FORM_TOOLS <= set(agent_core._TOOL_MAP)


def test_member_always_injected():
    for tool in FORM_TOOLS:
        out = agent_core._apply_member(tool, {"member": "Wenliang", "session": "x"}, "Jim")
        assert out["member"] == "Jim"


def test_render_is_doc_tool():
    assert "fill_form_render" in agent_core._DOC_TOOLS


def test_scan_render_get_long_timeout():
    assert agent_core._CLI_TIMEOUTS["form-scan"] >= 120
    assert agent_core._CLI_TIMEOUTS["form-render"] >= 120


def test_doc_sentinel_takes_first_line_only():
    # form-render 第一行是路径，后续可能有"警告:"行 —— 哨兵只取第一行
    reply = "⚙️ fill_form_render\n\n好了\n\x01DOC:jim/forms/x_filled.pdf"
    text, imgs, docs = agent_core.split_reply(reply)
    assert docs == ["jim/forms/x_filled.pdf"]


def test_system_prompt_mentions_form_workflow():
    p = agent_core._build_system_prompt()
    assert "fill_form_scan" in p
    assert "一次只问一个" in p or "一条消息只问一个" in p
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_form_agent.py -q` → FAIL
- [ ] **Step 3: Implement in `agent_core.py`** (locations by anchor, current line numbers approximate):

3a. After `_ANYSEARCH_COMMANDS` (~line 144):

```python
_FORM_COMMANDS = {"form-scan", "form-define", "form-next", "form-set",
                  "form-render", "form-list", "form-cancel"}
```

3b. After `ALLOWED_COMMANDS |= _ANYSEARCH_COMMANDS` (~line 163):

```python
ALLOWED_COMMANDS |= _FORM_COMMANDS
```

3c. In `_cli_path` before the `else` fallback:

```python
    elif cmd in _FORM_COMMANDS:
        skill = "Form_Filler"
```

3d. Above `_run_cli` add per-command timeout table; inside `_run_cli` replace `timeout=30` with `timeout=_CLI_TIMEOUTS.get(cmd, 30)`:

```python
# 个别命令超时加长：form-scan 平面表要逐页渲染+OCR，form-render 要重组 PDF。
_CLI_TIMEOUTS = {"form-scan": 120, "form-render": 120}
```

3e. Tool impls, after `_tool_anysearch_subdomains`:

```python
def _tool_fill_form_scan(args): return _run_cli("form-scan", args)
def _tool_fill_form_define(args):
    args = dict(args)
    fields = args.get("fields")
    if isinstance(fields, (list, dict)):
        args["fields"] = json.dumps(fields, ensure_ascii=False)
    return _run_cli("form-define", args)
def _tool_fill_form_next(args): return _run_cli("form-next", args)
def _tool_fill_form_set(args): return _run_cli("form-set", args)
def _tool_fill_form_render(args): return _run_cli("form-render", args)
def _tool_fill_form_list(args): return _run_cli("form-list", args)
def _tool_fill_form_cancel(args): return _run_cli("form-cancel", args)
```

3f. `_TOOL_MAP` entries (after `"anysearch_subdomains"`):

```python
    "fill_form_scan": _tool_fill_form_scan,
    "fill_form_define_fields": _tool_fill_form_define,
    "fill_form_next": _tool_fill_form_next,
    "fill_form_set_answer": _tool_fill_form_set,
    "fill_form_render": _tool_fill_form_render,
    "fill_form_list": _tool_fill_form_list,
    "fill_form_cancel": _tool_fill_form_cancel,
```

3g. After `_CAL_MEMBER_TOOLS` definition:

```python
# 填表工具按成员私有（会话在 data/<成员>/forms/），一律强制注入发送者 member
_FORM_TOOLS = {"fill_form_scan", "fill_form_define_fields", "fill_form_next",
               "fill_form_set_answer", "fill_form_render", "fill_form_list",
               "fill_form_cancel"}
```

And in `_apply_member`, extend the condition:

```python
    if (tool_name in _MEMBER_WRITE_TOOLS or tool_name in _NOTE_TOOLS
            or tool_name in _SHEET_TOOLS or tool_name in _CAL_MEMBER_TOOLS
            or tool_name in _FORM_TOOLS):
```

3h. `_DOC_TOOLS` line becomes:

```python
_DOC_TOOLS = {"send_document", "send_file", "fill_form_render"}
```

3i. In `handle()`, the doc-collection line `produced_docs.append(result.strip())` becomes (form-render may print `警告:` lines after the path; sentinel takes line 1 only):

```python
                    elif name in _DOC_TOOLS:
                        produced_docs.append(result.strip().splitlines()[0])
```

3j. `TOOL_SCHEMAS` additions (append before the closing `]`):

```python
    _fn("fill_form_scan", "识别 PDF 表格的可填字段并创建填表会话（用户要求填表时用）。"
        "可填写 PDF 直接列出字段；平面/扫描 PDF 返回逐页 OCR 文本+坐标，"
        "需再调 fill_form_define_fields 提交你推断的字段。", {
        "file": _s("PDF 路径（用户发来的保存路径，data 内）"),
    }, ["file"]),
    _fn("fill_form_define_fields", "平面表专用：把你从 OCR 布局推断出的待填字段提交给会话。"
        "anchor 是填写区域（标签右侧或下方的空白处），页面像素坐标。", {
        "session": _s("会话 id"),
        "fields": _s('JSON 数组: [{"name","label","type":"text|checkbox|choice",'
                     '"options":[],"page":0,"anchor":{"x","y","w","h"}}]'),
    }, ["session", "fields"]),
    _fn("fill_form_next", "取会话中下一个未回答字段（问用户前调它）。", {
        "session": _s("会话 id"),
    }, ["session"]),
    _fn("fill_form_set_answer", "记录用户对某字段的回答。留空传空字符串。", {
        "session": _s("会话 id"),
        "field": _s("字段 name"),
        "value": _s("用户给的值；checkbox 用 on/off；留空传 \"\""),
    }, ["session", "field", "value"]),
    _fn("fill_form_render", "所有字段回答完后生成填好的 PDF 并自动发给用户。", {
        "session": _s("会话 id"),
    }, ["session"]),
    _fn("fill_form_list", "列出我的填表会话（恢复中断的填表用）。", {}),
    _fn("fill_form_cancel", "取消一个填表会话。", {
        "session": _s("会话 id"),
    }, ["session"]),
```

3k. System prompt: in `_build_system_prompt`, find the behavior-rules section (the block containing the `send_document` guidance around line 292) and add:

```
- 填 PDF 表格：用户发来表格并要求填写 → fill_form_scan（file 传保存路径）。
  平面/扫描表先按 OCR 布局推断字段（fill_form_define_fields）。之后进入逐字段问答：
  每次 fill_form_next 取一个字段，一条消息只问一个字段——即使你从成员注册表/
  备忘/文档里知道答案，也必须问，把已知值作为建议给出（"回复'对'或给出正确值"）。
  绝不擅自替用户填任何值，绝不编造。用户答一个记一个（fill_form_set_answer；
  用户说"跳过/留空"传空字符串）。全部答完（或用户说"剩下都留空"时把余下字段
  逐个置空）再 fill_form_render，PDF 会自动发给用户。中断的填表用 fill_form_list 恢复。
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_form_agent.py -q` → PASS; full suite `python -m pytest tests/ -q` → PASS (existing `test_send_files.py` / `test_agent_*` must stay green — 3i touches shared code)
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(agent): wire form filler tools into runtime agent"`

---

### Task 8: handle_image routing, docs, requirements

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` (`handle_image` prompt)
- Create: `.codewhale/skills/Form_Filler/SKILL.md`
- Modify: `requirements-optional.txt`, `README.md`, `FamilyAssistant.md` (feature lists)

- [ ] **Step 1: `handle_image` routing** — in the `handle_image` prompt (the five-case block), renumber and insert a new case before "5) 其他有信息价值的材料"（其他 case 编号顺延为 6）:

```
f"5) 用户此前或随图说明想要**填写**这份表格（如\"帮我填这个表\"）："
f"不要归档，改调 fill_form_scan（file 传上面的保存路径）进入填表流程。\n"
```

And at the end of the prompt after "信息不完整就先问用户" add: `f"拿不准是归档还是填表时问用户。"`

- [ ] **Step 2: requirements-optional.txt** — append (keep file's comment style):

```
# pypdf — Form_Filler 的可填写 PDF（AcroForm）读字段/填值；未安装时 form-scan 提示安装。
pypdf
# pypdfium2 — Form_Filler 的平面/扫描 PDF 逐页渲染；未安装时平面表填写不可用（可填写 PDF 不受影响）。
pypdfium2
# Pillow — Form_Filler 平面表盖字 + 重组 PDF（与 pypdfium2 配套）。
Pillow
```

- [ ] **Step 3: SKILL.md** — `.codewhale/skills/Form_Filler/SKILL.md`, following Note_Keeper's structure: 一句话简介（收 PDF 表 → 逐字段问答 → 回填好的 PDF）、代码位置 tree（cli.py / form_fill.py / form_overlay.py / form_session.py）、会话 JSON schema 表格、CLI 子命令表（含 exact stdout contract: form-render 第一行=路径）、两个 tier 的流程图解、依赖与降级说明、成员隔离说明（路径闸门 + member 注入）。

- [ ] **Step 4: README.md** — add a feature section after 📁 家庭文档管理:

```markdown
### 📋 PDF 表格代填（Form Filler）
- 微信/Telegram 发一份 PDF 表格说"帮我填"→ Bot 识别字段，**一条消息问一个空**，答完自动生成填好的 PDF 发回
- 可填写 PDF（政府/移民/银行表单常见）直接填字段；扫描/平面表格走 OCR 定位 + 盖字
- 已知信息（成员法定名、备忘里的号码）会作为**建议**给出，但每个空都经你确认——绝不擅自填
- 填一半断了不怕：会话落盘，`/clear`、重启后都能继续；"剩下都留空"一句话收尾
- 依赖 pypdf（可填写 PDF）/ pypdfium2+Pillow（扫描表），未安装时给安装提示
```

Also update `FamilyAssistant.md` if it lists skills/commands (check its structure; add Form_Filler skill + form-* commands wherever other skills are enumerated).

- [ ] **Step 5: Full suite + manual smoke** — `python -m pytest tests/ -q` → all PASS. Manual smoke (agent test REPL needs DEEPSEEK_API_KEY; if unavailable, CLI smoke only):

```powershell
$env:DATA_ROOT="$env:TEMP\fa_form_smoke"
python .codewhale/skills/Form_Filler/cli.py form-list --member Jim
# expect: 没有填表会话。
```

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(form): image routing, docs, optional deps for form filler"`

---

## Self-review notes

- Spec coverage: session store (T3), acroform (T4), flat overlay (T5), CLI (T6), agent tools + prompt + member enforcement + sentinel (T7), handle_image routing + docs + deps (T8), paths (T1), OCR words (T2). XFA: covered implicitly — no AcroForm fields → flat path. Encrypted: T4 `_open` + CLI relays.
- Sentinel contract: form-render stdout line 1 = path; `handle()` change (3i) keeps send_file/send_document behavior (single-line results unaffected).
- Type consistency: field dict shape identical across form_session/_norm_field, form_fill.scan_fields output (no page/anchor → defaults added by _norm_field), CLI, and schema docs. checkbox canonical values "on"/"off" everywhere; acroform on-state stored in options[0] but render() re-reads states from the PDF (options informational).
- `form_fill.scan_fields` output feeds `form_session.new_session(fields=...)` — `_norm_field` fills missing page/anchor/value/asked. choice with empty options raises — acroform /Ch with no /Opt would break: guard in scan_fields → if opts empty, emit type "text" instead. **Fix during T4 implementation:** change the `/Ch` branch to:

```python
        elif ft == "/Ch":
            opts = [str(o) for o in (f.get("/Opt") or [])]
            if opts:
                fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                               "type": "choice", "options": opts})
            else:
                fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                               "type": "text", "options": []})
```
