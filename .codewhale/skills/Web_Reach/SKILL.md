# Web_Reach

> 只读联网 skill。让 Agent 搜最新资讯、抓取并总结网页、转写 YouTube 视频字幕。抓取到的原文交给 Agent 的 LLM 总结后回复用户。

实现 `reach.py` + `cli.py` 就在本 skill 目录 `.codewhale/skills/Web_Reach/`。搜索/网页抓取仅用标准库 urllib；YouTube 需 `yt-dlp`（缺失时优雅降级）。

搜索：`web_search` 一次调用并发跑 4 路——Serper EN、Serper 中文、AnySearch EN、AnySearch 中文（`reach.bilingual_search`），不论用户语言。中文网常更有用。Serper 报错/无结果回退 Parallel；AnySearch 走 `Any_Search/anysearch.py`。网页读取走 Jina 阅读器（keyless）。只读公开信息，不写库、不按成员隔离。

## 命令行调用（agent 经白名单子命令调用）

从项目根目录直接跑：

```bash
# 联网搜索：中英两组关键词（LLM 从请求提炼），输出 4 块 `## <源> · <语言>: <查询>`
python .codewhale/skills/Web_Reach/cli.py web-search --query_en "RTX 5090 price" --query_zh "RTX 5090 价格"

# 抓取并阅读单个网页正文
python .codewhale/skills/Web_Reach/cli.py web-read --url "https://example.com/article"

# YouTube 视频字幕转写（无字幕回退标题+简介）
python .codewhale/skills/Web_Reach/cli.py yt-summary --url "https://www.youtube.com/watch?v=..."
```

输出为抓取到的原文/转写（截断到约 6000 字，DeepSeek max_tokens 偏紧；`web-search` 每块 2000，共 ~8000）。失败打印 `[错误] …` 并 `exit 0`，让 Agent 自然地告诉用户"没查到"，不编造。

## Agent 工具映射

`agent_core.py` 把三个子命令注册成 function-calling 工具（固定白名单，非任意 shell）：

| 工具 (LLM) | 子命令 | 触发场景 |
|-----------|--------|---------|
| `web_search` | `web-search --query_en --query_zh` | 任何联网查询："最新新闻 / 价格 / 帮我查一下 X" |
| `web_read` | `web-read --url` | 用户发链接让看/总结文章 |
| `youtube_summarize` | `yt-summary --url` | 用户发 YouTube 链接让总结 |

数据流：用户消息 → DeepSeek 选工具 → `cli.py` 抓取+截断 → stdout → DeepSeek 用中文总结 → 回复。

## API（`reach.py`，纯逻辑可注入测试）

| 函数 | 返回 | 说明 |
|------|------|------|
| `bilingual_search(query_en, query_zh, *, serper, any_search)` | `str` | 4 路并发（`ThreadPoolExecutor`），每块 `trim` 到 `BLOCK_CAP`；空语言跳过；某路抛错只坏本块 |
| `web_search(query, *, search, fallback=None)` | `str` | `search(q)`/`fallback(q)` 返 `[{title,url,snippet}]`，格式化为编号结果；`search` 抛错/无结果时试 `fallback`，皆败返回 `[错误] …`（两边原因都写） |
| `web_read(url, *, fetch)` | `str` | 经 `r.jina.ai` 抓取清洗单页；空链接/失败返回 `[错误] …` |
| `summarize_youtube(url, *, get_subs, get_meta)` | `str` | 优先字幕转写，回退标题+简介，再无则 `[错误] …` |
| `parse_vtt(vtt)` | `str` | `.vtt` → 去时间轴/标签/连续重复的纯文字 |
| `trim(text, cap=6000)` | `str` | 截断并加 `…[截断]` 标记 |

网络适配器 `serper_search` / `parallel_search` / `jina_fetch` / `ytdlp_subs` / `ytdlp_meta` 做真实 I/O，由 `cli.py` 注入纯逻辑；单测注入假 fetcher，不联网。

## 依赖

- 搜索 / 网页抓取：零外部包（标准库 urllib）。
- YouTube：`pip install yt-dlp`。**未安装时 `yt-summary` 优雅降级**（返回 `[错误] 该视频无字幕、无简介，无法总结`，不崩溃）。

## 配置

- `SERPER_API_KEY` / `PARALLEL_API_KEY`（`web-search` 至少一个）：环境变量 > skill 目录 `.env`（模板 `.env.example`，gitignore）。缺一个即直接走另一个；两个都缺即报错，无 keyless 回退。
- 换掉 RapidAPI Real-Time Web Search（2026-11-12）：同 14 查询实测 Serper 中位 ~1.1s、与其共享 ~74% 链接（同为 Google）；RapidAPI ~7.5s。Parallel `advanced` ~2.2s，新闻最新、多一手来源，偶有重复链接。旧 DuckDuckGo 回退（链接为跳转、常夹广告）一并去掉。
- `web-search` 子进程上限 45s（`agent_tools.py` `CLI_TIMEOUTS`）：4 路并发，最慢 Serper 20s + Parallel 20s。

- `JINA_API_KEY`：设置后作为 `Authorization: Bearer` 头发给 `r.jina.ai`，提升免费限频。不设则用 keyless 免费档。

## 限制 / 注意

- 同步单条回复：抓取期间（约 5–20s）Bot 静默，完成后一次性回复（与其它工具一致）。
- 抓取到的网页文本会进入 LLM 上下文（轻度注入面）；但工具是固定只读、LLM 不能执行命令，最坏只是被污染的"总结"，非 RCE。
- 依赖外部服务（Serper / Parallel 按量计费 / Jina / YouTube），可能限频或偶发不可用 → 返回 `[错误]`。
