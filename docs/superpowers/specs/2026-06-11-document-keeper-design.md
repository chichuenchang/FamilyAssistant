# Document Keeper — Design

Date: 2026-06-11
Status: approved

## Goal

A new skill that ingests, indexes, and stores important family documents (lease contracts,
insurance policies, SIN, health cards, etc.), tracks their dates, and reminds members when
action is due. Documents arrive as photos via chat channels (or local file paths), are
OCR'd and indexed, then archived under a dedicated documents directory — the same way
receipts are handled today, but with date tracking and reminders on top.

User decisions made during design:

- Reminders: **both** on-demand (`doc-due` CLI) and proactive daily push via existing
  chat transports.
- Privacy: **cloud OCR for everything** — the user accepts that all document images
  (including SIN/passport) go through Tencent OCR and extracted text through DeepSeek.
- Architecture: **new skill, shared DB** — follows every existing project convention.

## Requirements

- Ingest a document from a chat photo or local file path; store the original file.
- Auto-extract metadata (type, title, issuer, document number, issue/expiry dates) via
  the existing OCR skill + LLM extraction; user confirms or corrects.
- Index documents searchable by type, member, keyword, and status.
- Track expiry/action dates; surface documents due within a lead window.
- Remind proactively once per day through existing WeChat/Telegram transports — no new
  daemon process.
- Deletion is local-only (excluded from the agent command whitelist), same posture as
  `member-*` commands.

## 1. Skill layout

```
.codewhale/skills/Document_Keeper/
├── SKILL.md      ← skill doc (model, CLI reference, query patterns, boundaries)
├── models.py     ← reads config.json once at import: doc_types, documents_dir,
│                   reminder_lead_days; emergency fallbacks if config missing
├── db.py         ← documents table in data/ledger.db (shared DB_PATH from config)
├── cli.py        ← doc-add / doc-list / doc-show / doc-due / doc-update / doc-ack /
│                   doc-remove
└── reminder.py   ← due-date scan + once-per-day gate, called by transports
```

Self-contained, standard library + SQLite only, same `sys.path` pattern as
Expense_Tracker.

## 2. Data model

One `documents` table in the existing `data/ledger.db`. Idempotent
`CREATE TABLE IF NOT EXISTS` at connection setup, consistent with existing tables.

| column | type | meaning |
|--------|------|---------|
| id | INTEGER | primary key |
| doc_type | TEXT | one of config `doc_types` (lease, insurance, health, id_document, other) |
| title | TEXT | human name, e.g. "2026 apartment lease" |
| member | TEXT | empty = family-level (same convention as ledger tables) |
| issuer | TEXT | landlord / insurer / government agency |
| doc_number | TEXT | policy number, SIN, card number |
| issue_date | TEXT | ISO date, nullable |
| expiry_date | TEXT | ISO date, nullable (a SIN never expires) |
| action_note | TEXT | what to do when due, e.g. "give 60-day notice" |
| remind_days | INTEGER | per-doc lead override; NULL = config `reminder_lead_days` |
| acknowledged | INTEGER | 0/1; set by `doc-ack`, auto-reset to 0 when expiry_date changes |
| file_path | TEXT | relative path under documents dir |
| ocr_text | TEXT | full OCR text — enables keyword search |
| data | TEXT(JSON) | flexible extra fields (same pattern as tax_filings.data) |
| status | TEXT | `active` / `expired` / `archived` / `superseded` |
| notes | TEXT | free notes |
| created_at | TEXT | creation timestamp |

## 3. File storage

- New top-level directory from config `documents_dir` (default `documents`), organized
  by type: `documents/<doc_type>/YYYY-MM-DD_<doc_type>_<title>.<ext>`.
- `file_path` stored relative to project root, same as receipts.
- Original file is never modified; re-ingesting the same file is caught by a duplicate
  check (same doc_type + doc_number, or same file content hash when doc_number empty;
  the SHA-256 hash is stored under a `file_sha256` key in the `data` JSON field) and
  refused unless `--force`.

## 4. Ingestion flow

1. User sends a document photo via WeChat/Telegram with intent ("this is our lease"),
   or supplies a local path.
2. Transport saves the image; the agent moves/copies it into
   `documents/<doc_type>/...` once the type is known.
3. OCR skill extracts full text (Tencent); DeepSeek extraction proposes doc_type, title,
   issuer, doc_number, issue/expiry dates.
4. Agent calls `doc-add` with extracted values, `--ocr-text` for the index, and the
   code-injected `--member` (LLM-provided member stripped, same anti-spoof rule as
   ledger writes).
5. Agent replies with the extracted summary and detected dates; user corrects with
   `doc-update` if extraction was wrong.

PDFs: stored and indexed from manually supplied metadata only (no OCR path for PDFs in
this iteration; transports currently download photos, not files).

## 5. CLI reference

| Command | Behavior |
|---------|----------|
| `doc-add --type --title [--member --issuer --number --issue-date --expiry --action-note --remind-days --file --ocr-text --data --notes]` | Insert document row. Validates doc_type against config, member against registry, dates as ISO. Duplicate check as in §3. |
| `doc-list [--type --member --keyword --status --due]` | Table of documents. `--keyword` does LIKE search over title/ocr_text/notes. Default hides `archived`/`superseded`. |
| `doc-show --id N` | Full metadata of one document, including file path and OCR text excerpt. |
| `doc-due [--days N]` | Documents where `status=active`, expiry_date is set, and `expiry_date − lead ≤ today` (lead = per-doc remind_days, else config default). Unacknowledged first; includes already-expired. |
| `doc-update --id N [any metadata flag]` | Update fields. Changing expiry_date resets `acknowledged` to 0. |
| `doc-ack --id N` | Mark current reminder acknowledged — daily push skips it until expiry changes. |
| `doc-remove --id N [--delete-file]` | Delete the row; file removed only with explicit flag. **Local-only** (not whitelisted). |

All commands except `doc-remove` are added to `wechat.allowed_commands`.

## 6. Reminders

On-demand: `doc-due` as above — the agent runs it when asked "anything expiring?".

Daily push (`reminder.py`):

- `due_message() -> str | None` — runs the `doc-due` query for unacknowledged documents;
  returns a formatted reminder message, or `None` when nothing is due.
- `should_run_today() -> bool` — compares today against the last-run date persisted in
  `data/.doc_reminder_state`; updates it when a run happens. Crash-safe: state is
  written only after a successful push.
- Transport integration: `telegram_bot.py` and `wechat_ilink.py` call
  `reminder.check_and_push(send_fn)` once per poll iteration; it no-ops unless the day
  changed and something is due. Push goes to every registered member's channel ids
  (from the members registry). A transport with no registered ids for its channel sends
  nothing.
- Reminder repeats daily within the lead window until the document is acknowledged,
  updated with a new expiry, or archived.

## 7. Config additions

```json
"documents_dir": "documents",
"doc_types": ["lease", "insurance", "health", "id_document", "other"],
"reminder_lead_days": 30
```

Plus the new `doc-*` commands (minus `doc-remove`) appended to
`wechat.allowed_commands`. Read once at import by `models.py` with emergency fallbacks,
consistent with the single-source-of-truth principle.

## 8. Agent integration

- `FamilyAssistant.md` skills table gains a Document Keeper row with trigger words
  (文档、合同、保险、证件、到期、提醒、租约).
- `agent_core` needs no structural change: documents flow through the existing
  whitelisted-CLI mechanism, member injection already applies to all write commands.
- Image routing: an incoming photo is a receipt by default (current behavior); the
  agent treats it as a document when the user's caption/context says so, and then runs
  the ingestion flow of §4 with the file stored under documents_dir instead of receipts.

## 9. Error handling

- Invalid doc_type / member / date format: refuse with clear message, exit code 1, no
  write — same contract as Expense_Tracker validation.
- OCR unavailable (no Tencent keys): store the file, create the row with manual
  metadata, leave ocr_text empty, tell the user extraction was skipped.
- Duplicate document: refuse with the existing row id; `--force` overrides.
- Missing/corrupt config: fallback doc_types (`other` only), documents_dir `documents`,
  lead 30 days.
- Reminder push failure: state file not updated, so the next poll retries; one local
  log line.

## 10. Testing

- db: insert/list/show/update/remove round-trips; keyword search; duplicate detection
  (doc_number and content-hash paths); idempotent table creation on an existing
  ledger.db.
- Due logic: per-doc remind_days override vs config default; expired docs included;
  acknowledged docs excluded; ack reset on expiry change.
- reminder: `should_run_today` date gate; `due_message` formatting; no state update on
  push failure.
- CLI validation: bad type/date/member rejected; `doc-remove` deletes file only with
  `--delete-file`.
- Config fallbacks when config.json missing.
- Existing test suite stays green.

## Out of scope

- Document versioning/diffing (a new lease is a new row; old one marked `superseded`).
- Encryption at rest.
- Full-text search engine (SQLite LIKE is enough at family scale).
- PDF OCR / transport file (non-photo) download.
- Recurring reminders unrelated to documents (general reminder system is a separate
  future skill).
