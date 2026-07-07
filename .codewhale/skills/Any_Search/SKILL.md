# Any_Search

> 高质量实时联网搜索 skill。比 Web_Reach 更准的联网搜索：支持垂直领域结构化结果与网页全文抽取。抓取到的结果交给 Agent 的 LLM 总结后回复用户。

实现 `anysearch.py`（纯逻辑，可注入测试）+ `cli.py`（真实 AnySearch JSON-RPC 适配器）就在本 skill 目录 `.codewhale/skills/Any_Search/`。

走 AnySearch API（JSON-RPC 2.0，`https://api.anysearch.com/mcp`）。**`ANYSEARCH_API_KEY` 可选**——未配置走匿名访问（限额较低但可用）。只读公开信息，不写库、不按成员隔离。作为 Web_Reach 的高质量替代：问最新资讯时优先 Any Search，Web Reach 作兜底。

## 命令行调用（agent 经白名单子命令调用）

从项目根目录直接跑：

```bash
# 联网搜索（通用）
python .codewhale/skills/Any_Search/cli.py any-search --query "最新 AI 新闻"

# 垂直领域结构化搜索（先用 any-subdomains 发现子域，再配 --domain/--sub_domain）
python .codewhale/skills/Any_Search/cli.py any-search --query "AAPL" --domain finance --sub_domain finance.quote

# 抓取并提取单个网页全文（markdown）
python .codewhale/skills/Any_Search/cli.py any-extract --url "https://example.com/article"

# 列出某垂直领域可用子域及参数
python .codewhale/skills/Any_Search/cli.py any-subdomains --domains finance,health
```

输出为抓取到的原文/结果（截断到约 6000 字，DeepSeek max_tokens 偏紧）。失败打印 `[错误] …` 并 `exit 0`，让 Agent 自然地告诉用户"没查到"，不编造。

## Agent 工具映射

`agent_core.py` 把三个子命令注册成 function-calling 工具（固定白名单，非任意 shell）：

| 工具 (LLM) | 子命令 | 触发场景 |
|-----------|--------|---------|
| `anysearch_search` | `any-search --query` | "最新新闻 / 帮我查一下 X / 行情 / 垂直领域查询" |
| `anysearch_extract` | `any-extract --url` | 用户发链接让看/总结文章 |
| `anysearch_subdomains` | `any-subdomains --domains` | 垂直搜索前发现可用子域 |

数据流：用户消息 → DeepSeek 选工具 → `cli.py` 抓取+截断 → stdout → DeepSeek 用中文总结 → 回复。

## 垂直领域

`AVAILABLE_DOMAINS`（17 个）：`general` · `resource` · `social_media` · `finance` · `academic` · `legal` · `health` · `business` · `security` · `ip` · `code` · `energy` · `environment` · `agriculture` · `travel` · `film` · `gaming`。

`--domain` 选领域后，`any-subdomains --domains <领域>` 发现该领域的子域路由键（如 `finance.quote`）及参数，再用 `--sub_domain` / `--sub_domain_params` 做结构化搜索。

## API（`anysearch.py`，纯逻辑可注入测试）

| 函数 | 返回 | 说明 |
|------|------|------|
| `search(query, *, call, domain, sub_domain, sub_domain_params, max_results)` | `str` | 通用或垂直搜索；空查询/失败返回 `[错误] …` |
| `extract(url, *, call)` | `str` | 抓取单页全文为 markdown |
| `subdomains(domains, *, call)` | `str` | 列出垂直领域子域/参数 |
| `parse_sdp(value)` | `dict` | 解析 `sub_domain_params`：dict 透传 / JSON / `key=value,key2=value2` |
| `trim(text, cap=6000)` | `str` | 截断并加 `…[截断]` 标记 |

网络适配器 `anysearch_call`（POST JSON-RPC 2.0 `tools/call`）做真实 I/O，由 `cli.py` 注入纯逻辑；单测注入假 caller，不联网。

## 配置（可选）

- `ANYSEARCH_API_KEY`：优先级 环境变量 > 本 skill 目录 `.env`（从 `.env.example` 复制，`.env` 已 gitignore）> 匿名。设置后提升限额。申请：`https://anysearch.com/console/api-keys`。

## 限制 / 注意

- 同步单条回复：抓取期间（约数秒）Bot 静默，完成后一次性回复（与其它工具一致）。
- 抓取到的网页文本会进入 LLM 上下文（轻度注入面）；工具固定只读、LLM 不能执行命令，最坏只是被污染的"总结"，非 RCE。
- 依赖 AnySearch 外部服务，可能限频或偶发不可用 → 返回 `[错误]`，Agent 可回退 Web_Reach。
