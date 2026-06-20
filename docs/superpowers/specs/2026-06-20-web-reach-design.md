# Web_Reach skill — design

Date: 2026-06-20
Status: approved

## Goal

Give the agent runtime a read-only "what's happening out there" capability: latest
news / web search, read-and-summarize a URL, and summarize a YouTube video. The bot
fetches public content online, the existing DeepSeek LLM digests it, and reports back
to the user in their language.

## Why not load agent-reach directly

`agent-reach` (github.com/Panniantong/agent-reach) is an "agent as operator" meta-skill:
it installs upstream bins (yt-dlp, Jina reader, gh, opencli, mcporter/Exa, cookies) and
drops a `SKILL.md` the agent is expected to read and then improvise shell commands from.

The FamilyAssistant runtime is the opposite: a DeepSeek function-calling bot with a
**fixed tool whitelist** (`_TOOL_MAP` + `ALLOWED_COMMANDS`, `agent_core.py:264`). It never
runs arbitrary shell and never reads a SKILL.md at runtime. The bot ingests untrusted
WeChat/Telegram messages, so an arbitrary-command tool would be a prompt-injection → RCE
path.

Decision: reuse agent-reach's *upstream engines* (Jina reader, yt-dlp) but wrap a curated
subset as new **fixed tools**, idiomatic with the existing 6 skills.

## Scope (v1)

Minimal, no-auth sources. No cookies, no API keys, no Node/mcporter.

| tool (LLM name)      | subcommand            | engine                          |
|----------------------|-----------------------|---------------------------------|
| `web_search`         | `web-search --query`  | `GET https://s.jina.ai/<query>` |
| `web_read`           | `web-read --url`      | `GET https://r.jina.ai/<url>`   |
| `youtube_summarize`  | `yt-summary --url`    | `yt-dlp` (auto-subs → transcript)|

Out of scope (possible phase 2): X / Reddit / Bilibili / Xiaohongshu (need
Node/mcporter/opencli + cookie upkeep). If added, the N near-identical channel wrappers
are a good DeepSeek codegen-from-template fan-out.

## Component: `.codewhale/skills/Web_Reach/cli.py`

Stdlib `urllib` for HTTP; `yt-dlp` as a subprocess for YouTube. Mirrors the other skill
CLIs (argparse subcommands, prints text to stdout, exit 0).

- `web-search --query "..."` → URL-encode query, `GET s.jina.ai/<query>`, return cleaned
  markdown of top results.
- `web-read --url "..."` → `GET r.jina.ai/<url>`, return cleaned markdown of the page.
- `yt-summary --url "..."` → run `yt-dlp` to fetch auto/uploaded subtitles
  (`--write-auto-sub --write-sub --skip-download --sub-lang en.* --sub-format vtt`,
  to a temp dir), parse the `.vtt` to plain de-duplicated transcript text. If no subs:
  fall back to `yt-dlp --dump-json` title + description.

Cross-cutting rules for every subcommand:
- **Trim output to ~6000 chars** before printing (DeepSeek max_tokens is tight; long
  pages/transcripts blow context). Append `…[截断]` when trimmed.
- **Internal network timeout < 25s** (under `_run_cli`'s 30s subprocess cap) so we return
  a clean message instead of being killed at 30s.
- On any failure (network, no results, no subs+no metadata, rate-limited) print a short
  `[错误] …` line and exit 0 (the bot LLM relays it as "couldn't reach it").
- Optional `JINA_API_KEY` env → sent as `Authorization: Bearer` header for higher rate
  limits; absent = keyless free tier.
- No persistence, no member context (read-only public info).

## Wiring: `agent_core.py` (mirror notes/cal, ~5 edits)

1. `_REACH_COMMANDS = {"web-search", "web-read", "yt-summary"}`
2. `ALLOWED_COMMANDS |= _REACH_COMMANDS` (always on, like notes/cal — core capability)
3. `_cli_path`: `elif cmd in _REACH_COMMANDS: skill = "Web_Reach"`
4. Tool fns: `_tool_web_search/_tool_web_read/_tool_youtube_summarize` → `_run_cli(cmd, args)`
5. `TOOL_SCHEMAS` += 3 `_fn(...)` entries; `_TOOL_MAP` += 3 entries
6. System-prompt 行为准则 lines:
   - 用户问"最新新闻 / 外面在发生什么 / 帮我查一下 X" → `web_search`
   - 用户发链接让看 / 总结文章 → `web_read`
   - 用户发 YouTube 链接让总结 → `youtube_summarize`
   - 工具返回的是抓取到的原文，你据此用中文总结报告；抓取失败就如实说没查到。

## Data flow

user msg → DeepSeek selects tool → `cli.py` fetches + trims → stdout → tool result →
DeepSeek summarizes in user's language → reply (existing `⚙️` tool badge). Same shape as
OCR (skill returns text, LLM digests).

## Error handling

| case                         | behavior                                             |
|------------------------------|------------------------------------------------------|
| network fail / rate-limited  | `[错误] …` → LLM: "暂时没查到"                         |
| YouTube no subtitles         | fall back to title+description metadata               |
| no subs and no metadata      | `[错误] 该视频无字幕，无法总结`                        |
| output too long              | trim to ~6k chars + `…[截断]`                          |
| subprocess > 30s             | `_run_cli` returns `[错误] 超时` (already handled)     |

## Testing: `.codewhale/skills/Web_Reach/test_cli.py`

Mock network (no live calls in tests):
- vtt parse: strips timestamps/tags, de-dupes rolling caption lines → clean transcript
- trim cap: >6k input → ≤~6k output ending in `…[截断]`
- no-subs fallback: yt-dlp returns no vtt → uses metadata
- error path: HTTP error / empty result → `[错误]` line, exit 0
- arg plumbing: `web-search`/`web-read`/`yt-summary` parse flags correctly

## Dependencies

- `yt-dlp` (pip) — only new runtime dep. Search/read use stdlib only.
- Document in new `Web_Reach/SKILL.md` + a README line.

## Decisions / tradeoffs

- **Sync single reply** (consistent with all existing tools); no interim "查ing…" ack in
  v1. Bot is silent during the ~5–20s fetch. Possible later enhancement.
- **Char cap ~6k** before LLM digest.
- Jina is an external free service → can rate-limit; optional `JINA_API_KEY` mitigates.
- Fetched web text enters LLM context (mild injection surface) — but tools are fixed and
  read-only and the LLM cannot exec, so worst case is a poisoned *summary*, not RCE.
