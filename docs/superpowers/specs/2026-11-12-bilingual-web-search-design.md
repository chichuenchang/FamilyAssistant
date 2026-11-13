# Bilingual web search

Every general web lookup: Serper + AnySearch, English + Chinese keywords, whatever language user writes. Chinese web often more useful.

## Approaches

- **A, picked**: one `web_search` tool, schema requires `query_en` + `query_zh`. LLM extracts keywords both languages; code fans out 4 searches concurrently. Fan-out guaranteed, one tool round.
- B, rejected: prompt rule telling LLM to call `web_search` + `anysearch_search` twice each. 4 sequential rounds, DeepSeek skips calls, ~24k chars context.

## Design

- `reach.bilingual_search(query_en, query_zh, *, serper, any_search)`: `serper(q)` / `any_search(q)` return result text. Runs Serper EN, Serper 中文, AnySearch EN, AnySearch 中文 in `ThreadPoolExecutor`. Each block trimmed to 2000 chars, labeled `## <source> · <lang>: <query>`. Total ~8000.
- One language empty: run other only. Both empty: `[错误] 空查询`. Block failing: its `[错误] …` line stays, other blocks still go out.
- No URL dedup: AnySearch returns free text, not structured results; overlap EN/ZH small.
- `serper` = existing `web_search` (Parallel fallback kept). `any_search` = `anysearch.search(q, call=anysearch_call)` (bootstrap mounts all skill dirs).
- CLI: `web-search --query_en … --query_zh …` (flag = schema key, `tool_runtime.py:59`). Timeout 45s stays: Serper 20 + Parallel 20 worst case, AnySearch 25, concurrent.
- Schema: `web_search` says "任何联网查询都用它"; `query_en`/`query_zh` say extract keywords, not translate whole sentence. `anysearch_search` reworded: vertical domain only (`domain` set).
- Reply language: user's. Chinese results folded into summary.

## Left to builder

Test shape, SKILL.md wording, `Agent_Runtime` references to `anysearch_search/web_search`.
