# Consistency Audit — 2026-07-10 (post documents.db split + Form Filler)

Scope: docs/comments vs code after two features landed (Form_Filler on
`formFiller`, documents.db split + profiles on `docStoreSplit`). Method: grep
sweep for stale storage claims (`ledger.db`, privacy statements, layout
trees, dependency enumerations, command-family lists), cross-checked against
`paths.py` / `doc_models.py` / `agent_core.py`. Full test suite green before
and after (540 passed).

## Findings (all doc/comment drift; no code bugs)

1. `README.md` 目录结构 — member-private list lacked `forms/`. Fixed.
2. `README.md` requirements-optional line — missing pypdf/pypdfium2/Pillow. Fixed.
3. `README.md` skills tree — `Form_Filler/` directory absent. Fixed.
4. `Agent_Runtime/SKILL.md` 磁盘布局 — no documents.db, no forms/. Fixed.
5. `config.json` `_comment` — always-allowed command families missing
   `profile-*` / `form-*`. Fixed.
6. `migrate_storage.py` header — target layout said ledger.db holds 文档;
   added note that documents move on to documents.db. Fixed.
7. `doc_models.py` comment — "家庭账本" → "家庭文档库". Fixed.
8. `Document_Keeper/cli.py` test-hook comment — "真实账本" → "真实文档库". Fixed.

## Checked clean

- `Expense_Tracker` docs: ledger described as finance-only. ✔
- `Note_Keeper` docs: worksheet examples (房贷利率/保单号) don't conflict with
  profiles rule (personal identity facts → profiles). ✔
- `Remote_Backup` docs: mirror is file-level over data/, no per-DB
  enumeration to update. ✔
- `paths.py` docstring matches actual layout incl. documents.db + forms/. ✔
- System prompt behavior rules vs tools: profiles/save_note/form workflow
  consistent. ✔
- Live data end state: profiles 14 facts in documents.db; private worksheet
  deleted; ledger `documents_legacy` empty rename in place. ✔
