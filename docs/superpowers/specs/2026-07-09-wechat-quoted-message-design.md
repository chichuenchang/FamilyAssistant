# WeChat quoted/reply message visibility — design

Date: 2026-07-09
Status: approved

## Problem

When a family member quote-replies to a message in WeChat (e.g. replies "把这个记到日历"
to an earlier message), the bot passes only `msg.text` to the agent. The quoted
message's content is dropped, so the agent answers without the context the user is
pointing at.

## Facts

- weixin-ilink 0.3.5 already parses the quote: `IncomingMessage.quoted_title`
  returns `raw_item["ref_msg"]["title"]` — the replied-to message's summary
  (full text for text messages, `[图片]`-style placeholder for media).
- The SDK's own `WeixinClient.extract_text` convention is
  `[引用: {title}]\n{text}`.
- Quote-replies always arrive as TEXT items carrying `ref_msg`; image/file/voice
  handlers never see quotes.

## Design

In `.codewhale/skills/Agent_Runtime/wechat_ilink.py`:

- Add a module-level helper `_with_quote(text, quoted_title)` that returns
  `[引用: {quoted_title}]\n{text}` when a quoted title is present, else `text`
  unchanged.
- In `handle_text`, pass `_with_quote(msg.text, msg.quoted_title)` to
  `agent.handle(...)` and include the quoted title in the debug log line.

No `agent_core.py` change: the agent receives the quote inline in the user
message, same as any other text.

## Limits (accepted)

- Quoting an image/file yields only the summary placeholder (e.g. `[图片]`),
  not the original bytes — the iLink ref payload carries no media handle.
- Test mode (`--mode test`) has no quote concept; unchanged.

## Testing

New `tests/test_wechat_quote.py` covering `_with_quote`: with title, without
title (None/empty), and text preserved verbatim.
