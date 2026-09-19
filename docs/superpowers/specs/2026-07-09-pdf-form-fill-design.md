# PDF Form Fill — Design

Date: 2026-07-09
Status: Approved (sections approved in conversation; blanket approval through implementation)

## Goal

Agent receives a PDF form over WeChat/Telegram, reads its fields, asks the user
**one field per message** (always asks, even when a value is known — known values
are offered as suggestions, never silently filled), then renders the completed
PDF and sends it back to the user.

## Scope

- **Tier 1 — fillable PDFs (AcroForm):** read field names/types/options, fill
  values, output stays vector.
- **Tier 2 — flat/scanned PDFs (no form fields):** render pages to images,
  OCR with coordinates (Tencent OCR polygons), LLM infers field labels and
  blank-anchor positions, stamp answer text onto page images, reassemble PDF.
- Out of scope: digital signatures, XFA-only form logic, form validation rules
  beyond field type (checkbox/choice), auto-submission anywhere.

## Architecture

New skill directory `.codewhale/skills/Form_Filler/`:

```
SKILL.md         # skill doc, same pattern as other skills
cli.py           # argparse subcommands (see CLI below)
form_fill.py     # AcroForm read + fill (pypdf)
form_overlay.py  # flat PDF: render (pypdfium2) → OCR polygons → stamp (Pillow)
form_session.py  # session JSON CRUD under data/<member>/forms/
```

Agent tools in `agent_core.py` are thin `_run_cli` wrappers, member injected by
code (`_apply_member`) so the LLM cannot cross member boundaries.

### Data flow

1. PDF arrives via existing transport path → member inbox.
2. LLM calls `fill_form_scan` → tier detection:
   - AcroForm fields present → tier 1, fields extracted, session created.
   - No fields → tier 2: pages rendered + OCR'd, OCR lines with polygons
     printed; LLM infers fields and calls `fill_form_define_fields`.
3. Session persisted at `data/<member>/forms/<id>.json`.
4. Ping-pong loop: `fill_form_next` → LLM asks user that one field (with
   suggestion from member registry / notes / worksheets when known) → user
   answers → `fill_form_set_answer` → next.
5. All answered (or user says leave rest blank) → `fill_form_render` → filled
   PDF at `data/<member>/forms/<id>_filled.pdf` → path printed → existing
   `DOC_SENTINEL` mechanism sends the file back.

Session state lives on disk, so the flow survives history trimming, `/clear`,
and process restarts. `form-list` allows resuming.

## Session JSON

```json
{
  "id": "20260709_143000_a1b2",
  "member": "Jim",
  "source_pdf": "Jim/inbox/2026-07/xxx.pdf",
  "kind": "acroform | flat",
  "status": "defining | collecting | done | cancelled",
  "created": "2026-07-09T14:30:00",
  "fields": [
    {
      "name": "family_name",
      "label": "Family Name 姓",
      "type": "text | checkbox | choice",
      "options": ["..."],
      "page": 0,
      "anchor": {"x": 120, "y": 340, "w": 200, "h": 18},
      "value": null,
      "asked": false
    }
  ],
  "render_path": null
}
```

- `anchor` only for flat forms, in rendered-image pixel coordinates.
- `status: "defining"` only for flat forms between scan and define-fields.

## CLI (cli.py subcommands)

- `form-scan --file <path> --member <m>` — tier detect. AcroForm: extract
  fields, create session, print field list + session id. Flat: render pages,
  OCR with polygons, print OCR lines with coordinates + page dimensions,
  create session with empty fields, status `defining`.
- `form-define --session <id> --fields <json>` — flat only. LLM passes the
  field list it inferred (label, page, anchor). Anchors validated to be within
  page bounds. Moves status to `collecting`.
- `form-next --session <id>` — print next unanswered field (name/label/type/
  options). LLM adds its own suggestion from context when asking the user.
- `form-set --session <id> --field <name> --value <v>` — validate by type
  (checkbox: on/off; choice: value must be in options), save.
- `form-render --session <id>` — tier 1: pypdf fill + NeedAppearances;
  tier 2: Pillow stamps text at anchors onto rendered page images, multi-page
  PDF. Prints data-relative output path (sendable via send_file gate).
- `form-list --member <m>` — sessions with age/status, for resuming.
- `form-cancel --session <id>` — mark cancelled.

## Agent tools

`fill_form_scan`, `fill_form_define_fields`, `fill_form_next`,
`fill_form_set_answer`, `fill_form_render`, `fill_form_list`,
`fill_form_cancel`.

System prompt additions (workflow rules):

- One field per message, strict ping-pong.
- Always ask, even when the value is known; offer known value as a suggestion
  ("回复'对'或给出正确值"). Never fill silently, never invent values.
- Render only after all fields are answered or the user explicitly says to
  leave the rest blank.

## Tier 2 stamping detail

- Anchor = blank area right of (or on the blank line after) the label polygon;
  the LLM decides from OCR layout when calling `form-define`.
- Font: `C:\Windows\Fonts\msyh.ttc`, fallback DejaVu; size scaled to anchor
  height.
- Value drawn in black; no box/border drawn.
- Output is raster (input was already scanned); tier 1 output remains vector.

## Error handling & degradation

Project convention: optional deps degrade gracefully.

- pypdf missing → `form-scan` exits with "pip install pypdf" hint.
- pypdfium2/Pillow missing → tier 1 still works; flat scan prints install hint.
- Tencent OCR unconfigured → flat tier unavailable, message mirrors existing
  OCR fallback wording.
- Encrypted PDF → detected, user told it cannot be filled.
- XFA-only forms → treated as flat if renderable, else reported unsupported.
- `form-set` unknown field / invalid choice → `[错误] ...`, LLM re-asks.
- Render: value too long for anchor → shrink font to fit, minimum 8 px, then
  truncate + warn in output.
- Stale sessions: `form-list` shows age; no auto-delete.
- Multiple concurrent sessions per member allowed; LLM tracks the current
  session id in conversation; after `/clear`, resume via `form-list`.

## Dependencies

Add to `requirements-optional.txt` (with per-dep comments matching file style):

- `pypdf` — AcroForm read/fill (tier 1).
- `pypdfium2` — flat PDF page rendering (tier 2).
- `Pillow` — text stamping + multi-page PDF assembly (tier 2).

All BSD/Apache-licensed. Core remains stdlib-only.

## Testing

pytest, `tests/`, matching existing optional-dep skip patterns:

- `test_form_session.py` — session CRUD, member scoping, next/set/validate
  (stdlib only).
- `test_form_fill.py` — build tiny AcroForm fixture with pypdf; scan → set →
  render → assert field values in output (skip without pypdf).
- `test_form_overlay.py` — fake OCR polygons, define fields, stamp onto blank
  image PDF, assert output file + page count (skip without pypdfium2/Pillow).
- Unit tests: anchor validation, font shrink, choice validation.
