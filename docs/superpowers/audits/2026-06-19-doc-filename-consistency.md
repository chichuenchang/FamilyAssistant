# Document Filename Convention Consistency Audit — 2026-06-19

> **Scope:** entire repo — live code (`.codewhale/**/*.py`, `tests/**/*.py`), live docs (`*.md` except under `docs/superpowers/`, plus `config.json`), archive docs (`docs/superpowers/plans/**`, `docs/superpowers/specs/**`, `docs/superpowers/audits/**`).
> **Baseline:** NEW filename = `<member>_<safe_title><ext>` (member present) or `<safe_title><ext>` (family-level, no member). No ISO date, no `_<doc_type>_` segment. Directory = `data/Family/documents/<doc_type>/`.
> **OLD (now WRONG) filename:** `<YYYY-MM-DD>_<doc_type>_<safe_title><ext>`.

## Summary

| Category | Count | Verdict |
|----------|-------|---------|
| A — Live code (`.py`) | **0 inconsistent** | All live `.py` files already match the new convention. |
| B — Live docs (`SKILL.md`, `README.md`, `FamilyAssistant.md`, `config.json`) | **0 inconsistent** | All live docs already match the new convention. |
| C — Ingest / member-threading | **0 issues** | `member` is correctly threaded from transport → agent → CLI → `_store_file`. |
| D — Archive docs (plans / specs / audits) | **6 snippet locations across 3 files** | All are point-in-time design records; listed below with ARCHIVE / no-fix-expected. |

## Findings Table

| file:line | category | snippet (trimmed) | inconsistent with NEW convention? (yes/no) | suggested action |
|-----------|----------|-------------------|---------------------------------------------|------------------|
| `docs/superpowers/plans/2026-06-11-document-keeper.md:918` | D | `def _store_file(src: str, doc_type: str, title: str) -> str:` | yes (3-arg signature, no `member`) | ARCHIVE — no fix expected |
| `docs/superpowers/plans/2026-06-11-document-keeper.md:933` | D | `stem = f"{date.today().isoformat()}_{doc_type}_{safe_title}"` | yes (old stem with date + doc_type) | ARCHIVE — no fix expected |
| `docs/superpowers/plans/2026-06-11-document-keeper.md:955` | D | `file_rel = _store_file(args.file, args.type, args.title)` | yes (3-arg call, no `member`) | ARCHIVE — no fix expected |
| `docs/superpowers/plans/2026-06-11-document-keeper.md:1692` | D | `doc-add --file` 自动复制到 `documents/<类型>/YYYY-MM-DD_类型_标题.ext` | yes (Chinese old format: ISO date + doc_type segment) | ARCHIVE — no fix expected |
| `docs/superpowers/specs/2026-06-11-document-keeper-design.md:81` | D | `documents/<doc_type>/YYYY-MM-DD_<doc_type>_<title>.<ext>` | yes (old format in design spec) | ARCHIVE — no fix expected |
| `docs/superpowers/specs/2026-06-19-pdf-ocr-ingest-design.md:94–95` | D | `Family/documents/<type>/<date>_<type>_<title>.pdf` | yes (old format in ingest design spec) | ARCHIVE — no fix expected |

## CLEAN — Key files verified consistent

These files were checked and **already follow the new convention** (no action needed):

### Live code
- `.codewhale/skills/Document_Keeper/cli.py` — `_store_file(src, doc_type, title, member="")` with 4-arg signature; filename = `<safe_member>_<safe_title><ext>` or `<safe_title><ext>`; no date, no doc_type in stem. `cmd_doc_add` correctly threads `member`.
- `.codewhale/skills/Document_Keeper/doc_db.py` — no filename construction or parsing; `file_path` stored as opaque rel path.
- `.codewhale/skills/Document_Keeper/doc_models.py` — only defines `DOCUMENTS_DIR` via `paths.family_dir() / "documents"`; no filename logic.
- `.codewhale/skills/Document_Keeper/reminder.py` — reads `due_documents()` only; no filename construction/parsing.
- `.codewhale/skills/Agent_Runtime/agent_core.py` — `handle_image` threads `member` → LLM → `_apply_member` injects member into tool args → CLI receives `--member`. Ingest path is fully threaded.
- `.codewhale/skills/Agent_Runtime/paths.py` — `family_documents_dir(doc_type)` returns `Family/documents/<doc_type>/`; no filename logic.
- `.codewhale/skills/Agent_Runtime/wechat_ilink.py` — `handle_image` + `handle_file` resolve member and pass to `agent.handle_image(path, user, member)`.
- `.codewhale/skills/Agent_Runtime/telegram_bot.py` — both image and document handlers resolve member and pass to agent; member always populated before archive.
- `.codewhale/skills/OCR/ocr.py` — no document filename logic; OCR only.
- `.codewhale/skills/Remote_Backup/backup_sync.py` — no document filename parsing; files tracked by rel path only.
- `config.json` — `doc_types` list only; no filename format.
- No `migrate_storage.py` or `image_gc.py` exist in the live code tree.

### Tests
- `tests/test_document_keeper.py:52–63` — `test_store_file_returns_family_rel` asserts `Family/documents/lease/2026_Lease.pdf` (new format: no date, no doc_type).
- `tests/test_document_keeper.py:66–73` — `test_store_file_prefixes_member` asserts `Family/documents/passport/Alex_Lee_Renewal.pdf` (new format: member prefix).
- `tests/test_document_keeper.py:62` — comment: `# Long-term docs: filename is <member>_<safe_title><ext> — no date, no doc_type.` (documents the NEW convention).
- `tests/test_expense_tracker.py:684` — receipt path `Family/receipts/YYYY-MM/<date>_expense_x.jpg` — this is the **receipt** naming convention (unchanged, out of scope); receipts keep date-based names. Not a document filename.

### Live docs
- `.codewhale/skills/Document_Keeper/SKILL.md:~46` — `documents/<类型>/<成员>_标题.ext` (new format, Chinese description — correct).
- `.codewhale/skills/Agent_Runtime/SKILL.md` — describes inbox→classify flow; no stale document filename format.
- `FamilyAssistant.md` — no document filename format described.
- `README.md` — describes `data/Family/documents/<类型>/` directory only; no filename format.

## Archive docs with old-format references (Category D)

These three files under `docs/superpowers/` describe the old filename format. They are **point-in-time design records** and are not expected to be updated:

1. **`docs/superpowers/plans/2026-06-11-document-keeper.md`** — already has a banner: "⚠️ 历史设计文档 … 下文路径/配置键 … 多已过时". Contains old `_store_file` 3-arg signature (L918), old stem construction (L933), old 3-arg call (L955), and old Chinese filename format (L1692).

2. **`docs/superpowers/specs/2026-06-11-document-keeper-design.md`** — already has a banner: "⚠️ 历史设计文档 … 下文路径/配置键 … 多已过时". Contains old format at L81: `documents/<doc_type>/YYYY-MM-DD_<doc_type>_<title>.<ext>`.

3. **`docs/superpowers/specs/2026-06-19-pdf-ocr-ingest-design.md`** — no historical banner, but is a dated design spec. Contains old format at L94–95: `Family/documents/<type>/<date>_<type>_<title>.pdf`. The spec describes the PDF ingest flow as of its writing date; the `_store_file` implementation it references has since been updated.

## Assumptions

- Receipt filenames (e.g., `<date>_<title>.jpg` under `Family/receipts/YYYY-MM/`) follow their own convention with ISO dates — this is **unchanged and out of scope**. The `test_expense_tracker.py:684` assertion about receipt paths is correct and not flagged.
- `_store_file` is the **only** function that constructs document filenames. No other code in the repo parses, splits, or reconstructs document file_path values (confirmed by grep for parse/split/reconstruct patterns).
- The `docs/superpowers/` directory is an intentional archive of design artifacts; the two older files already carry historical-disclaimer banners. The PDF ingest spec (2026-06-19) does not have a banner but is considered a point-in-time record — its description of the then-current `_store_file` logic was accurate at time of writing.
- Live code under `.codewhale/skills/` and `tests/` is the authoritative reference; archive docs under `docs/superpowers/` are not runtime.
