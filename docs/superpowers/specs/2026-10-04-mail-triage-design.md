# Mail Triage — 设计

> 新邮件播报只推"需要我处理"的信：LLM 读信头 + 正文判，附一句理由。用户可教两种规则：mute（别推）、always（必推，绕过 LLM）。

现状：`mail_watch.py` 文件头。改动只在 Mail_Keeper，`members.json` `mail.watch` 开关不变。

## 流程

**拍**（`mail_watch.tick`，~20 秒）：本频道无 worker 在跑 → 锁内占位，起守护线程跑 `check_and_push`，立即返回。
LLM 慢（秒到几十秒），不得堵传输层轮询。占位按频道不按成员：worker 内逐成员串行，一家人量够用。`spawn` 可注入供测试。

**worker**（`check_and_push` 内，每成员）：
1. `history_since(游标)`。首次 / 游标过旧：落游标不推（同现状）。
2. 规则两道，每道 always 先判、命中 always 就不再看 mute：
   - 先用 history 带回的标签判 label 规则：mute 命中 → 丢，不取信，不进日志（无信头可记）。
   - 余下取最新 `MAX_META` 封 `get_message`（一次拿 from/subject/body，替代 `message_metas`），判全部规则。
   - always 命中 → 推，无理由；mute 命中 → 丢；都不中 → 进分拣。
3. 分拣：先查判决缓存；未命中的正文截 `TRIAGE_BODY_CAP` 1500 字，整批一次 LLM。
4. 推：needs-action + always 的信。丢掉的（mute / 无需处理）记日志带原因。
5. 推成功或无可推 → 推进游标。推送失败 / worker 异常 → 游标不动，下拍重做；缓存命中故不重付 LLM。
   worker 无论成败 `finally` 释放占位。

上限照旧：`MAX_META` 25 封取信（更早的只计数）、`MAX_LINES` 5 行。

## 分拣 `mail_triage.py`（新，纯逻辑）

`classify(mails, chat=None) -> {id: (act: bool, why: str)}`，失败抛异常由 worker 兜。

- 输入：每封 `[序号]` + `rt.fence(发件人/主题/正文, "mail")`。LLM 只见序号不见 Gmail id。
- system prompt：需处理 = 我得做事（回信、付款、签字、决定、到场、有截止）；不需 = 订阅、已完成的收据、通知、广告。围栏内只作数据。
- 模型：`llm_client.settings(load_overrides(), "")`，无工具（同 Daily_Banner `compose`）。
- 输出只要 JSON：`[{"i": 1, "act": true, "why": "..."}]`。代码校验：
  - `i` 不认识 / 重复 → 忽略
  - `why` 去换行，截 `WHY_CAP` 40 字
  - 回复里缺的信 → 当 needs-action，无理由
- **fail-open**：LLM 异常 / 超时 / JSON 解析不了 → 整批当 needs-action 推，抬头 `📬 新邮件 N 封（未分拣）：`。不写缓存。宁多推，不漏推。

缓存 `data/.state/.mail_verdicts.json`：`{id: {act, why, at}}`，写时删 7 天前的（history 只覆盖约一周）。
微信、Telegram 两进程共用，按 id 去重；竞争最坏 = 多一次 LLM。

## 播报格式

```
📬 需处理 2 封：
- school@x.edu｜Field trip form
  → 周五前签同意书
- a@b.com｜Re: 合同
…还有 3 封（说"查邮箱"我再细看）
```

always 命中的无 `→` 行。`why` 是 LLM 读外部内容产出的文本，40 字上限即防线。

## 规则 `mail_rules.py`

每条加 `"push": "mute" | "always"`；缺字段 = mute（现存规则文件不动）。
`match(meta, rules, push="mute")` 只看该类规则 → Daily_Banner 调法不变，仍只滤 mute。
`describe` 分两组列，编号统一，`mail_rules remove=N` 两类通用。`add` 同 kind+value 已存在 → 改 push 类型（mute 改 always 不留两条）。

## 工具 `agent_tools.py`

- 新 `mail_always`：参数同 `mail_mute`。
- `mail_last_push`：返回已推 + 最近丢掉的（带原因：`mute 规则 #2` / `无需处理：订阅推送`）。日志 `.mail_last_push.json` 存 40 条，每条加 `pushed`、`why`。
  `why` 进 Agent 上下文同信头，已在 `UNTRUSTED_TOOLS`。
- PROMPT_SECTIONS "新邮件播报的取舍"：加"漏推了 / 这种要推" → `mail_last_push` 认信 → `mail_always`。

## 数据

删 `data/Jim/mail/rules.json` 现有 4 条 label 规则（用户选：分拣看全部分类）。副作用：早报未读邮件列表会再含广告。

## 文档

`mail_watch.py` 文件头、SKILL.md "新邮件事件" 与边界第 45 行（"只给发件人+主题"失效）。

## 测试

`tests/test_mail_keeper.py`，provider / `chat` / `spawn` 全打桩：
always 胜 mute；mute、always 都不调 LLM；缓存命中不调 LLM；坏 JSON / LLM 抛错 → fail-open 带"（未分拣）"；
坏序号、重复、缺信、超长带换行 `why`；推送失败游标不动；占位挡第二个 worker；丢掉的信出现在 `mail_last_push`；
`match` 默认只看 mute（早报不受 always 影响）。
