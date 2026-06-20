# Web_Reach

> 只读联网 skill。让 Agent 搜最新资讯、抓取并总结网页、转写 YouTube 视频字幕。抓取到的原文交给 Agent 的 LLM 总结后回复用户。

实现 `reach.py` + `cli.py` 就在本 skill 目录 `.codewhale/skills/Web_Reach/`。搜索/网页抓取仅用标准库（urllib，无 key）；YouTube 需 `yt-dlp`（缺失时优雅降级）。

无认证、无 API key（搜索走 DuckDuckGo 经 Jina 阅读器；可选 `JINA_API_KEY` 提升限频）。只读公开信息，不写库、不按成员隔离。

## 命令行调用（agent 经白名单子命令调用）

从项目根目录直接跑：

```bash
# 联网搜索最新资讯（DuckDuckGo 结果页经 r.jina.ai 清洗为 markdown）
python .codewhale/skills/Web_Reach/cli.py web-search --query "最新 AI 新闻"

# 抓取并阅读单个网页正文
python .codewhale/skills/Web_Reach/cli.py web-read --url "https://example.com/article"

# YouTube 视频字幕转写（无字幕回退标题+简介）
python .codewhale/skills/Web_Reach/cli.py yt-summary --url "https://www.youtube.com/watch?v=..."
```

输出为抓取到的原文/转写（截断到约 6000 字，DeepSeek max_tokens 偏紧）。失败打印 `[错误] …` 并 `exit 0`，让 Agent 自然地告诉用户"没查到"，不编造。

## Agent 工具映射

`agent_core.py` 把三个子命令注册成 function-calling 工具（固定白名单，非任意 shell）：

| 工具 (LLM) | 子命令 | 触发场景 |
|-----------|--------|---------|
| `web_search` | `web-search --query` | "最新新闻 / 外面在发生什么 / 帮我查一下 X" |
| `web_read` | `web-read --url` | 用户发链接让看/总结文章 |
| `youtube_summarize` | `yt-summary --url` | 用户发 YouTube 链接让总结 |

数据流：用户消息 → DeepSeek 选工具 → `cli.py` 抓取+截断 → stdout → DeepSeek 用中文总结 → 回复。

## API（`reach.py`，纯逻辑可注入测试）

| 函数 | 返回 | 说明 |
|------|------|------|
| `web_search(query, *, fetch)` | `str` | 经 Jina 阅读器抓 DuckDuckGo 结果页；空查询/失败/无结果返回 `[错误] …` |
| `web_read(url, *, fetch)` | `str` | 经 `r.jina.ai` 抓取清洗单页；空链接/失败返回 `[错误] …` |
| `summarize_youtube(url, *, get_subs, get_meta)` | `str` | 优先字幕转写，回退标题+简介，再无则 `[错误] …` |
| `parse_vtt(vtt)` | `str` | `.vtt` → 去时间轴/标签/连续重复的纯文字 |
| `trim(text, cap=6000)` | `str` | 截断并加 `…[截断]` 标记 |

网络适配器 `jina_fetch` / `ytdlp_subs` / `ytdlp_meta` 做真实 I/O，由 `cli.py` 注入纯逻辑；单测注入假 fetcher，不联网。

## 依赖

- 搜索 / 网页抓取：零外部包（标准库 urllib）。
- YouTube：`pip install yt-dlp`。**未安装时 `yt-summary` 优雅降级**（返回 `[错误] 该视频无字幕、无简介，无法总结`，不崩溃）。

## 配置（可选）

- `JINA_API_KEY`：设置后作为 `Authorization: Bearer` 头发给 `r.jina.ai`，提升免费限频。不设则用 keyless 免费档。

## 限制 / 注意

- 同步单条回复：抓取期间（约 5–20s）Bot 静默，完成后一次性回复（与其它工具一致）。
- 抓取到的网页文本会进入 LLM 上下文（轻度注入面）；但工具是固定只读、LLM 不能执行命令，最坏只是被污染的"总结"，非 RCE。
- 依赖外部免费服务（Jina / DuckDuckGo / YouTube），可能限频或偶发不可用 → 返回 `[错误]`。
