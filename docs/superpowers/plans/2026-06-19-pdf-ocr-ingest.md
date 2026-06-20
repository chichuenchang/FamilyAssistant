# PDF OCR + Chat Auto-Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A PDF sent in WeChat/Telegram is downloaded, OCR'd, and archived like a photo (usually a long-term document under `Family/documents/`), and OCR works on PDFs everywhere it works on images.

**Architecture:** Make `ocr_image` PDF-aware via Tencent's `IsPdf` per-page OCR (no new dependency); add `Agent.handle_file` mirroring `handle_image` with a PDF-tuned classification prompt; wire the transport file handlers (the WeChat `on_file` stub + a new Telegram document branch) to download a PDF to the sender's inbox and route it to `handle_file`.

**Tech Stack:** Python 3 stdlib (`urllib`, `base64`, `pathlib`), pytest, Tencent Cloud `GeneralBasicOCR`, DeepSeek (agent classification).

## Global Constraints

- **Stdlib only** — no new third-party packages. PDF OCR uses Tencent `GeneralBasicOCR` with `IsPdf=True` + `PdfPageNumber=N` (the PDF base64 goes in `ImageBase64`); no local PDF library.
- **`MAX_PDF_PAGES = 20`** — cap pages OCR'd per PDF (Tencent free tier is 1000 calls/month; one call per page).
- **`ocr_image` return contract** (must match the image path): page 1 `None` → return `None` (OCR unavailable / unreadable PDF); a `None` on page ≥ 2 → end of document, return text gathered so far; pages present but all empty → `""`.
- **PDF only** — non-PDF file messages keep the friendly "暂不支持" reply.
- **Inbox staging** — a received PDF is saved to `data/<member>/inbox/YYYY-MM/<ts>_<channel>.pdf`; `doc-add --file` later copies the authoritative file to `Family/documents/`. The inbox copy lingering is existing accepted behavior (same as document-classified images); not addressed here.
- **Never crash the bot** — transport handlers wrap work in try/except and reply the error; `handle_file` only invokes the LLM when OCR yields text.
- **Code comments/docstrings** follow the codebase style (Chinese prose, English identifiers). Entrypoints keep their existing Windows-console guard.
- **Verification gate** — the real Tencent `IsPdf` behavior is confirmed against a sample PDF in Final Verification; unit tests fake `_call_ocr`, so they pass regardless.

---

## File Structure

- `.codewhale/skills/OCR/ocr.py` — **modify**: `MAX_PDF_PAGES` + `ocr_image` PDF branch.
- `.codewhale/skills/Agent_Runtime/agent_core.py` — **modify**: add `Agent.handle_file`.
- `.codewhale/skills/Agent_Runtime/wechat_ilink.py` — **modify**: replace the `on_file` stub.
- `.codewhale/skills/Agent_Runtime/telegram_bot.py` — **modify**: add `download_document` + a `document` dispatch branch.
- `.codewhale/skills/OCR/SKILL.md`, `.codewhale/skills/Agent_Runtime/SKILL.md`, `README.md` — **modify**: document PDF support.
- `tests/conftest.py` — **modify**: add the OCR skill dir to `sys.path`.
- `tests/test_ocr.py` — **create**: `ocr_image` PDF tests.
- `tests/test_agent_member.py` — **modify**: `handle_file` tests.
- `tests/test_telegram_pdf.py` — **create**: `download_document` test.

Run the suite: `python -m pytest tests/ -q` (from project root).

---

## Task 1: `ocr_image` handles PDFs

**Files:**
- Modify: `.codewhale/skills/OCR/ocr.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_ocr.py`

**Interfaces:**
- Consumes: existing `_call_ocr(payload) -> dict | None`, `base64`, `Path`.
- Produces: module constant `MAX_PDF_PAGES = 20`; `ocr_image(path)` now OCRs `.pdf` per-page (Tencent `IsPdf`) and concatenates; image behavior unchanged. `ocr_extract` and the `ocr_image` CLI tool inherit PDF support automatically (they call `ocr_image`).

- [ ] **Step 1: Make the OCR skill importable in tests**

In `tests/conftest.py`, after the existing skill-dir `sys.path` inserts, add:

```python
OCR_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "OCR"
)
sys.path.insert(0, str(OCR_DIR))
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ocr.py`:

```python
# tests/test_ocr.py — OCR module (PDF support).
import ocr


def test_ocr_image_pdf_loops_pages_with_ispdf(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-fake")
    calls = []

    def fake(payload):
        calls.append(payload)
        n = payload["PdfPageNumber"]
        if n == 1:
            return {"TextDetections": [{"DetectedText": "page1"}]}
        if n == 2:
            return {"TextDetections": [{"DetectedText": "page2"}]}
        return None  # page 3 → out of range

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    out = ocr.ocr_image(str(f))
    assert out == "page1\npage2"
    assert all(c["IsPdf"] is True for c in calls)
    assert [c["PdfPageNumber"] for c in calls] == [1, 2, 3]


def test_ocr_image_pdf_page1_failure_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: None)
    assert ocr.ocr_image(str(f)) is None


def test_ocr_image_pdf_caps_at_max_pages(monkeypatch, tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"%PDF")
    seen = []

    def fake(payload):
        seen.append(payload["PdfPageNumber"])
        return {"TextDetections": [{"DetectedText": f"p{payload['PdfPageNumber']}"}]}

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    out = ocr.ocr_image(str(f))
    assert seen == list(range(1, ocr.MAX_PDF_PAGES + 1))   # never beyond the cap
    assert out.count("\n") == ocr.MAX_PDF_PAGES - 1         # all pages joined


def test_ocr_image_image_path_has_no_ispdf(monkeypatch, tmp_path):
    f = tmp_path / "x.jpg"
    f.write_bytes(b"img")
    captured = {}

    def fake(payload):
        captured.update(payload)
        return {"TextDetections": [{"DetectedText": "hi"}]}

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    assert ocr.ocr_image(str(f)) == "hi"
    assert "IsPdf" not in captured


def test_ocr_image_missing_file_returns_none():
    assert ocr.ocr_image("nope.pdf") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_ocr.py -v`
Expected: FAIL — `AttributeError: module 'ocr' has no attribute 'MAX_PDF_PAGES'` / PDF assertions fail.

- [ ] **Step 4: Implement the PDF branch**

In `.codewhale/skills/OCR/ocr.py`, add the constant near the OCR endpoint constants (after `OCR_REGION`):

```python
MAX_PDF_PAGES = 20   # PDF 逐页 OCR 上限（腾讯免费额度 1000 次/月，防超大 PDF 烧额度）
```

Replace the existing `ocr_image` function with:

```python
def ocr_image(image_path: str) -> Optional[str]:
    """通用文字识别。图片直接 OCR；.pdf 用腾讯 IsPdf 逐页 OCR 后拼接。

    返回 None = OCR 不可用 / 首页失败；空文档返回 ""。
    """
    p = Path(image_path)
    if not p.exists():
        return None
    b64 = base64.b64encode(p.read_bytes()).decode()

    if p.suffix.lower() == ".pdf":
        pages: list[str] = []
        for n in range(1, MAX_PDF_PAGES + 1):
            data = _call_ocr({"ImageBase64": b64, "IsPdf": True,
                              "PdfPageNumber": n, "LanguageType": "zh"})
            if not data:
                if n == 1:
                    return None          # 首页失败 = OCR 不可用 / PDF 不可读
                break                    # 后续页无数据 = 文档到此结束
            words = [d["DetectedText"] for d in data.get("TextDetections", [])
                     if d.get("DetectedText")]
            if words:
                pages.append("\n".join(words))
        return "\n".join(pages) if pages else ""

    data = _call_ocr({"ImageBase64": b64, "LanguageType": "zh"})
    if not data:
        return None
    words = [d["DetectedText"] for d in data.get("TextDetections", [])
             if d.get("DetectedText")]
    return "\n".join(words) if words else ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_ocr.py -v`
Expected: PASS (5 tests). Then `python -m pytest tests/ -q` — whole suite green.

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/OCR/ocr.py tests/conftest.py tests/test_ocr.py
git commit -m "feat(ocr): ocr_image OCRs PDFs per-page via Tencent IsPdf"
```

---

## Task 2: `Agent.handle_file`

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_agent_member.py`

**Interfaces:**
- Consumes: `ocr.ocr_image` / `ocr.is_available` (Task 1), `Agent.handle`.
- Produces: `Agent.handle_file(file_path: str, user="default", member="") -> str` — OCRs the PDF and drives the existing tools via a PDF-tuned prompt; empty `member` → `""`; no-OCR → a fallback reply with no LLM call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_member.py`:

```python
def test_handle_file_returns_empty_without_member(tmp_path):
    agent = agent_core.Agent()
    assert agent.handle_file(str(tmp_path / "x.pdf"), user="x", member="") == ""


def test_handle_file_ocr_drives_handle(monkeypatch):
    import ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_image", lambda path: "CONSENT FORM TEXT")
    agent = agent_core.Agent()
    cap = {}
    monkeypatch.setattr(agent, "handle",
                        lambda prompt, user="default", member="": cap.update(p=prompt) or "ok")
    out = agent.handle_file("data/Alex/inbox/2026-06/x.pdf", user="u", member="Alex Lee")
    assert out == "ok"
    assert "x.pdf" in cap["p"] and "CONSENT FORM TEXT" in cap["p"]


def test_handle_file_fallback_when_ocr_unavailable(monkeypatch):
    import ocr
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    agent = agent_core.Agent()
    called = {"handle": False}
    monkeypatch.setattr(agent, "handle",
                        lambda *a, **k: called.__setitem__("handle", True) or "x")
    out = agent.handle_file("x.pdf", member="Alex Lee")
    assert "PDF" in out and called["handle"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_member.py -k handle_file -v`
Expected: FAIL — `AttributeError: 'Agent' object has no attribute 'handle_file'`.

- [ ] **Step 3: Implement `handle_file`**

In `.codewhale/skills/Agent_Runtime/agent_core.py`, add this method to the `Agent` class immediately after `handle_image`:

```python
    def handle_file(self, file_path: str, user: str = "default", member: str = "") -> str:
        """PDF 文件入口（与 handle_image 平行）：OCR → LLM 分类归档。"""
        if not member:
            return ""
        from ocr import ocr_image, is_available
        if is_available():
            ocr_text = ocr_image(file_path)
            if ocr_text:
                prompt = (
                    f"用户发了一份 PDF 文件，已保存为 {file_path}，OCR结果:\n{ocr_text}\n"
                    f"判断内容并处理（PDF 多为长期文档）：\n"
                    f"1) 重要文档（合同/保单/证件/健康卡/政府或移民表格）→ add_document 归档："
                    f"file 传上面的保存路径，ocr-text 传 OCR 全文，type 选最合适的；"
                    f"有到期日带 expiry，到期要办的事带 action-note。\n"
                    f"2) 银行/信用卡/支付账单（多页流水）→ 逐笔 add_transaction，每条明细一次"
                    f"（**绝不要把账单总额、应还款额、最低还款额、已还款额当成一笔记账**）；"
                    f"desc 带商家+时间以区分同日同额；真实独立消费被重复检查误拦时加 force=true。\n"
                    f"3) 发票/报价/未付账单等要跟进的 → add_task（source-image 传保存路径，有截止日给 due）；"
                    f"带日期时间的安排 → add_event。开出去/未付的发票绝不记成收入或支出。\n"
                    f"默认归属发送者；信息不全先问用户。记完简要汇报归档/记了什么。"
                )
                return self.handle(prompt, user=user, member=member)
        return ("📄 收到 PDF（已保存）。配置腾讯云 OCR 后可自动识别归档，"
                "或用文字告诉我这是什么。")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_member.py -k handle_file -v`
Expected: PASS (3 tests). Then `python -m pytest tests/ -q` — whole suite green.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_agent_member.py
git commit -m "feat(agent): handle_file OCRs + classifies an ingested PDF"
```

---

## Task 3: Wire the transport file handlers

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py`
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py`
- Test: `tests/test_telegram_pdf.py`

**Interfaces:**
- Consumes: `agent.handle_file` (Task 2), `member_inbox_dir`, `resolve`, `_backup_mark_dirty`, the Telegram `_api`/`TOKEN`/`receipt_month_dir`.
- Produces: WeChat `on_file` ingests `.pdf` → inbox → `handle_file`; Telegram `download_document(file_id, file_name, member) -> Path | None` + a `document` dispatch branch.

- [ ] **Step 1: Write the failing test (Telegram download)**

Create `tests/test_telegram_pdf.py`:

```python
# tests/test_telegram_pdf.py — Telegram PDF document download.
import urllib.request

import telegram_bot as tg


def test_download_document_saves_pdf_to_inbox(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg, "_api",
                        lambda action, params: {"ok": True,
                                                "result": {"file_path": "docs/x.pdf"}})

    class _Resp:
        def read(self):
            return b"%PDF-bytes"

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=30: _Resp())

    dest = tg.download_document("fid", "ConsentForm.pdf", member="Alex Lee")
    assert dest is not None
    assert dest.suffix == ".pdf"
    assert dest.read_bytes() == b"%PDF-bytes"
    assert "inbox" in dest.as_posix()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_telegram_pdf.py -v`
Expected: FAIL — `AttributeError: module 'telegram_bot' has no attribute 'download_document'`.

- [ ] **Step 3: Implement Telegram `download_document` + dispatch branch**

In `.codewhale/skills/Agent_Runtime/telegram_bot.py`, add `download_document` immediately after `download_photo`:

```python
def download_document(file_id: str, file_name: str, member: str = "") -> Path | None:
    """下载 Telegram 文档（PDF）到发送成员 inbox，保留 .pdf 后缀。"""
    import urllib.request
    r = _api("getFile", {"file_id": file_id})
    if not r or not r.get("ok"):
        return None
    file_path = r["result"].get("file_path", "")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    suffix = Path(file_name or "").suffix.lower() or ".pdf"
    staging = member_inbox_dir(member, now) if member else receipt_month_dir(now)
    dest = staging / f"{ts}_telegram{suffix}"
    try:
        dest.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        _backup_mark_dirty()
        return dest
    except Exception as e:
        print(f"[tg] 文档下载失败: {e}", file=sys.stderr)
        return None
```

In the message dispatch loop, add a `document` branch immediately AFTER the photo branch (after the photo `continue`, before `if not text:`):

```python
            # 文档消息（PDF）→ 下载到 inbox → OCR 归档流程
            doc = msg.get("document")
            if doc:
                name = doc.get("file_name", "") or ""
                is_pdf = name.lower().endswith(".pdf") or \
                    doc.get("mime_type") == "application/pdf"
                if is_pdf:
                    file_id = doc.get("file_id", "")
                    dest = download_document(file_id, name, member) if file_id else None
                    log.debug("文件 from %s(%s) → %s", user_name, member, dest)
                    if dest:
                        reply = agent.handle_file(str(dest), user=str(chat_id), member=member)
                    else:
                        reply = "文件下载失败，请重发。"
                else:
                    reply = f"收到文件 {name}（暂不支持，PDF 可以）"
                send_message(chat_id, reply)
                offset = max(offset, update_id)
                continue
```

- [ ] **Step 4: Replace the WeChat `on_file` stub**

In `.codewhale/skills/Agent_Runtime/wechat_ilink.py`, replace the existing `handle_file`:

```python
    @bot.on_file
    def handle_file(msg):
        member = resolve("wechat", msg.from_user)
        if member is None:
            return
        name = msg.file_name or ""
        if not name.lower().endswith(".pdf"):
            msg.reply_text(f"收到文件: {name}（暂不支持文件处理，PDF 可以）")
            return
        print(f"[wx] 文件消息 from {msg.from_user}({member}): {name}")
        _calendar_tick()
        _image_gc_tick()
        try:
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            pdf_path = member_inbox_dir(member, now) / f"{ts}_wechat.pdf"
            msg.save(str(pdf_path))
            _backup_mark_dirty()
            log.debug("文件 from %s(%s) 保存 → %s", msg.from_user, member, pdf_path)
            reply = agent.handle_file(str(pdf_path), user=msg.from_user, member=member)
            log.debug("文件回复 → %s", (reply or "")[:200])
            msg.reply_text(reply)
        except Exception as e:
            log.exception("文件处理出错")
            msg.reply_text(f"文件处理出错: {e}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_telegram_pdf.py -v`
Expected: PASS. Then `python -m pytest tests/ -q` — whole suite green. (The WeChat/Telegram dispatch closures are SDK glue, verified by inspection + the live smoke run in Final Verification.)

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/wechat_ilink.py .codewhale/skills/Agent_Runtime/telegram_bot.py tests/test_telegram_pdf.py
git commit -m "feat(transport): ingest PDF file messages (WeChat on_file + Telegram document)"
```

---

## Task 4: Document PDF support

**Files:**
- Modify: `.codewhale/skills/OCR/SKILL.md`, `.codewhale/skills/Agent_Runtime/SKILL.md`, `README.md`

- [ ] **Step 1: Update the docs**

- `OCR/SKILL.md`: note `ocr_image` / `ocr_extract` now accept `.pdf` (Tencent `IsPdf`, per page, capped at `MAX_PDF_PAGES=20`); the CLI `python ocr.py <file>` works on PDFs too.
- `Agent_Runtime/SKILL.md`: under the channel contract, add `agent.handle_file(path, user, member)` for PDF file messages (parallel to `handle_image`); transports route `.pdf` file messages to it.
- `README.md`: in the feature blurb for documents/OCR, add that sending a **PDF** in WeChat/Telegram auto-OCRs + archives it (contracts/policies/IDs/forms), searchable + expiry-reminded. Remove/avoid any claim that files are "暂不支持" for PDFs.

- [ ] **Step 2: Verify the docs match the shipped flags/methods**

Confirm `handle_file`, `download_document`, and the `IsPdf`/`MAX_PDF_PAGES` names match the code.

- [ ] **Step 3: Commit**

```bash
git add .codewhale/skills/OCR/SKILL.md .codewhale/skills/Agent_Runtime/SKILL.md README.md
git commit -m "docs(ocr): document PDF OCR + chat ingestion"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all green (prior count + the new OCR/agent/telegram tests).

- [ ] **Verify Tencent `IsPdf` against a real PDF (the load-bearing assumption)**

With `TENCENT_SECRET_ID`/`TENCENT_SECRET_KEY` set, OCR a small real PDF:

Run: `python .codewhale/skills/OCR/ocr.py "C:\Users\slimj\Downloads\VFS-ConsentForm.pdf"`
Expected: prints the document's text (multiple lines). If it errors with an `IsPdf`/parameter complaint, Tencent `GeneralBasicOCR` does not support `IsPdf` → switch Task 1's PDF branch to render pages with PyMuPDF (`fitz`) → PNG → the image OCR path, and add `PyMuPDF` to `requirements-dev.txt` (this is the spec's documented fallback). The unit tests are unaffected (they fake `_call_ocr`).

- [ ] **Live smoke (manual, optional)**

Send a PDF to the bot in WeChat/Telegram → expect it OCR'd + archived (a `doc-add` line in `data/bot_debug.log`, the file under `Family/documents/`), not the old "暂不支持" reply.

---

## Notes for the implementer

- **Why no PDF library:** Tencent `GeneralBasicOCR` accepts a PDF directly (`IsPdf=True`, one page per call via `PdfPageNumber`), so the project stays stdlib-only. The Final-Verification step confirms this against a real PDF; PyMuPDF is the documented fallback only if Tencent rejects `IsPdf`.
- **Cascade:** because `ocr_extract` and the `ocr_image` CLI tool both call `ocr_image`, Task 1 alone makes PDFs work for statements (transactions) and the CLI — no extra wiring.
- **`from ocr import ...` inside `handle_file`** re-resolves the module attributes each call, so tests monkeypatch `ocr.ocr_image` / `ocr.is_available` and it takes effect.
- **No secrets in code**: OCR/transport read only `os.environ`; never log secret values.
