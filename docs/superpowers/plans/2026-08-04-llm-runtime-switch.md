# LLM Runtime Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each chat user switch the agent's DeepSeek model (`flash`/`pro`) and reasoning effort (`low`/`medium`/`high`/`max`) at runtime via `/model` and `/effort` commands, persisted per user in `data/.llm_overrides.json`.

**Architecture:** Overrides live in a JSON state dotfile under `data/`, loaded once into `Agent._llm_overrides` at construction; `/model` and `/effort` are intercepted in `Agent.handle` next to `/clear` (zero LLM tokens); `_call_llm` resolves model/effort per user as override → env var → default. The state file is re-read and merged only when a switch command runs, never on the message path.

**Tech Stack:** Python 3.10+, stdlib only (`json`, `os`, `pathlib`), pytest.

Spec: `docs/superpowers/specs/2026-08-04-llm-runtime-switch-design.md`

## Global Constraints

- Stdlib only in `agent_core.py` — no new dependencies.
- State file: `data/.llm_overrides.json` via `paths.data_root()` (tests get `DATA_ROOT` isolation).
- Atomic writes: temp file + `os.replace` (project pattern, see `members._save_members`).
- Allowed models: `deepseek-v4-flash`, `deepseek-v4-pro` (aliases `flash`/`pro`). Allowed efforts: `low`, `medium`, `high`, `max`.
- Defaults unchanged: model `deepseek-v4-flash`, effort `max`; env vars `DEEPSEEK_MODEL` / `DEEPSEEK_REASONING_EFFORT` keep working as boot defaults.
- Chat replies in Chinese, matching existing command replies (`✅ 对话上下文已清除。`).
- Do NOT git-commit unless the user explicitly asks; leave changes uncommitted.

---

### Task 1: Override store helpers in agent_core.py

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` (insert new section immediately before `class Agent` at line 1309)
- Test: `tests/test_agent_llm_switch.py` (new file)

**Interfaces:**
- Produces:
  - `agent_core._LLM_MODELS: tuple` = `("deepseek-v4-flash", "deepseek-v4-pro")`
  - `agent_core._LLM_MODEL_ALIASES: dict` = `{"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}`
  - `agent_core._LLM_EFFORTS: tuple` = `("low", "medium", "high", "max")`
  - `agent_core._LLM_DEFAULT_MODEL` = `"deepseek-v4-flash"`, `agent_core._LLM_DEFAULT_EFFORT` = `"max"`
  - `agent_core._load_llm_overrides() -> dict[str, dict]` — reads + validates the state file
  - `agent_core._save_llm_overrides(overrides: dict) -> None` — atomic write

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_llm_switch.py — /model /effort 运行时切换（每用户覆盖 + 持久化）。
import json

import agent_core


def test_load_llm_overrides_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert agent_core._load_llm_overrides() == {}


def test_load_llm_overrides_validates_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / ".llm_overrides.json").write_text(json.dumps({
        "u1": {"model": "deepseek-v4-pro", "effort": "high"},
        "u2": {"model": "gpt-99", "effort": "ludicrous"},   # 非法值整条丢弃
        "u3": "not-a-dict",
        "u4": {"model": "deepseek-v4-flash"},                # 单键也合法
    }), encoding="utf-8")
    assert agent_core._load_llm_overrides() == {
        "u1": {"model": "deepseek-v4-pro", "effort": "high"},
        "u4": {"model": "deepseek-v4-flash"},
    }


def test_load_llm_overrides_corrupt_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    (tmp_path / ".llm_overrides.json").write_text("{broken", encoding="utf-8")
    assert agent_core._load_llm_overrides() == {}


def test_save_llm_overrides_roundtrip_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    data = {"u1": {"model": "deepseek-v4-pro", "effort": "max"}}
    agent_core._save_llm_overrides(data)
    assert agent_core._load_llm_overrides() == data
    assert not (tmp_path / ".llm_overrides.json.tmp").exists()  # 临时文件已 replace
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_llm_switch.py -q`
Expected: FAIL — `AttributeError: module 'agent_core' has no attribute '_load_llm_overrides'`

- [ ] **Step 3: Implement the helpers**

Insert immediately before `class Agent:` (line 1309) in `agent_core.py`:

```python
# ── 每用户 LLM 运行时覆盖（/model /effort；状态文件不入备份） ──────
# 状态存 data/.llm_overrides.json：{user: {"model": ..., "effort": ...}}。
# 只在 Agent 启动与执行切换命令时读写——消息路径零文件 IO。

_LLM_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
_LLM_MODEL_ALIASES = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
_LLM_EFFORTS = ("low", "medium", "high", "max")
_LLM_DEFAULT_MODEL = "deepseek-v4-flash"
_LLM_DEFAULT_EFFORT = "max"


def _llm_overrides_path() -> Path:
    return _paths.data_root() / ".llm_overrides.json"


def _load_llm_overrides() -> dict:
    """读每用户 LLM 覆盖。文件缺失 → {}；损坏/值非法 → 跳过并告警（手工改过也不炸）。"""
    try:
        raw = json.loads(_llm_overrides_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        _log.warning("LLM 覆盖状态文件损坏，按无覆盖启动", exc_info=True)
        return {}
    out = {}
    for user, entry in (raw.items() if isinstance(raw, dict) else []):
        if not isinstance(entry, dict):
            continue
        clean = {}
        if entry.get("model") in _LLM_MODELS:
            clean["model"] = entry["model"]
        if entry.get("effort") in _LLM_EFFORTS:
            clean["effort"] = entry["effort"]
        if clean:
            out[user] = clean
    return out


def _save_llm_overrides(overrides: dict) -> None:
    """原子写（临时文件 + os.replace，同 members._save_members 套路）。"""
    p = _llm_overrides_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_llm_switch.py -q`
Expected: 4 passed

---

### Task 2: Resolution wiring — `_llm_settings`, init load, `_call_llm` user param

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` — `Agent.__init__` (lines 1321-1335), `Agent.handle` LLM call site (line 1386), `Agent._call_llm` (lines 1483-1503)
- Test: `tests/test_agent_llm_switch.py` (append)

**Interfaces:**
- Consumes: Task 1 constants and `_load_llm_overrides`.
- Produces:
  - `Agent._llm_overrides: dict[str, dict]` (instance attr, loaded in `__init__`)
  - `Agent._llm_settings(user: str) -> tuple[str, str]` — returns `(model, effort)`
  - `Agent._call_llm(self, messages, user: str = "")` — new optional param; `handle` passes its `user`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_agent_llm_switch.py`)

```python
def _agent(tmp_path, monkeypatch):
    """干净环境下的 Agent：数据根隔离，LLM 相关环境变量清空。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    for v in ("DEEPSEEK_MODEL", "DEEPSEEK_REASONING_EFFORT", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return agent_core.Agent(idle_clear_hours=0)


def test_llm_settings_defaults(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    assert a._llm_settings("u1") == ("deepseek-v4-flash", "max")


def test_llm_settings_env_beats_default(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "high")
    assert a._llm_settings("u1") == ("deepseek-v4-pro", "high")


def test_llm_settings_override_beats_env(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    a._llm_overrides["u1"] = {"model": "deepseek-v4-pro", "effort": "low"}
    assert a._llm_settings("u1") == ("deepseek-v4-pro", "low")
    assert a._llm_settings("u2") == ("deepseek-v4-flash", "max")  # 不影响其他用户


def test_handle_passes_user_to_call_llm(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    seen = {}

    def stub(messages, user=""):
        seen["user"] = user
        return {"content": "好"}

    a._call_llm = stub
    assert a.handle("你好", user="wx_1", member="Jim") == "好"
    assert seen["user"] == "wx_1"
```

Note: the stub test works because `handle` calls `self._call_llm(msgs, user=user)`; assigning a plain function to the instance attribute shadows the bound method.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_llm_switch.py -q`
Expected: FAIL — `AttributeError: 'Agent' object has no attribute '_llm_settings'` (and `_llm_overrides`)

- [ ] **Step 3: Implement the wiring**

In `Agent.__init__`, after `self._last_active: dict[str, float] = {}` (line 1335) add:

```python
        self._llm_overrides: dict[str, dict] = _load_llm_overrides()
```

Add this method to `Agent` (place directly above `_call_llm`, line 1483):

```python
    def _llm_settings(self, user: str) -> tuple[str, str]:
        """该用户生效的 (model, effort)：个人覆盖 > 环境变量 > 默认。"""
        ov = self._llm_overrides.get(user) or {}
        model = (ov.get("model") or os.environ.get("DEEPSEEK_MODEL")
                 or _LLM_DEFAULT_MODEL)
        effort = (ov.get("effort") or os.environ.get("DEEPSEEK_REASONING_EFFORT")
                  or _LLM_DEFAULT_EFFORT)
        return model, effort
```

Change `_call_llm` signature and body (lines 1483-1503):

```python
    def _call_llm(self, messages, user: str = "") -> dict | None:
        """调 DeepSeek chat completions（native function calling）。

        返回 choices[0].message 整个 dict（可能含 tool_calls）；失败返回 None。
        model/effort 按 user 解析：个人覆盖（/model /effort）> 环境变量 > 默认。
        """
        import urllib.request
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model, effort = self._llm_settings(user)
        body = json.dumps({
            "model": model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            # DeepSeek V4 是推理模型，reasoning 占用 completion 预算，
            # 预算过低（曾 1500）会被推理耗尽 → content 空、无 tool_calls。
            # 账单图片 OCR 后逐笔记账尤其费 token，预算和超时都给足。
            "reasoning_effort": effort,
            "temperature": 0.3, "max_tokens": 32000,
        }).encode("utf-8")
```

(The old comment lines about `reasoning_effort=max 默认开满推理档` are replaced by the above — the effort now comes from `_llm_settings`.)

Change the call site in `handle` (line 1386):

```python
            message = self._call_llm(msgs, user=user)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_llm_switch.py -q`
Expected: 8 passed

---

### Task 3: `/model` and `/effort` commands in `Agent.handle`

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` — `Agent.handle` (after the `/clear` block, lines 1355-1358); new methods `_handle_llm_command` and `_persist_llm_override` on `Agent`
- Test: `tests/test_agent_llm_switch.py` (append)

**Interfaces:**
- Consumes: Task 1 constants/helpers, Task 2 `_llm_settings` and `_llm_overrides`.
- Produces:
  - `Agent._handle_llm_command(text: str, user: str) -> str | None` — reply string if `text` was a `/model` or `/effort` command, else `None`
  - `Agent._persist_llm_override(user: str) -> bool` — merge-on-write to the state file; `False` on write failure

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_model_command_set_show_reset(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model pro", user="u1", member="Jim")
    assert "deepseek-v4-pro" in r and a._llm_settings("u1")[0] == "deepseek-v4-pro"
    r = a.handle("/model", user="u1", member="Jim")
    assert "deepseek-v4-pro" in r and "覆盖" in r
    r = a.handle("/model reset", user="u1", member="Jim")
    assert "✅" in r and a._llm_settings("u1") == ("deepseek-v4-flash", "max")


def test_effort_command_set_show_reset(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/effort low", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "low"
    assert "low" in a.handle("/effort", user="u1", member="Jim")
    a.handle("/effort reset", user="u1", member="Jim")
    assert a._llm_settings("u1")[1] == "max"


def test_commands_accept_full_id_and_alias(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    a.handle("/model deepseek-v4-pro", user="u1", member="Jim")
    assert a._llm_settings("u1")[0] == "deepseek-v4-pro"
    a.handle("/model flash", user="u1", member="Jim")
    assert a._llm_settings("u1")[0] == "deepseek-v4-flash"


def test_command_invalid_arg_shows_usage_no_state_change(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)
    r = a.handle("/model turbo", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[0] == "deepseek-v4-flash"
    r = a.handle("/effort xhigh", user="u1", member="Jim")
    assert "用法" in r and a._llm_settings("u1")[1] == "max"


def test_commands_need_no_api_key_and_no_llm_call(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch)  # fixture 已删 DEEPSEEK_API_KEY
    called = []
    a._call_llm = lambda *args, **kw: called.append(1)
    a.handle("/model pro", user="u1", member="Jim")
    a.handle("/effort high", user="u1", member="Jim")
    assert not called


def test_overrides_persist_across_instances(tmp_path, monkeypatch):
    a1 = _agent(tmp_path, monkeypatch)
    a1.handle("/model pro", user="u1", member="Jim")
    a1.handle("/effort low", user="u1", member="Jim")
    a2 = agent_core.Agent(idle_clear_hours=0)  # 同 DATA_ROOT 新实例 = 模拟重启
    assert a2._llm_settings("u1") == ("deepseek-v4-pro", "low")


def test_persist_merges_other_process_writes(tmp_path, monkeypatch):
    a1 = _agent(tmp_path, monkeypatch)
    a1.handle("/model pro", user="u1", member="Jim")
    # 另一进程（另一个 Agent 实例）同时给 u2 写入覆盖
    a2 = agent_core.Agent(idle_clear_hours=0)
    a2.handle("/effort high", user="u2", member="Jim")
    # a1 再切换 u1 时不得丢掉 u2 的覆盖
    a1.handle("/effort max", user="u1", member="Jim")
    disk = agent_core._load_llm_overrides()
    assert disk["u2"] == {"effort": "high"}
    assert disk["u1"] == {"model": "deepseek-v4-pro", "effort": "max"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_llm_switch.py -q`
Expected: FAIL — `/model pro` falls through to the LLM path and returns `未配置 DEEPSEEK_API_KEY。`

- [ ] **Step 3: Implement the commands**

In `Agent.handle`, immediately after the `/clear` block (lines 1355-1358), add:

```python
        # 频道无关命令：/model /effort 运行时切换 LLM（不经 LLM，零 token）
        llm_reply = self._handle_llm_command(text, user)
        if llm_reply is not None:
            return llm_reply
```

Add these two methods to `Agent` (directly above `_llm_settings`):

```python
    def _handle_llm_command(self, text: str, user: str) -> str | None:
        """/model /effort 运行时切换（不经 LLM，零 token；每用户覆盖持久化到
        data/.llm_overrides.json）。是切换命令返回回复，否则返回 None。"""
        parts = text.lower().split()
        if not parts or parts[0] not in ("/model", "/effort"):
            return None
        kind = "model" if parts[0] == "/model" else "effort"
        label = "模型" if kind == "model" else "推理档"
        valid = _LLM_MODELS if kind == "model" else _LLM_EFFORTS
        env_name = "DEEPSEEK_MODEL" if kind == "model" else "DEEPSEEK_REASONING_EFFORT"
        default = _LLM_DEFAULT_MODEL if kind == "model" else _LLM_DEFAULT_EFFORT
        arg = parts[1] if len(parts) > 1 else ""

        if not arg:  # 查询当前生效值与来源
            ov = (self._llm_overrides.get(user) or {}).get(kind)
            env = os.environ.get(env_name)
            if ov:
                return f"当前{label}：{ov}（你的个人覆盖）。"
            if env:
                return f"当前{label}：{env}（环境变量）。"
            return f"当前{label}：{default}（默认）。"
        if arg == "reset":
            entry = self._llm_overrides.get(user)
            if entry:
                entry.pop(kind, None)
                if not entry:
                    self._llm_overrides.pop(user)
            self._persist_llm_override(user)
            return f"✅ 已清除你的{label}覆盖，回到环境变量/默认。"
        value = _LLM_MODEL_ALIASES.get(arg, arg) if kind == "model" else arg
        if value not in valid:
            usage = ("/model [flash|pro|reset]" if kind == "model"
                     else "/effort [low|medium|high|max|reset]")
            return f"用法: {usage}"
        self._llm_overrides.setdefault(user, {})[kind] = value
        ok = self._persist_llm_override(user)
        note = "" if ok else "（状态文件写入失败，重启后可能失效）"
        return f"✅ 你的{label}已切换为 {value}（仅影响你）{note}。"

    def _persist_llm_override(self, user: str) -> bool:
        """写回状态文件：先重读合并（另一传输进程的切换不被覆盖），再原子写。"""
        try:
            on_disk = _load_llm_overrides()
            if user in self._llm_overrides:
                on_disk[user] = self._llm_overrides[user]
            else:
                on_disk.pop(user, None)
            _save_llm_overrides(on_disk)
            return True
        except Exception:
            _log.warning("LLM 覆盖状态写回失败", exc_info=True)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_llm_switch.py -q`
Expected: 15 passed

---

### Task 4: Exclude the state file from backup

**Files:**
- Modify: `.codewhale/skills/Remote_Backup/backup_sync.py:87-91` (`_HARD_EXCLUDE_NAMES`)
- Test: `tests/test_agent_llm_switch.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks (independent guard).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_state_file_excluded_from_backup():
    import backup_sync
    assert ".llm_overrides.json" in backup_sync._HARD_EXCLUDE_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_llm_switch.py::test_state_file_excluded_from_backup -q`
Expected: FAIL — `AssertionError`

- [ ] **Step 3: Add the exclusion**

In `backup_sync.py`, change lines 87-91 to:

```python
_HARD_EXCLUDE_NAMES = {".telegram_offset", ".doc_reminder_state",
                       ".backup_manifest.json", ".backup_state.json",
                       ".calendar_state.json", ".sync_state.json",
                       ".image_gc_state.json", ".llm_overrides.json",
                       "wechat_recent_msgs.json", "wechat_sent_msgs.json"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_llm_switch.py::test_state_file_excluded_from_backup -q`
Expected: PASS

---

### Task 5: Docs + full suite

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/SKILL.md` (env var table ~line 151; add commands note)
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py:279-281` (system prompt 对话上下文 section — one line so the agent can answer "怎么切换模型")
- Modify: `README.md` (env section line 185 area)

- [ ] **Step 1: System prompt line** — in `agent_core.py` line 280, extend the 对话上下文 section:

```python
- 用户随时可发 /clear（或"清除上下文"）清空与你的对话上下文{idle_note}
- 用户可发 /model flash|pro 与 /effort low|medium|high|max 切换自己用的模型与推理档
  （每用户生效、重启保留）；被问"怎么切换模型/推理"时如实说明
```

(The line `- 被问"你能不能清除上下文/记忆"时，如实说明上述机制，不要说做不到` stays unchanged below.)

- [ ] **Step 2: SKILL.md** — in the 环境变量 table update the two LLM rows' descriptions and add a row/paragraph documenting the runtime commands and the state file:

```markdown
| `DEEPSEEK_MODEL` | Agent LLM 模型启动默认（默认 `deepseek-v4-flash`；用户可用 `/model` 运行时覆盖） | ❌ |
| `DEEPSEEK_REASONING_EFFORT` | 推理档启动默认，默认 `max`；可设 `high` 等降档（用户可用 `/effort` 运行时覆盖） | ❌ |
```

Plus a short section after the table:

```markdown
### 运行时切换（/model /effort）

用户随时可发 `/model flash|pro|reset`、`/effort low|medium|high|max|reset`（不带参数查当前值，
含来源：个人覆盖/环境变量/默认）。每用户覆盖存 `data/.llm_overrides.json`（不入备份），
Agent 启动时读入、切换时合并写回；消息路径不读文件。优先级：个人覆盖 > 环境变量 > 默认。
```

- [ ] **Step 3: README.md** — line 185, extend the parenthetical:

```
setx DEEPSEEK_API_KEY "sk-xxx"        # 必须（可选调优：DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_REASONING_EFFORT；聊天里 /model /effort 可运行时切换）
```

- [ ] **Step 4: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: all tests pass (existing ~38 files untouched + 16 new)

---

## Self-Review Notes

- Spec coverage: §1 state file → Tasks 1+4; §2 commands → Task 3; §3 resolution → Task 2; §4 error handling → Tasks 1 (corrupt load, invalid values), 3 (usage reply, write-failure note); §5 testing → each task's TDD steps; §6 docs → Task 5.
- Type consistency: `_llm_settings(user) -> (model, effort)` used identically in Tasks 2/3; `_handle_llm_command` returns `str | None` and `handle` checks `is not None` (empty-string replies can't occur — every branch returns non-empty text).
- `handle_image` routes through `self.handle(...)` (agent_core.py:1480), so the image path picks up per-user settings with no extra work.
