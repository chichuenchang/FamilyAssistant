# Mail Keeper

> 按成员私有的邮箱：查收、读全文、**两轮确认**后回信。只读 + 只发，不能删改信件。
> 当前 provider = Gmail REST v1（契约见 `gmail_provider.py` 文件头）。

```
Mail_Keeper/
├── gmail_provider.py  ← Gmail REST + OAuth（search / get_message / send_reply / history / --auth）
├── mail_draft.py      ← 待确认草稿 + 三道发信闸门（纯逻辑）
├── mail_watch.py      ← 新邮件播报（FAST_TICKS 轮询 history，按成员 opt-in）
├── mail_rules.py      ← 「这种别推」忽略规则（用户教出来的）
└── agent_tools.py      ← 7 个工具 + prompt 段落 + FAST_TICKS（无 cli.py，见下）
```

无 `cli.py`：发信闸门要本轮开始时间与用户原话（`__turn_at` / `__text`，由
`agent_core._apply_context` 注入 `CONTEXT_TOOLS`），子进程拿不到 → 工具在 bot 进程内跑。

## 发信闸门（`mail_draft.check`，代码强制）

邮件正文是外部内容（注入面），发信不可撤回 → 不靠 LLM 自觉：

1. 草稿 `created_at` 必须 < `__turn_at` —— 同一轮 `draft_reply` + `send_reply` 直接拒
   （被注入的邮件无法自问自答把信发出）
2. 本轮用户原话须命中 `mail_draft.CONFIRM_RE`
3. 草稿 30 分钟（`TTL_S`）过期

收件人不由 LLM 给：`gmail_provider.reply_recipient` 从原信 Reply-To/From 算
（泄密面只能回到原发件人）。草稿存 `data/.state/.mail_drafts.json`，每成员一条，不入备份。

## 开启（每个要用邮箱的成员各一次）

1. Google Cloud Console：启用 **Gmail API**（OAuth 客户端可复用 GCAL 的那个 Desktop app）。
2. `setx GMAIL_CLIENT_ID "..."`、`setx GMAIL_CLIENT_SECRET "..."`（新终端生效）。
   第二个成员用别的前缀（如 `GMAIL_WLI`），变量名与下面 `--prefix` 相应改。
3. `python .codewhale/skills/Mail_Keeper/gmail_provider.py --auth [--prefix GMAIL]`
   → 浏览器批准 → 按提示 `setx GMAIL_REFRESH_TOKEN "..."`。
4. `data/members.json` 给该成员加：
   `"mail": {"provider": "gmail", "cred_prefix": "GMAIL", "enabled": true, "watch": false}`。
   无 `mail` 块 = 无邮箱能力，工具拒绝；`watch: true` 才主动播报新邮件。
5. 重启 bot。

scope：`gmail.readonly` + `gmail.send`（**restricted** scope —— OAuth 应用发布状态若是
Testing，refresh token 7 天后失效需重授权；生产未验证状态个人用可长期有效，上限 100 用户）。

## 新邮件播报（`mail_watch.py`；默认关，成员 `mail` 块加 `"watch": true` 开）

只播报 **发件人 + 主题**（正文是注入面，未开口不该甩进聊天），不经 LLM。
游标 `data/.state/.mail_history.json`：`{频道: {成员: {history_id, at}}}`，按频道各一份
（微信/Telegram 都收得到）。确定性规则与常量见文件头。

**播报什么：全推，再按用户教的规则减**（`mail_rules.py`）。规则表空 = 每封都推；
用户说"这种以后别推" → Agent 调 `mail_mute` 落一条规则（`sender` / `domain` /
`subject`含词 / Gmail 分类 `label`），此后命中的信直接丢、游标照常前进。
规则 `data/<成员>/mail/rules.json`（跟人走、入备份）；`mail_rules` 看和撤销。

为何判断放代码而不是每封信问 LLM：便宜（一封信 0 token）、可解释（能列给用户看）、
可撤销，且播报路径不进 LLM = 主题行注入不到任何工具。代价是学不会"这封要我做事吗"
这类语义判断 —— 靠用户多说几句把规则教出来。

播报过的信头留在 `data/.state/.mail_last_push.json`（最近 `LAST_PUSH_KEEP` 条）：
播报不经 LLM，用户回头说"刚才那种别推"时，Agent 得靠 `mail_last_push` 才知道指哪封。

为何轮询而不用 Gmail 推送：

- Gmail 唯一的推送机制是 `users.watch` → Cloud Pub/Sub topic。**push 投递要公网 HTTPS 端点**
  （本 bot 跑家用机，没有）；剩下 pull 投递 = bot 自己轮询 Pub/Sub，还要多加
  Pub/Sub API、topic、订阅、给 `gmail-api-push@system.gserviceaccount.com` 发布权限、
  `pubsub` scope，且 `watch` 7 天过期要定期重续。
- 通知体只有 `emailAddress` + `historyId`，仍得回头调 `users.history.list` 才知道来了什么。
- 故直接轮询 `users.history.list?startHistoryId=`（2 quota units/次，用户配额 250 units/秒）：
  接在 `FAST_TICKS`（~20 秒一轮）上即可，延迟与 push 同级，零 GCP 额外配置。
  `historyId` 太旧（Gmail 只保证约一周）会 404 → 用 `getProfile` 的 historyId 重新起点。

## 边界

- ❌ 主动查邮箱（工具只在用户开口时动；播报是独立的 `watch` 开关，且只给发件人+主题）
- ❌ 新开一封信、指定收件人、抄送、附件（只能回原发件人，纯文本）
- ❌ 删信/改标签/标已读（scope 就没给）
- 附件不解析；正文优先 `text/plain`，只有 HTML 时去标签取文本，截断 `BODY_CAP` 6000 字
