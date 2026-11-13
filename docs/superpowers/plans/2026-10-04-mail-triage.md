# Mail Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, no subagents). Steps use `- [ ]`.

**Goal:** 新邮件播报只推需处理的信 + always/mute 规则。
**Architecture:** `check_and_push` 仍同步，加分拣；拍改为每频道一条守护线程跑它。分拣纯逻辑在新 `mail_triage.py`。
**Tech Stack:** Python 标准库、pytest、`llm_client.chat`。
**Spec:** `docs/superpowers/specs/2026-10-04-mail-triage-design.md`

## Global Constraints

- `TRIAGE_BODY_CAP` 1500、`WHY_CAP` 40、缓存 7 天、`LAST_PUSH_KEEP` 40
- 抬头：`📬 需处理 N 封：` / fail-open `📬 新邮件 N 封（未分拣）：`
- 测试跑 `python -m pytest tests/test_mail_keeper.py tests/test_daily_banner.py -q`；测试里 LLM 一律注入，不打网络
- 提交：一行约 5 词，无 Co-Authored-By

## Review Focus

1. LLM 回 ```json 围栏包着的数组 → 仍解析成功
2. LLM 回非列表 / 元素非 dict / `act` 非 bool → 该信当 needs-action，不抛
3. 同一封信两频道各判一次 → 第二次走缓存不调 LLM
4. worker 线程抛异常 → 占位释放，下拍能再起
5. always 规则 label 命中、信已被删（404）→ 不卡游标

---

### Task 1: 规则分 mute / always

**Files:** Modify `Mail_Keeper/mail_rules.py`；Test `tests/test_mail_keeper.py::TestMailRules`
**Produces:** `add(member, *, kind, value, note="", push="mute")`；`match(meta, rules, push="mute") -> dict | None`；`index_of(rule, rules) -> int`（1 起）；`describe` 两组、原序编号。

- [ ] 测：`match` 默认不看 always；`push="always"` 只看 always；缺 `push` 字段 = mute；同 kind+value 再 `add` 改 push 不重复；`describe` 两组含原编号
- [ ] 跑测，确认失败
- [ ] 实现
- [ ] 跑测通过（含 banner 测试）
- [ ] 提交 `Split mail rules mute always`

### Task 2: `mail_triage.py`

**Files:** Create `Mail_Keeper/mail_triage.py`；Test `tests/test_mail_triage.py`
**Consumes:** `rt.fence`、`llm_client.settings/load_overrides/chat`、`jsonfile`、`paths.state_file`
**Produces:**
- `classify(mails: list[dict], chat=None) -> dict[str, tuple[bool, str]]`（mails 项含 id/from/subject/body；LLM 失败或回复解析不了抛 `TriageError`）
- `judge(mails, chat=None, now=None) -> tuple[dict[str, tuple[bool, str]], bool]`：查缓存 → 未中走 classify → 成功写缓存；第二值 = 是否分拣成功（False = fail-open，全当需处理、无理由）
- `cache_path() -> Path`

- [ ] 测：正常判决；序号外/重复忽略；缺信 = True 无理由；`why` 去换行截 40；围栏 ```json 解析；非列表 / 坏元素；chat 返回 None 与抛错都 fail-open；缓存命中不调 chat；7 天前条目被清；正文截 1500 且进 LLM 前带围栏；LLM 见不到 Gmail id
- [ ] 跑测失败
- [ ] 实现
- [ ] 跑测通过
- [ ] 提交 `Add LLM mail triage module`

### Task 3: 批量取全文

**Files:** Modify `Mail_Keeper/gmail_provider.py`（`message_metas` 旁）；Test `TestProviderHistory`
**Produces:** `get_messages(ids, prefix) -> list[dict]`，同 `message_metas` 并发、保序、404 略过；`message_metas` 与之共用 `_fetch_many(fn, ids, prefix)`。

- [ ] 测：保序、404 略过、其他错误抛
- [ ] 跑测失败 → 实现 → 通过
- [ ] 提交 `Fetch full messages in bulk`

### Task 4: `mail_watch` 接分拣 + 线程拍

**Files:** Modify `Mail_Keeper/mail_watch.py`、`Mail_Keeper/agent_tools.py`（`_mail_watch_tick`）；Test `TestMailWatch`、`TestMailWatchFiltering`
**Consumes:** Task 1–3
**Produces:**
- `check_and_push(push_fn, channel, *, provider_for, members_path=None, now=None, chat=None) -> int`
- `tick(push_fn, channel, *, provider_for, spawn=None) -> bool`（本拍起了线程否；同频道在跑则 False）
- 日志项 `{id, from, subject, labels, pushed: bool, why}`；`record(member, items)` 替代 `record_last_push`；`last_push(member)` 不变

- [ ] 测：现有测试注入 chat（全判需处理）后照过，抬头改"需处理"；无需处理的信不推、游标前进、进日志带"无需处理："；always 胜 mute 且不调 LLM；mute（发件人）不调 LLM 且进日志带"mute 规则 #n"；LLM 失败 → "（未分拣）" 全推；推送失败游标不动、第二轮走缓存不调 LLM；`→ why` 行；`tick` 占位挡第二线程、worker 抛错后占位释放
- [ ] 跑测失败 → 实现 → 通过
- [ ] 提交 `Triage new mail before push`

### Task 5: Agent 工具与提示

**Files:** Modify `Mail_Keeper/agent_tools.py`；Test `TestMuteTools`
**Produces:** 工具 `mail_always`（参数同 `mail_mute`）；`mail_last_push` 分"播报过 / 没播报（原因）"；`mail_rules` 撤销文案通用；PROMPT_SECTIONS 加漏推流程。

- [ ] 测：`mail_always` 注册且 MEMBER_LOCKED、落 always 规则；`mail_last_push` 显示没播报的与原因；`mail_rules remove` 删 always 规则
- [ ] 跑测失败 → 实现 → 通过
- [ ] 提交 `Add always push agent tool`

### Task 6: 文档与数据

**Files:** `mail_watch.py` 文件头、`mail_rules.py` 文件头、`Mail_Keeper/SKILL.md`、`data/Jim/mail/rules.json`（不进 git）
- [ ] 文件头 + SKILL.md "新邮件事件" / 边界第 45 行
- [ ] 删 rules.json 4 条 label 规则
- [ ] 全量 `python -m pytest -q`
- [ ] 提交 `Document mail triage behaviour`
