# Family Members — Design

Date: 2026-06-11
Status: approved

## Goal

Multiple family members access FamilyAssistant remotely (WeChat, Telegram). The system must
(1) know which member sent each message, (2) attribute ledger data to members, and
(3) silently block anyone not registered. Member registration happens only on the local
machine — Agent Runtime can read the registry but never modify it.

## Requirements

- Identify members by the channel id they message from (Telegram chat id, WeChat user id).
- Unknown channel id: drop the message silently — no reply, no LLM call. Zero
  prompt-injection surface for strangers.
- Registry managed locally via CLI commands that are excluded from the agent's command
  whitelist. The agent cannot add, change, or remove members.
- Shared family ledger: every member sees all data; rows carry a member tag for
  attribution and per-member summaries.
- Attribution is set by code from the resolved channel id, never by the LLM — a breached
  or confused model cannot spoof another member.

## 1. Member registry (config.json)

```json
"members": {
  "爸爸": { "telegram": ["123456789"], "wechat": ["wxid_abc"] },
  "妈妈": { "wechat": ["wxid_def"] }
}
```

- Member name is the key. Each channel type maps to a list of ids (one person may use
  several devices/accounts).
- Single source of truth, consistent with the project's config.json convention.
- Missing or empty `members` section means lockdown: every remote message is dropped.
  Safe by default.

## 2. Registration CLI (local-only)

New subcommands in `.codewhale/skills/Expense_Tracker/cli.py`:

| Command | Behavior |
|---------|----------|
| `member-add <name> --telegram <id> --wechat <id>` | Add member or append channel ids to an existing member. Either or both flags. Rejects a channel id already bound to a different member. |
| `member-list` | Print members and their bound channel ids. |
| `member-remove <name>` | Remove the member from the registry. Existing ledger rows keep the name string. |

- These commands read config.json, modify the `members` section, and write it back
  atomically (write temp file, replace).
- They are **not** added to `wechat.allowed_commands`. `agent_core` already rejects any
  CLI command outside that whitelist, so the agent cannot invoke them.

## 3. Identity resolution and transport gate

New module `.codewhale/skills/Agent_Runtime/members.py`:

- `resolve(channel: str, channel_id: str) -> str | None` — reads the registry from
  config.json, returns the member name or `None`.
- Loads config at import (same pattern as `agent_core._CONFIG`); restart after registry
  changes, consistent with the rest of the project.

Transport gate (primary defense):

- `telegram_bot.py`: before calling `agent.handle*`, resolve `("telegram", str(chat_id))`.
  `None` → log one local line, send nothing, skip the update.
- `wechat_ilink.py`: same with `("wechat", msg.from_user)` for text and image handlers.
- The unknown sender's text is never passed to the LLM.

Defense in depth (secondary):

- `Agent.handle(text, user, member)` and `Agent.handle_image(path, user, member)` gain a
  `member` parameter. Falsy member → return `""` without any LLM call. Covers future
  channels that forget the gate.

## 4. Data model

Add `member TEXT NOT NULL DEFAULT ''` to `transactions`, `deposits`, `transfers`,
`tax_filings`, plus index `idx_txn_member ON transactions(member)`.

- Migration: idempotent `ALTER TABLE ... ADD COLUMN` guarded by a PRAGMA table_info
  check, run at connection setup. Existing rows get `''` = family-level/unattributed.
- CLI write commands (`add`, `deposit-add`, `transfer-add`, `tax-add`) accept
  `--member <name>`; non-empty values are validated against the registry. Empty is
  allowed (local family-level entry).
- `list`, `summary`, `monthly` gain a `--member` filter; `summary` can group by member.
- Anti-spoof rule: `agent_core` appends `--member <resolved-name>` to whitelisted CLI
  write calls itself, after the LLM chooses the command and arguments. Any `--member`
  produced by the LLM is stripped first.
- The resolved member name is injected into the system context so the agent can address
  the person naturally. Conversation history stays keyed by channel id.

## 5. Error handling

- Unknown sender: silent drop + local log. Never reply (no information leak that a bot
  lives at this address).
- `member-add` with a channel id bound to another member: refuse with a clear message.
- `--member` value not in registry (local CLI misuse): refuse with member-list hint.
- Corrupt/missing config.json: registry resolves nothing → lockdown.

## 6. Testing

- `members.resolve`: known id, unknown id, multiple ids per member, missing members
  section, corrupt config.
- CLI: member-add (new, append, duplicate-id rejection), member-list, member-remove;
  `--member` validation on writes; `--member` filter on reads.
- Migration: fresh DB has columns; legacy DB (without columns) migrates idempotently;
  running migration twice is a no-op.
- Transport gate: unit-test the gate function used by telegram_bot (unknown id → no
  agent call, no send) with mocks.
- Existing 20 tests stay green.

## Out of scope

- Per-member visibility/permissions (everyone sees the shared ledger).
- Remote member management of any kind.
- Hot-reload of the registry without restart.
- Per-member budgets or allowances (future idea).
