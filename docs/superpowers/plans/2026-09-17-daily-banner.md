# Daily Banner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline; user bans subagents). Steps use `- [ ]`.

**Goal:** 08:20 每日早报推送（日程 3 天 + 待办 + 未读邮件，LLM 压缩，模板兜底）。

**Architecture:** 新 skill `Daily_Banner`，`FAST_TICKS` 钩子做廉价时间闸门，守护线程取数 + 成文 + 推送。

**Tech Stack:** Python 3 stdlib，现有 `cal_db` / `calendar_sync` / `gmail_provider` / `mail_rules` / `llm_client`。

**Spec:** `docs/superpowers/specs/2026-09-17-daily-banner-design.md`

## Global Constraints

- 模块名 `banner.py` 跨 skill 唯一（`bootstrap.check_unique_modules`）。
- 远端文本（日历/邮件）进 LLM 前必须 `rt.fence`。
- 状态走 `_paths.state_file(".daily_banner_state.json")`，调用时求值（测试靠 `DATA_ROOT`）。
- 测试跑：`python -m pytest tests/test_daily_banner.py -q`；全量 `python -m pytest -q`。

---

### Task 1: 闸门 + 状态 + 线程投递

**Files:** Create `.codewhale/skills/Daily_Banner/banner.py`, `tests/test_daily_banner.py`

**Produces:** `load_cfg() -> dict`，`in_window(now, cfg) -> bool`，`recipients(channel, members_path=None) -> dict[str, list[str]]`，
`tick(push_text, channel, *, now=None, cfg=None, members_path=None, spawn=None) -> list[str]`（返回本拍启动的成员），
`deliver(push_text, channel, member, ids, day: date, cfg) -> bool`，`build(member, day, cfg) -> str`（Task 2 实现）。

- [ ] 测试：`in_window` 08:19 假 / 08:20 真 / 11:59 真 / 12:00 假；`tick` disabled 不跑；未 opt-in 跳过；
  送达后同日不再跑；`push_text` 返回 False 不记状态且 `RETRY_S` 内不重试；`_running` 中的成员不重复启动。
  `build` 用 monkeypatch 打桩，`spawn=lambda f, *a: f(*a)` 同步执行。
- [ ] 跑，确认失败（模块不存在）。
- [ ] 实现 `banner.py` 闸门/状态/`tick`/`deliver`（代码见仓库，本计划不重复）。
- [ ] 跑，通过。提交 `feat(banner): daily banner tick gate and delivery`。

### Task 2: 取数 + 成文

**Files:** Modify `banner.py`，`tests/test_daily_banner.py`

**Produces:** `Digest`（`day, events, tasks, mails, stale`，`empty` 属性），`gather(member, day, cfg) -> Digest`，
`template(d) -> str`，`llm_input(d) -> str`，`compose(d, chat=None) -> str`，`build = compose(gather(...))`。

- [ ] 测试（临时成员库 + `cal_db.add_item`）：窗口内活动入选、窗口外不入；逾期待办标"逾期"排前、无期限在后；
  `refresh_range` 报错 → `stale`；邮件静音规则生效；`llm_input` 含围栏 nonce；
  `chat` 返回 None/抛错 → 模板；`chat` 返回文本 → 原样；全空 → 不调 LLM，推"无安排"句。
- [ ] 跑，确认失败。
- [ ] 实现。
- [ ] 跑，通过。提交 `feat(banner): gather calendar/tasks/mail and compose brief`。

### Task 3: 挂载 + 配置 + 文档

**Files:** Create `.codewhale/skills/Daily_Banner/agent_tools.py`、`SKILL.md`；Modify `config.json`、`README.md`；Test `tests/test_daily_banner.py`

- [ ] 测试：`skill_registry.load().fast_ticks` 含 `banner.tick`。
- [ ] `agent_tools.py`：`ORDER = 100`，`FAST_TICKS = [tick]`。
- [ ] `config.json` 加 `daily_banner` 块（spec「配置」）。
- [ ] `SKILL.md`：开启方法（members.json `"banner": true`）+ 指向 spec。README 功能列表加一段。
- [ ] 全量测试通过。提交 `feat(banner): register daily banner skill`。
