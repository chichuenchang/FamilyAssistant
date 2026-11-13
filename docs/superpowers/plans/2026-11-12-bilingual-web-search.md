# Bilingual web search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `web_search` fans out Serper + AnySearch × EN + 中文 in one tool call.

**Architecture:** Pure `reach.bilingual_search` takes two `str -> str` searchers, runs 4 jobs in `ThreadPoolExecutor`, labels + trims each block. `cli.py` injects existing `web_search` (Serper→Parallel) and `anysearch.search`.

**Tech Stack:** Python stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-bilingual-web-search-design.md`

## Global Constraints

- Block cap 2000 chars (`reach.BLOCK_CAP`). Block order: Serper EN, Serper 中文, AnySearch EN, AnySearch 中文.
- Header line: `## <Serper|AnySearch> · <EN|中文>: <query>`.
- CLI flags `--query_en` / `--query_zh`; `web-search` timeout stays 45s.
- Failures print `[错误] …`, exit 0.

## Review Focus

- One searcher raises (not returns `[错误]`): that block shows `[错误] 搜索失败：<msg>`, others intact.
- Only `query_zh` given (LLM omitted EN): run Chinese blocks only.
- Whitespace-only queries count as empty.
- Same query in both fields (user query already keyword-y, LLM lazy): still run both; no special case.
- CLI with neither flag: `[错误] 空查询`, exit 0 (both flags optional in argparse).

---

### Task 1: `bilingual_search` pure logic

**Files:**
- Modify: `.codewhale/skills/Web_Reach/reach.py` (after `web_search`)
- Test: `tests/test_web_reach.py` (new `class TestBilingualSearch`)

**Interfaces:**
- Produces: `bilingual_search(query_en: str, query_zh: str, *, serper: Callable[[str], str], any_search: Callable[[str], str]) -> str`; `BLOCK_CAP = 2000`.

- [ ] **Step 1: Write failing tests**

```python
class TestBilingualSearch:
    def _echo(self, tag):
        return lambda q: f"{tag}:{q}"

    def test_four_blocks_in_order(self):
        out = reach.bilingual_search("gpu price", "显卡 价格",
                                     serper=self._echo("S"), any_search=self._echo("A"))
        heads = [l for l in out.splitlines() if l.startswith("## ")]
        assert heads == ["## Serper · EN: gpu price", "## Serper · 中文: 显卡 价格",
                         "## AnySearch · EN: gpu price", "## AnySearch · 中文: 显卡 价格"]
        assert "S:gpu price" in out and "A:显卡 价格" in out

    def test_runs_concurrently(self):
        barrier = threading.Barrier(4, timeout=2)
        def wait(q):
            barrier.wait()
            return q
        reach.bilingual_search("a", "b", serper=wait, any_search=wait)  # BrokenBarrierError if serial

    def test_block_trimmed(self):
        out = reach.bilingual_search("a", "", serper=lambda q: "x" * 5000,
                                     any_search=lambda q: "y")
        assert "x" * reach.BLOCK_CAP + "…[截断]" in out and "x" * (reach.BLOCK_CAP + 1) not in out

    def test_one_language_only(self):
        out = reach.bilingual_search("  ", "显卡", serper=self._echo("S"), any_search=self._echo("A"))
        assert "EN" not in out and out.count("## ") == 2

    def test_both_empty(self):
        assert reach.bilingual_search(" ", "", serper=self._echo("S"),
                                      any_search=self._echo("A")) == "[错误] 空查询"

    def test_raise_isolated(self):
        def boom(q):
            raise RuntimeError("down")
        out = reach.bilingual_search("a", "b", serper=self._echo("S"), any_search=boom)
        assert out.count("[错误] 搜索失败：down") == 2 and "S:a" in out and "S:b" in out
```

Add `import threading` to test imports.

- [ ] **Step 2: Run** `pytest tests/test_web_reach.py -k Bilingual -q` — expect FAIL (`AttributeError: bilingual_search`).
- [ ] **Step 3: Implement** in `reach.py`. Jobs list built in order, skipping empty language; `ThreadPoolExecutor(max_workers=len(jobs))`, `pool.map`; each worker catches `Exception` into `[错误] 搜索失败：{e}`; block = header + `\n` + `trim(text.strip(), BLOCK_CAP)`; join `\n\n`.
- [ ] **Step 4: Run** `pytest tests/test_web_reach.py -q` — expect all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add bilingual search fan-out logic"`

### Task 2: CLI, schemas, docs

**Files:**
- Modify: `.codewhale/skills/Web_Reach/cli.py`, `.codewhale/skills/Web_Reach/agent_tools.py`, `.codewhale/skills/Any_Search/agent_tools.py`, `.codewhale/skills/Web_Reach/SKILL.md`, `.codewhale/skills/Any_Search/SKILL.md`, `.codewhale/skills/Agent_Runtime/agent_tools.py:67`, `.codewhale/skills/Agent_Runtime/SKILL.md:150`
- Test: `tests/test_web_reach.py` (`TestCli`, `TestAgentRegistration`)

**Interfaces:**
- Consumes: Task 1 `bilingual_search`; `reach.web_search(q, search=reach.serper_search, fallback=reach.parallel_search)`; `anysearch.search(q, call=anysearch.anysearch_call)`.

- [ ] **Step 1: Update tests**
  - `test_web_search_empty_query_prints_error_exit0`: args `"web-search", "--query_en", "  ", "--query_zh", " "` → `[错误] 空查询` in stdout, exit 0.
  - `test_missing_required_arg_exits_2`: replace with `test_web_search_no_flags_prints_error_exit0` (`_run_cli("web-search")` → exit 0, `[错误] 空查询`).
  - New `test_web_search_schema_requires_both_languages`: `web_search` schema in `agent_core.TOOL_SCHEMAS` has `required == ["query_en", "query_zh"]`.
- [ ] **Step 2: Run** `pytest tests/test_web_reach.py -k "Cli or Registration" -q` — expect FAIL.
- [ ] **Step 3: Implement**
  - `cli.py`: `web-search` flags `--query_en` / `--query_zh` (default `""`); call `bilingual_search`; `import anysearch`; docstring line updated.
  - `Web_Reach/agent_tools.py`: schema description — every online lookup uses this; system searches Serper + AnySearch in English and Chinese. `query_en` = English keywords, `query_zh` = 中文关键词, both extracted from request (not full-sentence translation), both required. `CLI_TIMEOUTS` comment: 4 searches concurrent, Serper+Parallel 40s worst.
  - `Any_Search/agent_tools.py`: `anysearch_search` description — vertical domain only (with `domain`); general lookups go `web_search`. Drop "比 web_search 更准/首选".
  - `Agent_Runtime/agent_tools.py:67` + `Agent_Runtime/SKILL.md:150`: say `web_search` (drop `anysearch_search` for plain fact lookups).
  - SKILL.md files: Web_Reach command example + tool table + one line on fan-out; Any_Search "问最新资讯时优先 Any Search" line becomes vertical-only.
- [ ] **Step 4: Run** `pytest -q` — expect all PASS.
- [ ] **Step 5: Live smoke** `python .codewhale/skills/Web_Reach/cli.py web-search --query_en "RTX 5090 price" --query_zh "RTX 5090 价格"` — expect 4 headed blocks (an `[错误]` block allowed only if that provider key/quota is down; report it).
- [ ] **Step 6: Commit** `git commit -m "Wire bilingual search into agent"`
