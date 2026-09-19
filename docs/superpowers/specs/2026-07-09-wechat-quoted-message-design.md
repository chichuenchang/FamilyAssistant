# WeChat quoted/reply message visibility — design

Date: 2026-07-09
Status: approved

## Problem

When a family member quote-replies to a message in WeChat (e.g. replies "把这个记到日历"
to an earlier message), the bot passes only `msg.text` to the agent. The quoted
message's content is dropped, so the agent answers without the context the user is
pointing at.

## Facts

**Corrected 2026-07-10 from captured wire payloads** (the original "Facts" were
read off SDK source and turned out wrong — `quoted_title` was always `None`):

- The real iLink `ref_msg` carries **no `title` and no content**, only the
  quoted message's identity:
  `ref_msg.message_item = {msg_id, create_time_ms, type: 0, ...}` where
  `msg_id` equals the quoted message's top-level `message_id`.
- `IncomingMessage.quoted_title` (`raw_item["ref_msg"]["title"]`, weixin-ilink
  0.3.5) therefore always returns `None`; the SDK's `extract_text`
  `[引用: {title}]` path is dead code against the current wire format.
- Quote-replies do arrive as TEXT items carrying `ref_msg`; that part held.
- Consequence: quoted content must be resolved locally — cache recent inbound
  `message_id → text` and look the `msg_id` up (`_remember_msg`/`_quoted_text`
  in `wechat_ilink.py`, cache capped at 200).
- The server never reveals outbound `message_id`s: the send API response is an
  empty object `{}`, and the poll stream does not echo BOT-type messages
  (both verified 2026-07-10 via temporary `SEND_RESP` / `POLL_NONUSER` dumps).
  So quotes of bot replies are resolved by **timestamp matching** instead:
  `_send_reply` records each outbound text with its send time
  (`data/wechat_sent_msgs.json`, cap 100), and `_quoted_text` matches the
  quote's `create_time_ms` against it within a ±15 s window, injecting
  `我此前的回复「{text}」` (truncated to 200 chars). Only if both the msg_id
  cache and the timestamp match miss does it fall back to the
  `"{MM-DD HH:MM} 的一条消息（原文不可见…）"` placeholder (e.g. messages from
  before these caches existed). Both caches persist across restarts.

## Design

In `.codewhale/skills/Agent_Runtime/wechat_ilink.py`:

- Add a module-level helper `_with_quote(text, quoted_title)` that returns
  `[引用: {quoted_title}]\n{text}` when a quoted title is present, else `text`
  unchanged.
- In `handle_text`, pass `_with_quote(msg.text, msg.quoted_title)` to
  `agent.handle(...)` and include the quoted title in the debug log line.

No `agent_core.py` change: the agent receives the quote inline in the user
message, same as any other text.

## Telegram parity (added 2026-07-11)

Telegram's `reply_to_message` carries the quoted message's full `text`/`caption`
natively, so `telegram_bot.py` injects quotes with a trivial `_tg_quoted_text`
(no cache needed; media → `[图片]`/`[文件] name` placeholder; >200 chars
truncated). Same `[引用: …]\n` convention as WeChat.

## Limits (accepted)

- Quoting an image/file yields only the summary placeholder (e.g. `[图片]`),
  not the original bytes — the iLink ref payload carries no media handle.
- Test mode (`--mode test`) has no quote concept; unchanged.

## Testing

New `tests/test_wechat_quote.py` covering `_with_quote`: with title, without
title (None/empty), and text preserved verbatim.
