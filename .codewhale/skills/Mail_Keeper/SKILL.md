# Mail Keeper

> 按成员私有的邮箱：查收、读全文、**两轮确认**后回信。只读 + 只发，不能删改信件。
> 当前 provider = Gmail REST v1（契约见 `gmail_provider.py` 文件头）。

无 `cli.py`：发信闸门要 `__turn_at` / `__text`（`agent_core._apply_context` 注入），
子进程拿不到 → 工具在 bot 进程内跑。

发信闸门：`mail_draft.py` 文件头。草稿预览必达用户：`SHOW_TOOLS`（`skill_registry.py` 文件头）。

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

## 新邮件事件（默认关，成员 `mail` 块 `"watch": true` 开）

播报规则/状态文件：`mail_watch.py` 文件头。忽略规则：`mail_rules.py` 文件头。

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
