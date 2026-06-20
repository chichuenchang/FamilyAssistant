# PDF OCR + unified handle_image — Doc/Code Consistency Audit

**Date:** 2026-06-19  
**Scope:** README.md, OCR/SKILL.md, Agent_Runtime/SKILL.md vs agent_core.py + ocr.py  
**Result:** All 3 doc files are **clean** for the 5 enumerated problem types. One minor wording suggestion noted below.

## Summary

All docs correctly reflect that:
- `Agent.handle_image` is the unified entry for both images and PDFs (no stale `handle_file`).
- PDF is fully supported via Tencent Cloud `IsPdf`, capped at `MAX_PDF_PAGES=20`.
- No doc claims PDF is "暂不支持" (unsupported).
- No doc describes PDF as a separate handler/method from `handle_image`.
- All referenced names (`agent.handle`, `agent.handle_image`, `agent_core.member_inbox_dir`, `members.resolve`, `ocr_image`, `is_available`, `MAX_PDF_PAGES`, `IsPdf`) exist in code.
- All file-tree entries list files that exist on disk.

## Per-File Results

| File | Line | Problem | Suggested Fix |
|------|------|---------|---------------|
| `README.md` | — | clean | — |
| `.codewhale/skills/OCR/SKILL.md` | — | clean | — |
| `.codewhale/skills/Agent_Runtime/SKILL.md` | — | clean | — |

## Minor Note (not a problem-category match)

| File | Line | Note |
|------|------|------|
| `.codewhale/skills/Agent_Runtime/SKILL.md` | ~95 | "非 PDF 文件仍回复'暂不支持'" — literally "non-PDF files are unsupported", but images (which are non-PDF) *are* supported. In context the paragraph topic is "图片或 PDF 文件消息", so the intended meaning is "files that are neither images nor PDFs". Suggest: "其他类型文件仍回复'暂不支持'" or "非图片/PDF 文件仍回复'暂不支持'" for clarity. Not flagged as a problem — no reader in context would misinterpret this as claiming PDF is unsupported or as a stale method reference. |

## Verification Details

### Code confirms:
- `agent_core.py:874` — `Agent.handle_image(self, image_path, user, member)` exists. Docstring: "图片/PDF 入口：ocr_image 对两者一视同仁（PDF 走腾讯 IsPdf 逐页）".
- `agent_core.py` — No `Agent.handle_file` method anywhere (grep confirmed 0 matches).
- `ocr.py:63` — `MAX_PDF_PAGES = 20`.
- `ocr.py:~175` — `ocr_image()` PDF branch: `if p.suffix.lower() == ".pdf":` → iterates `range(1, MAX_PDF_PAGES + 1)` with `IsPdf: True`.
- `ocr.py` — `is_available()` exists.
- `members.py:48` — `resolve(channel, channel_id)` exists.

### Docs confirm:
- `Agent_Runtime/SKILL.md` contract: `agent.handle_image(path, user, member)  # 图片或 PDF 文件消息` ✅
- `Agent_Runtime/SKILL.md` new-channel guidance: "PDF 与图片同一入口" ✅
- `OCR/SKILL.md:3`: "PDF 通过腾讯云 IsPdf 逐页 OCR（上限 MAX_PDF_PAGES=20 页）" ✅
- `README.md`: "发票/小票照片或 PDF 账单 → 腾讯云 OCR" ✅
- No doc contains `handle_file` in reference to the Agent (grep confirmed 0 matches across all 3 docs).

### Explicitly NOT flagged:
- `wechat_ilink.py:147` `def handle_file(msg):` — this is the SDK `on_file` message handler closure, not an agent method. Correct and excluded per instructions.
