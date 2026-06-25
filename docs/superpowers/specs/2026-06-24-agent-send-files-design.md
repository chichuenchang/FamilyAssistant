# Agent Sends Documents / Files — Design

> Date: 2026-06-24
> Skill: Agent_Runtime (transports + agent_core) + Document_Keeper
> Status: approved

## Problem

The agent can now send chart **images** back to the user (via the `\x01IMG:`
sentinel + transport `_send_reply`). Users also want the agent to send **files**:
"send me my lease PDF", "send me that receipt". The image pipeline generalizes to
arbitrary attachments.

## Goals

- `send_document(id)` — send an archived Document_Keeper file by its id.
- `send_file(path)` — send an arbitrary data file by a data-relative path
  (generated CSV, a receipt image, etc.).
- Deliver over both transports (WeChat, Telegram) and show in CLI test mode.
- Preserve the privacy model: `send_file` cannot reach another member's private dir.

## Non-goals

- Sending files from outside `data_root`.
- Generating/transforming files (the file must already exist).
- Voice/video sending.
- Re-send/delivery-retry.

## Mechanism — generalize the attachment sentinel

Today: `IMG_SENTINEL = "\x01IMG:"`, collected for `visualize_data`, split by
`split_reply -> (text, imgs)`. Generalize to documents:

- Add `DOC_SENTINEL = "\x01DOC:"`.
- `split_reply(reply) -> tuple[str, list[str], list[str]]` returns
  `(text, images, docs)` (3-tuple; existing 2-tuple callers updated).
- Collection in `Agent.handle()` keyed by tool kind:

```python
_IMAGE_TOOLS = {"visualize_data"}
_DOC_TOOLS   = {"send_document", "send_file"}
```

In the tool loop, a successful (`result` not starting `[错误]`) call appends to
`produced_images` (image tools) or `produced_docs` (doc tools). After `final` is
built, append one sentinel line per item (`\x01IMG:`/`\x01DOC:` + path).

Sentinels are code-controlled (never LLM text); `\x01` (SOH) never occurs in real
replies. The path → user pipeline stays pure code: a hallucinated path cannot
leak a file (transport gate) or break delivery.

## Two tools (`agent_core.py`)

Both are **member-forced** (resolved member injected; LLM cannot spoof). Folded
into a single member-forced set with the note/sheet tools.

### `send_document(id)`

- LLM gets `id` from `list_documents` / `show_document`.
- Handler runs new CLI `doc-file --id N` (Document_Keeper), which prints the doc's
  data-relative `file_path`, or exits 1 + `[错误] 文档无文件` (no file / missing
  doc). Documents are family-shared archives → naturally bounded; no per-member
  gate needed for `send_document`.
- On success the printed rel path becomes the tool result → collected → `\x01DOC:`.

### `send_file(path)`

- Handler validates **in-process** in `agent_core` (no mutation, no subprocess):
  - Resolve `path` (rel via `paths.resolve_rel`, or absolute) to an absolute path.
  - Must `exists()` and be a file.
  - Must be under `paths.data_root()` AND within `paths.family_dir()` **or**
    `paths.member_dir(member)` (gate A — blocks other members' private dirs).
  - Pass → return `paths.to_rel(abs)`; fail → `[错误] 路径不允许或文件不存在`.
- `member` is injected by the member-forcing layer.

A shared helper `_resolve_sendable(path, member) -> str | None` implements the
gate (returns rel path or None); `_tool_send_file` maps None → the error string.

## CLI: `doc-file` (Document_Keeper)

```
doc-file --id N
```
- `d = doc_db.get_document(N, db_path=_DB_OVERRIDE)`.
- `d` is None or `d["file_path"]` empty → print `[错误] 文档无文件` to stderr,
  `sys.exit(1)`.
- else print `d["file_path"]` (data-relative) to stdout.
- Read-only; not in `_BACKUP_WRITE_COMMANDS`. Test override env `DOC_KEEPER_DB`.

Registered in Document_Keeper `cli.py` subparsers + dispatch.

## Agent wiring (`agent_core.py`)

- `_DOC_FILE_COMMANDS = {"doc-file"}`; `_cli_path` routes it to Document_Keeper
  (extend the existing `_DOC_COMMANDS` branch condition); `ALLOWED_COMMANDS |=
  _DOC_FILE_COMMANDS`.
- Handlers:
  - `_tool_send_document(args)` → `_run_cli("doc-file", {"id": ...})`.
  - `_tool_send_file(args)` → in-process gate via `_resolve_sendable`; returns rel
    path or error string.
- Register both in the tool dispatch map and the member-forced set.
- Tool schemas:
  - `send_document(id)` — "把已归档的文档（租约/保单/证件等）原件发给用户。先用
    list_documents/show_document 拿到 id。"
  - `send_file(path)` — "把 data 目录内的一个文件发给用户（路径为 data 相对路径）。
    只能发家庭共享或你自己的文件。"
- `_IMAGE_TOOLS` / `_DOC_TOOLS` sets + `produced_docs` collection + `DOC_SENTINEL`.
- Prompt guidance: when the user asks to be sent a stored doc / a file, use
  `send_document` (after locating its id) or `send_file` (a data-relative path).

## Transport delivery

Each transport's `_send_reply` consumes the 3-tuple: send images, then docs, then
text. Same security gate at send time (resolved abs path exists AND under
`data_root`).

- **WeChat** (`wechat_ilink.py`): `msg.reply_image(p)` for images, `msg.reply_file(p)`
  for docs.
- **Telegram** (`telegram_bot.py`): `send_photo` (exists) for images; new
  `send_document(chat_id, path, caption="")` — `sendDocument` multipart upload
  (mirror of `send_photo`, field name `document`). Send docs after photos.
- **CLI test mode** (`wechat_ilink.run_test`): print `[图片] <p>` then `[文件] <p>`
  then text.

Filename = path basename. A missing/unsendable attachment is logged and skipped;
text still sends (delivery never crashes message handling).

Note: Telegram's `send_document` helper shares the name of the agent tool concept
but lives in the transport module — no collision (different modules).

## Privacy & safety

- `send_file` gate A blocks cross-member private files; only family-shared
  (`data/Family/…`) or the requesting member's own dir is sendable.
- `send_document` only exposes archived docs (already family-shared, curated).
- Transport re-checks `under data_root` + `exists` before every send — defense in
  depth against any bad path reaching a sentinel.
- Member injected by code for both tools (no LLM spoofing of identity).

## Testing

`tests/test_send_files.py` (+ updates to `tests/test_chart.py` for the 3-tuple).

- `doc-file` CLI: doc with file → prints its rel path (exit 0); doc without file
  / unknown id → exit 1. Use `DOC_KEEPER_DB` temp db (seed via `doc_db`).
- `send_file` gate (`agent_core._resolve_sendable` with a temp `DATA_ROOT` +
  seeded members): family path → rel; own-member path → rel; other-member private
  path → None; path outside data_root → None; missing file → None.
- `split_reply` 3-tuple: text+img+doc mixed → correct split; doc-only; img-only;
  passthrough (no sentinels) → `(text, [], [])`.
- Transport: Telegram `send_document` builds a valid multipart body (monkeypatch
  urlopen, assert boundary/fields/`document` part). `_send_reply` calls
  `reply_file` / `send_document` for the doc list.
- agent: `send_document` + `send_file` in tool map, schemas, member-forced set;
  `doc-file` in `ALLOWED_COMMANDS` and routed to Document_Keeper.

## Out of scope / future

- Sending multiple docs as an album/zip.
- Captions / cover text per attachment.
- Access to non-`data_root` paths (explicitly disallowed).
