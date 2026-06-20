# PDF OCR + Chat Auto-Ingest — Design Spec

Date: 2026-06-19
Status: approved (standing approval through implementation)

## Problem

OCR (`ocr_image` / `ocr_extract`, Tencent Cloud `GeneralBasicOCR`) only handles **images**. The
chat transports deliver file messages (`@bot.on_file` exists in `wechat_ilink.py`) but the handler
is a stub that replies "收到文件（暂不支持文件处理）". So a PDF a family member sends — a contract,
an insurance policy, a government/immigration form — is dropped: not OCR'd, not archived, not
searchable.

## Goal

A PDF sent in WeChat/Telegram is downloaded, OCR'd, and archived like a photo: the agent classifies
it (usually a long-term document → `add_document`) and stores it under `Family/documents/`, with the
OCR text indexed for keyword search and expiry reminders. OCR also works on PDFs everywhere it works
on images (the `ocr_image` CLI tool, `ocr_extract` for statements).

Non-goals (YAGNI): non-PDF file types (docx/xlsx/…) — they keep the "暂不支持" reply; PDF text-layer
extraction libraries (use Tencent OCR for all PDFs, scanned and digital, to stay dependency-free);
splitting/merging PDFs; per-page images persisted to disk.

## Decisions (confirmed with user)

- **Scope: full chat auto-ingest** (not just the OCR layer). The `on_file` stub is replaced so a
  PDF flows transport → OCR → agent classification → archive, mirroring the image flow.
- **OCR method: Tencent `IsPdf` (no new dependency).** Base64 the PDF and call the existing
  `GeneralBasicOCR` with `IsPdf=True` + `PdfPageNumber=N`, page by page. Works for scanned and
  digital PDFs, reuses the existing TC3 signing and the 1000/month free quota. **Verification gate:
  Task 1 of the plan confirms `IsPdf` works against a real sample PDF; if Tencent does not support
  it for `GeneralBasicOCR`, fall back to rendering pages with PyMuPDF (`fitz`) → PNG → `ocr_image`.**

## Components

### 1. OCR layer — `ocr.py` (`ocr_image` becomes PDF-aware)

`ocr_image(path)` branches on the lowercase suffix:
- `.pdf` → read bytes, base64-encode, then for `n` in `1..MAX_PDF_PAGES` (cap **20**, quota guard)
  call `_call_ocr({"ImageBase64": pdf_b64, "IsPdf": True, "PdfPageNumber": n, "LanguageType": "zh"})`.
  Concatenate each page's joined `DetectedText` with `\n`. Stop the loop when a page returns no data
  (`_call_ocr` already returns `None` on out-of-range / Tencent errors). Return contract, matching the
  image path: **page 1 `None` → return `None`** (OCR unavailable / hard failure, same as an image that
  fails); a `None` on page ≥ 2 → end of document, return the text gathered so far; pages present but
  all empty → return `""`.
- anything else → the existing image path (unchanged).

`ocr_extract` is unchanged — it calls `ocr_image`, so passing a `.pdf` (e.g. a bank statement) yields
per-line transactions transparently. The `ocr_image` CLI tool (`_tool_ocr_image` in `agent_core`)
likewise gains PDF support for free.

Add module constant `MAX_PDF_PAGES = 20`. Keep one OCR call per page (Tencent `IsPdf` is per-page).

### 2. Agent — `agent_core.Agent.handle_file(path, user, member)`

New method, mirroring `handle_image`:
- Empty `member` → `""` (unregistered sources never reach the LLM).
- `from ocr import ocr_image, is_available`. If available, `ocr_text = ocr_image(path)`.
- If `ocr_text`, build a **PDF-tuned classification prompt** and `return self.handle(prompt, …)`:
  - The path was saved as `{path}`; OCR full text follows.
  - PDFs are **usually long-term documents** (contract / insurance / ID / health card / government
    or immigration form): `add_document` with `file={path}`, `ocr-text=<full text>`, and `type` from
    the doc-type set (`lease/insurance/health/id_document/other`; an immigration/visa form → `other`),
    plus `expiry` + `action-note` when a deadline is present (drives the daily reminder).
  - Multi-page **bank/card/payment statement** (line-item flows) → per-line `add_transaction` (same
    rules as the image prompt: line items only, never totals/min-payment; `desc` carries
    distinguishing info; `force=true` for genuine same-day-same-amount lines).
  - Otherwise an actionable doc (invoice/quote/unpaid bill) → `add_task` (with `source-image={path}`,
    `due` if dated); a date/time arrangement → `add_event`.
  - Default ownership = sender (per the existing for-member rule); ask the user when info is
    incomplete; report briefly what was archived.
- No-OCR fallback (`is_available()` false or `ocr_text` empty): reply
  "📄 收到 PDF（已保存）。配置腾讯云 OCR 后可自动识别归档，或用文字告诉我这是什么。" — the file is
  already in the sender's inbox, so it is recoverable / can be archived manually via `doc-add --file`.

### 3. Transport — wire the file handlers

**`wechat_ilink.py` `@bot.on_file`** (replace the stub): resolve member (unregistered → silent
return). `_calendar_tick()` + `_image_gc_tick()` like the image handler. If `msg.file_name` ends in
`.pdf` (case-insensitive): `now = datetime.now()`; save to
`member_inbox_dir(member, now) / f"{ts}_wechat.pdf"` via `msg.save(...)`; `_backup_mark_dirty()`; log;
`reply = agent.handle_file(str(pdf_path), user=msg.from_user, member=member)`; `msg.reply_text(reply)`.
Wrap in try/except (reply the error) exactly like `handle_image`. Non-PDF files keep the existing
"收到文件: {name}（暂不支持文件处理）" reply.

**`telegram_bot.py`** gets the same wiring on its document/file handler (download the PDF to the
sender's inbox, route to `agent.handle_file`; non-PDF → friendly "暂不支持"). If Telegram has no file
handler yet, add one following its existing photo-handler pattern.

### 4. Storage

- The inbox PDF (`data/<member>/inbox/YYYY-MM/<ts>_<channel>.pdf`) is sender-private staging.
  `doc-add --file` copies the authoritative file to `Family/documents/<type>/<date>_<type>_<title>.pdf`
  (existing `_store_file` logic, suffix-preserving). Backup picks up `Family/documents/...` via Jim's
  scope.
  > ⚠ Filename convention changed since this spec: now `<member>_<title><ext>` (no date, no type — type is the dir). See `docs/superpowers/audits/2026-06-19-doc-filename-consistency.md`.
- The inbox copy lingers exactly like a document-classified inbox **image** does today (`doc-add`
  copies, it does not move) — existing accepted behavior, **not addressed here**. (`image_gc` only
  cleans stale *calendar-item* `source_image` files, not the inbox, so it is unrelated.)

## Error handling

- `ocr_image` PDF branch: a per-page `_call_ocr` returning `None` ends the loop (treated as
  end-of-document) rather than raising; a hard failure on page 1 returns `None` (OCR unavailable),
  preserving the current contract.
- `handle_file` only runs the LLM when OCR yields text; otherwise the graceful fallback reply.
- Transport handlers wrap everything in try/except and reply the error string — a bad PDF never
  crashes the bot loop.
- Page cap (`MAX_PDF_PAGES`) bounds quota use on a pathological large PDF.

## Testing

- `ocr_image` PDF branch (faked `_call_ocr`): asserts each call carries `IsPdf=True` + the right
  `PdfPageNumber`, that pages are concatenated, that the loop stops when a page returns `None`, and
  that it caps at `MAX_PDF_PAGES`. Image path test stays green (no `IsPdf`).
- `ocr_extract` on a `.pdf` (faked `ocr_image`) still returns transactions — confirms the cascade.
- `Agent.handle_file`: with OCR faked to return text, asserts it calls `self.handle` with a prompt
  containing the saved path + OCR text; with OCR unavailable, asserts the fallback reply and that no
  LLM call is made.
- Transport: Telegram's `download_document` (mock `_api` + `urllib`) saves a `.pdf` into the sender
  inbox; the PDF-suffix routing decision (PDF → `handle_file`, non-PDF → "暂不支持") is unit-tested via
  a small pure helper. The SDK message-loop closures themselves are thin glue, verified by inspection
  + the live smoke run.
- Full `pytest` suite green before completion.

## Implementation order (phased, TDD)

1. **Verify Tencent `IsPdf`** on a real sample PDF (a throwaway script / manual call). Pin the OCR
   approach (A confirmed, or fall back to PyMuPDF render). Record the result.
2. `ocr.py`: `MAX_PDF_PAGES` + `ocr_image` PDF branch (+ tests).
3. `agent_core.py`: `handle_file` + register nothing new in tool maps (it drives existing tools)
   (+ tests).
4. Transport: `wechat_ilink` `on_file` wiring + `telegram_bot` document wiring (+ tests).
5. Docs: OCR `SKILL.md` (PDF support) + Agent_Runtime `SKILL.md` / README note that the bot ingests
   PDFs. Full suite green.
