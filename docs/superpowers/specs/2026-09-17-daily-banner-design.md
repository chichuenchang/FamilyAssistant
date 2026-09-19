# Daily Banner — 设计

> 每天 08:20 推一条早报：未来 3 天日程 + 未完成待办 + 昨夜未读邮件。LLM 排优先级压缩，失败退模板。

## 挂载

新 skill `.codewhale/skills/Daily_Banner/`：`banner.py`（取数/成文/状态）+ `agent_tools.py`（`FAST_TICKS`）。
选 FAST（~20 秒）不选 SLOW（~10 分钟）：SLOW 会晚到 08:30。

## 触发

- 每拍先做廉价判定：`enabled` 关、未到 `time`、过了 `catchup_until`、本 `(频道, 成员)` 今天已推 → 直接返回。
- `catchup_until` 默认 12:00：bot 晚启动仍补推，午后启动当天跳过（不在 22:00 推"早报"）。
- 取数 + LLM 慢（10–30 秒），跑守护线程，不堵传输层轮询；进程内 guard 防同一 `(频道, 成员)` 并发。
- 失败不记状态，但进程内退避 `RETRY_S`=600 秒，免得每 20 秒打一次 LLM。

## 收件人

仅 `data/members.json` 里 `"banner": true` 的成员（opt-in）。名字属隐私，不进 git 跟踪的 `config.json`。
推给该成员本频道全部 id。微信 + Telegram 两进程都跑 = 各推一份（同 doc 提醒 / mail_watch）。

## 取数（按成员私有）

- 日程：远端同步平时只随消息触发，08:20 未必新鲜 → 先 `calendar_sync.refresh_range(member, "schedule", 今天, 今天+N-1)`，
  再 `cal_db.list_range(今天, 今天+N-1, kind="event")`。不用 `refresh_domain`：它拉过去 365 天，太重。
- 待办：`refresh_range(member, "tasks", …)`（待办域恒全量）后取全部 active 待办；逾期、N 天内到期在前，无期限在后，截 `TASK_CAP`。
- 远端刷新有 errors → 早报带一行"远端同步失败，数据可能旧"，照用本地缓存。
- 邮件：成员有 mail 块且凭据齐才取。`gmail_provider.search("in:inbox is:unread newer_than:1d", 15)`，
  只要发件人 + 主题，过 `mail_rules.match` 静音规则。外部内容 → `rt.fence(…, "mail")` 才进 LLM。
- 某源异常只丢该源，其余照推。三源全空 → 仍推一句"今天无安排"（证明早报活着）。

## 成文

`llm_client.chat(messages, None, model, effort)`，无工具；model/effort 取 `llm_client.settings(load_overrides(), "")`
（环境变量 > 默认）。system prompt：中文、≤15 行、先冲突/逾期/今天，再需回复的邮件，末行一条最该做的事；
围栏内容只作数据。返回 `None`/空/异常 → 确定性模板。

## 配置

`config.json`：

```json
"daily_banner": {"enabled": true, "time": "08:20", "catchup_until": "12:00", "lookahead_days": 3}
```

本机本地时间。状态 `data/.state/.daily_banner_state.json`：`{频道: {成员: "YYYY-MM-DD"}}`（点前缀不进备份）。

## 测试

`tests/test_daily_banner.py`，provider/LLM/时钟全打桩：
时间闸门（前/中/过 catchup）、每日一次、未 opt-in 跳过、推送失败不记状态 + 退避、
LLM 失败走模板、邮件入 LLM 前带围栏、远端错误带提示行、线程 guard 不并发。
