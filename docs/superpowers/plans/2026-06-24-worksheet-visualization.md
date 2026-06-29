# Worksheet Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the assistant render a chart (line/bar/pie) from worksheet numbers and send the PNG back to the user over WeChat / Telegram / CLI test mode.

**Architecture:** A new `chart.py` in Note_Keeper renders an LLM-supplied chart spec to a PNG under `data/<member>/charts/` (matplotlib, Agg, offline). A `chart-render` CLI exposes it. `agent_core` adds a `visualize_data` tool, collects produced PNG paths during the tool loop, and appends a `\x01IMG:` sentinel to the reply; a `split_reply` helper lets each transport send the image then the text.

**Tech Stack:** Python 3.12+, matplotlib (new dep, Agg backend), pytest, urllib (Telegram multipart).

## Global Constraints

- matplotlib imported **lazily inside `chart.py`**; ImportError → `RuntimeError("matplotlib 未安装")`; CLI maps that to exit 1 + `[错误] matplotlib 未安装（pip install matplotlib）`.
- Use Agg backend: `import matplotlib; matplotlib.use("Agg")` before `pyplot`.
- CJK fonts: set `matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei","SimHei","Arial Unicode MS","DejaVu Sans"]` and `rcParams["axes.unicode_minus"] = False`.
- Charts dir: `paths.member_dir(member) / "charts"` (NOT a `_DOMAINS` value — do not use `member_store_dir`). Return data-relative path via `paths.to_rel`.
- Retention: `notes.chart_retention_days` (config, default 7). Prune-on-render.
- Delivery sentinel: `IMG_SENTINEL = "\x01IMG:"`, one line per image, appended by code (never by LLM).
- Member isolation: `visualize_data` is member-forced (in `_SHEET_TOOLS`).
- Windows console UTF-8 reconfigure block already present in `cli.py`.
- Test override env var for data root: `DATA_ROOT` (honored by `paths.data_root`).

---

## File Structure

- Create: `.codewhale/skills/Note_Keeper/chart.py` — validate + render + slug + prune.
- Modify: `.codewhale/skills/Note_Keeper/cli.py` — `chart-render` subcommand.
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py` — command set/routing, tool handler+schema, `_SHEET_TOOLS`, `split_reply`, `produced_images` collection + sentinel, prompt guidance.
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py` — send image then text in text/image handlers + test mode.
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py` — `send_photo` + send image then text.
- Modify: `.codewhale/skills/Remote_Backup/backup_sync.py` — exclude `charts/` dir.
- Modify: `.codewhale/skills/Note_Keeper/SKILL.md` + `README.md` — document feature + matplotlib dep.
- Create: `tests/test_chart.py` — chart + split_reply + CLI + backup-exclude tests.

---

### Task 1: chart.py — validation, slug, prune

**Files:**
- Create: `.codewhale/skills/Note_Keeper/chart.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Produces:
  - `_validate_spec(spec: dict) -> None` — `ValueError` on bad spec (rules below).
  - `_slug(title: str) -> str` — filesystem-safe slug (alnum + CJK kept, else `_`, cap 40, non-empty fallback `chart`).
  - `_prune_old(charts_dir: Path, retention_days: int) -> int` — delete `*.png` older than cutoff (by mtime), return count.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chart.py — Note_Keeper 可视化（chart.py）测试
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Note_Keeper"
AR = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(AR))
import chart  # noqa: E402


def _spec(**kw):
    base = {"type": "line", "title": "2025 成绩", "x_labels": ["1月", "2月"],
            "series": [{"name": "数学", "values": [88, 92]}]}
    base.update(kw)
    return base


def test_validate_ok():
    chart._validate_spec(_spec())  # no raise


@pytest.mark.parametrize("bad", [
    {"type": "scatter"},
    {"title": ""},
    {"x_labels": []},
    {"series": []},
    {"series": [{"name": "x", "values": [1]}]},          # len != x_labels(2)
    {"series": [{"name": "", "values": [1, 2]}]},         # empty name
    {"type": "pie", "series": [{"name": "a", "values": [1, 2]},
                               {"name": "b", "values": [1, 2]}]},  # pie >1 series
])
def test_validate_bad_raises(bad):
    with pytest.raises(ValueError):
        chart._validate_spec(_spec(**bad))


def test_slug_sanitizes():
    assert chart._slug("2025 成绩!!") .replace("_", "")  # not empty after strip
    assert "/" not in chart._slug("a/b\\c")
    assert chart._slug("") == "chart"
    assert len(chart._slug("x" * 100)) <= 40


def test_prune_old(tmp_path):
    fresh = tmp_path / "fresh.png"; fresh.write_bytes(b"x")
    old = tmp_path / "old.png"; old.write_bytes(b"x")
    old_mtime = time.time() - 8 * 86400
    os.utime(old, (old_mtime, old_mtime))
    n = chart._prune_old(tmp_path, retention_days=7)
    assert n == 1 and not old.exists() and fresh.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chart.py -k "validate or slug or prune" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chart'`.

- [ ] **Step 3: Write minimal implementation**

```python
# .codewhale/skills/Note_Keeper/chart.py
"""
Family Assistant — Note Keeper 可视化（worksheet → 图表 PNG）。

LLM 从工作表取出数字，按 spec 调本模块渲染 line/bar/pie 图。
matplotlib 懒加载（Agg，离线渲染，数据不出本机）。图片存成员私有
data/<成员>/charts/，渲染前按 retention 天数清理旧图，不入备份。
"""

import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime"))
import paths as _paths

_TYPES = ("line", "bar", "pie")


def _validate_spec(spec: dict) -> None:
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是对象")
    if spec.get("type") not in _TYPES:
        raise ValueError(f"type 必须是 {_TYPES}")
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title 不能为空")
    x_labels = spec.get("x_labels")
    if not isinstance(x_labels, list) or not x_labels:
        raise ValueError("x_labels 不能为空")
    series = spec.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError("series 不能为空")
    if spec["type"] == "pie" and len(series) != 1:
        raise ValueError("pie 图只能有一个 series")
    for s in series:
        if not isinstance(s, dict):
            raise ValueError("series 项必须是对象")
        if not str(s.get("name", "")).strip():
            raise ValueError("series.name 不能为空")
        vals = s.get("values")
        if not isinstance(vals, list) or len(vals) != len(x_labels):
            raise ValueError("series.values 长度必须等于 x_labels")


def _slug(title: str) -> str:
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", (title or "").strip()).strip("_")
    s = s[:40]
    return s or "chart"


def _prune_old(charts_dir: Path, retention_days: int) -> int:
    if not charts_dir.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    n = 0
    for f in charts_dir.glob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chart.py -k "validate or slug or prune" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/chart.py tests/test_chart.py
git commit -m "feat(notes): chart spec validation, slug, prune"
```

---

### Task 2: chart.py — render_chart (PNG)

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/chart.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `_validate_spec`, `_slug`, `_prune_old`.
- Produces: `render_chart(spec: dict, member: str, retention_days: int = 7) -> str` — returns data-relative PNG path; `ValueError` on bad spec; `RuntimeError("matplotlib 未安装")` if matplotlib import fails.

- [ ] **Step 1: Write the failing test**

```python
@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("ctype", ["line", "bar", "pie"])
def test_render_produces_png(data_root, ctype):
    spec = {"type": ctype, "title": "2025 成绩", "x_labels": ["1月", "2月", "3月"],
            "series": [{"name": "数学", "values": [88, 92, 95]}]}
    rel = chart.render_chart(spec, member="爸爸")
    assert rel.endswith(".png") and "charts" in rel
    abs_p = Path(os.environ["DATA_ROOT"]) / rel
    assert abs_p.exists() and abs_p.stat().st_size > 0
    assert abs_p.read_bytes()[:4] == b"\x89PNG"


def test_render_bad_spec_raises(data_root):
    with pytest.raises(ValueError):
        chart.render_chart({"type": "scatter", "title": "x",
                            "x_labels": ["a"], "series": [{"name": "s", "values": [1]}]},
                           member="爸爸")


def test_render_missing_matplotlib(data_root, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "matplotlib" or name.startswith("matplotlib."):
            raise ImportError("no mpl")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError):
        chart.render_chart({"type": "line", "title": "t", "x_labels": ["a"],
                            "series": [{"name": "s", "values": [1]}]}, member="爸爸")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chart.py -k render -v`
Expected: FAIL — `AttributeError: module 'chart' has no attribute 'render_chart'`.

- [ ] **Step 3: Write minimal implementation**

Append to `chart.py`:

```python
def _load_mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        matplotlib.rcParams["font.sans-serif"] = [
            "Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
        import matplotlib.pyplot as plt
        return plt
    except ImportError as e:
        raise RuntimeError("matplotlib 未安装") from e


def render_chart(spec: dict, member: str, retention_days: int = 7) -> str:
    _validate_spec(spec)
    plt = _load_mpl()
    charts_dir = _paths.member_dir(member) / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    _prune_old(charts_dir, retention_days)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = charts_dir / f"{ts}_{_slug(spec['title'])}.png"

    x = spec["x_labels"]
    series = spec["series"]
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    try:
        if spec["type"] == "pie":
            ax.pie(series[0]["values"], labels=x, autopct="%1.1f%%")
            ax.axis("equal")
        elif spec["type"] == "bar":
            import numpy as np
            idx = np.arange(len(x))
            width = 0.8 / len(series)
            for i, s in enumerate(series):
                ax.bar(idx + i * width, s["values"], width, label=s["name"])
            ax.set_xticks(idx + width * (len(series) - 1) / 2)
            ax.set_xticklabels(x)
        else:  # line
            for s in series:
                ax.plot(x, s["values"], marker="o", label=s["name"])
        if spec["type"] != "pie":
            if spec.get("x_label"):
                ax.set_xlabel(spec["x_label"])
            if spec.get("y_label"):
                ax.set_ylabel(spec["y_label"])
            if len(series) > 1:
                ax.legend()
        ax.set_title(spec["title"])
        fig.tight_layout()
        fig.savefig(out)
    finally:
        plt.close(fig)
    return _paths.to_rel(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chart.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/chart.py tests/test_chart.py
git commit -m "feat(notes): render_chart line/bar/pie to PNG"
```

---

### Task 3: CLI `chart-render`

**Files:**
- Modify: `.codewhale/skills/Note_Keeper/cli.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `chart.render_chart`.
- Produces: `chart-render --member --spec '<json>'` → prints data-relative PNG path; bad JSON / bad spec / missing matplotlib → exit 1 + stderr.

- [ ] **Step 1: Write the failing test**

```python
def _cli(data_root, *args):
    env = dict(os.environ, DATA_ROOT=str(data_root), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(SKILL / "cli.py"), *args],
        capture_output=True, text=True, encoding="utf-8", env=env)


def test_cli_chart_render(data_root):
    spec = json.dumps({"type": "bar", "title": "测试", "x_labels": ["a", "b"],
                       "series": [{"name": "s", "values": [1, 2]}]})
    r = _cli(data_root, "chart-render", "--member", "爸爸", "--spec", spec)
    assert r.returncode == 0
    rel = r.stdout.strip()
    assert rel.endswith(".png")
    assert (Path(data_root) / rel).exists()


def test_cli_chart_bad_json(data_root):
    r = _cli(data_root, "chart-render", "--member", "爸爸", "--spec", "{bad")
    assert r.returncode == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chart.py -k cli -v`
Expected: FAIL — `chart-render` invalid choice (argparse exit 2).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, add `import chart` near `import sheet_db`. Add a config reader + command function before `def main()`:

```python
def _chart_retention_days() -> int:
    try:
        import json as _json
        cfg = _json.loads((Path(__file__).resolve().parents[3] / "config.json")
                          .read_text(encoding="utf-8"))
        return int((cfg.get("notes") or {}).get("chart_retention_days") or 7)
    except Exception:
        return 7


def cmd_chart_render(args):
    spec = json.loads(args.spec)   # JSONDecodeError -> ValueError -> main() returns 1
    try:
        rel = chart.render_chart(spec, member=args.member,
                                 retention_days=_chart_retention_days())
    except RuntimeError as e:      # matplotlib 未安装
        print(f"[错误] {e}（pip install matplotlib）", file=sys.stderr)
        sys.exit(1)
    _mark_backup_dirty()           # harmless; charts excluded from backup anyway
    print(rel)
```

Register subparser inside `main()` after `sheet-delete`:

```python
    p = sub.add_parser("chart-render", help="渲染工作表数据为图表 PNG")
    p.add_argument("--member", required=True)
    p.add_argument("--spec", required=True, help="JSON 图表规格")
```

Add to `dispatch`:

```python
        "chart-render": cmd_chart_render,
```

(`main()` already wraps dispatch in `try/except ValueError -> return 1`; bad JSON and bad spec both surface as exit 1.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chart.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Note_Keeper/cli.py tests/test_chart.py
git commit -m "feat(notes): chart-render CLI subcommand"
```

---

### Task 4: agent_core — split_reply + sentinel collection

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Produces:
  - `IMG_SENTINEL = "\x01IMG:"` (module constant).
  - `split_reply(reply: str) -> tuple[str, list[str]]` — strips sentinel lines, returns `(text, [rel_path,...])`.
  - `handle()` appends one sentinel line per PNG produced by a successful `visualize_data` call.

- [ ] **Step 1: Write the failing test**

```python
def test_split_reply():
    import agent_core as ac
    text, imgs = ac.split_reply("hello\n" + ac.IMG_SENTINEL + "Family/charts/a.png")
    assert text == "hello" and imgs == ["Family/charts/a.png"]
    # multiple
    r = "line1\nline2\n" + ac.IMG_SENTINEL + "a.png\n" + ac.IMG_SENTINEL + "b.png"
    text, imgs = ac.split_reply(r)
    assert text == "line1\nline2" and imgs == ["a.png", "b.png"]
    # no image — passthrough
    text, imgs = ac.split_reply("just text")
    assert text == "just text" and imgs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chart.py -k split_reply -v`
Expected: FAIL — `AttributeError: module 'agent_core' has no attribute 'split_reply'`.

- [ ] **Step 3: Write minimal implementation**

Near the top-level helpers in `agent_core.py` (e.g. just after `_run_cli`), add:

```python
IMG_SENTINEL = "\x01IMG:"


def split_reply(reply: str) -> tuple[str, list[str]]:
    """从回复中剥离 \\x01IMG: 哨兵行，返回 (可见文本, [图片 data 相对路径])。"""
    imgs, keep = [], []
    for line in (reply or "").split("\n"):
        if line.startswith(IMG_SENTINEL):
            p = line[len(IMG_SENTINEL):].strip()
            if p:
                imgs.append(p)
        else:
            keep.append(line)
    return "\n".join(keep).strip(), imgs
```

In `handle()`, declare a collector before the tool loop (right after `tool_counts: dict[str, int] = {}`):

```python
        produced_images: list[str] = []
```

Inside the tool loop, after `result = fn(targs) if fn else ...` and the debug log, collect successful chart paths:

```python
                if name == "visualize_data" and result and not result.startswith("[错误]"):
                    produced_images.append(result.strip())
```

After `final = ...` and before `return final`, append sentinels:

```python
        for p in produced_images:
            final += f"\n{IMG_SENTINEL}{p}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chart.py -k split_reply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py tests/test_chart.py
git commit -m "feat(agent): split_reply + chart image sentinel collection"
```

---

### Task 5: agent_core — visualize_data tool wiring

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/agent_core.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `chart-render` CLI (Task 3), `_SHEET_TOOLS` set.
- Produces: tool `visualize_data` (handler + schema), routed + whitelisted + member-forced.

- [ ] **Step 1: Add command set + routing + whitelist**

After `_SHEET_COMMANDS = {...}` add:

```python
_CHART_COMMANDS = {"chart-render"}
```

In `_cli_path`, extend the Note_Keeper branch:

```python
    elif cmd in _NOTE_COMMANDS or cmd in _SHEET_COMMANDS or cmd in _CHART_COMMANDS:
        skill = "Note_Keeper"
```

After `ALLOWED_COMMANDS |= _SHEET_COMMANDS` add:

```python
ALLOWED_COMMANDS |= _CHART_COMMANDS
```

- [ ] **Step 2: Add tool handler + dispatch + member-forcing**

After `_tool_edit_worksheet_row` add:

```python
def _tool_visualize_data(args):
    args = dict(args)
    spec = args.pop("spec", None)
    if isinstance(spec, (dict, list)):
        args["spec"] = json.dumps(spec, ensure_ascii=False)
    elif spec is not None:
        args["spec"] = str(spec)
    return _run_cli("chart-render", args)
```

In the tool dispatch dict, after `"delete_worksheet": _tool_delete_worksheet,` add:

```python
    "visualize_data": _tool_visualize_data,
```

Add `"visualize_data"` to the `_SHEET_TOOLS` set (member-forced).

- [ ] **Step 3: Add tool schema**

After the `delete_worksheet` `_fn(...)` entry, add:

```python
    _fn("visualize_data", "把工作表里的数字画成图表（折线/柱状/饼图）并发给用户。"
        "你先从相关工作表取出对应数字（必要时先 show_worksheet），再调本工具。"
        "用户说\"画个图/可视化/看看趋势/show me the chart\"时用", {
        "spec": {
            "type": "object",
            "description": "图表规格：type(line/bar/pie), title, 可选 x_label/y_label, "
                           "x_labels(类别/X轴数组), series(数组，每项 {name, values})。"
                           "line/bar 可多 series；pie 只能一个 series，values 对应 x_labels",
        },
    }, ["spec"]),
```

- [ ] **Step 4: Add system-prompt guidance**

In `_build_system_prompt`, after the worksheet guidance line, add:

```
- 用户要"图/可视化/趋势/图表/show me the chart"→ 先确认数据在哪张工作表（必要时 show_worksheet 取全），抽出对应数字，调 visualize_data 画图；图会自动发给用户，你只需简短说明
```

- [ ] **Step 5: Verify import + registration**

Run: `python -c "import sys; sys.path.insert(0,'.codewhale/skills/Agent_Runtime'); import agent_core as a; assert 'visualize_data' in a._TOOL_MAP and 'visualize_data' in a._SHEET_TOOLS and 'chart-render' in a.ALLOWED_COMMANDS; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/agent_core.py
git commit -m "feat(agent): visualize_data tool (handler, schema, routing, member-forced)"
```

---

### Task 6: transports + backup exclude + docs

**Files:**
- Modify: `.codewhale/skills/Agent_Runtime/wechat_ilink.py`
- Modify: `.codewhale/skills/Agent_Runtime/telegram_bot.py`
- Modify: `.codewhale/skills/Remote_Backup/backup_sync.py`
- Modify: `.codewhale/skills/Note_Keeper/SKILL.md`, `README.md`
- Test: `tests/test_chart.py`

**Interfaces:**
- Consumes: `agent_core.split_reply`, `paths.resolve_rel`, `paths.data_root`.

- [ ] **Step 1: Write the failing test (backup excludes charts/)**

```python
def test_backup_excludes_charts():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                          / ".codewhale" / "skills" / "Remote_Backup"))
    import backup_sync as bs
    assert bs._excluded("Family/charts/x.png") is True
    assert bs._excluded("Jimbo/charts/2025_x.png") is True
    assert bs._excluded("Jimbo/notes/notes.db") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chart.py -k backup_excludes -v`
Expected: FAIL — `charts/x.png` currently not excluded (returns False).

- [ ] **Step 3: Exclude charts/ in backup_sync.py**

Add a dir-exclude set near `_HARD_EXCLUDE_NAMES`:

```python
# 永不进备份的目录段（图表可再生，不镜像）
_HARD_EXCLUDE_DIRS = {"charts"}
```

Extend `_excluded`:

```python
def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(seg in _HARD_EXCLUDE_DIRS for seg in parts[:-1]):
        return True
    name = parts[-1]
    if name in _HARD_EXCLUDE_NAMES:
        return True
    if "creds" in name:
        return True
    return False
```

- [ ] **Step 4: Run backup test**

Run: `python -m pytest tests/test_chart.py -k backup_excludes -v`
Expected: PASS.

- [ ] **Step 5: WeChat — send image then text**

In `wechat_ilink.py`, add near the top imports (after `from agent_core import ...`):

```python
from agent_core import split_reply as _split_reply
```

Add a helper after the imports:

```python
def _send_reply(msg, reply: str) -> None:
    """拆出图片哨兵：先发图，再发文字。图缺失/失败仅记录，不影响文字。"""
    import paths as _paths
    text, imgs = _split_reply(reply or "")
    for rel in imgs:
        try:
            ap = _paths.resolve_rel(rel).resolve()
            if ap.exists() and ap.is_relative_to(_paths.data_root().resolve()):
                msg.reply_image(str(ap))
        except Exception:
            log.exception("发送图片失败（跳过）: %s", rel)
    if text:
        msg.reply_text(text)
```

Replace the three `msg.reply_text(reply)` success calls in `handle_text`, `handle_image`, and (PDF) `handle_file` with `_send_reply(msg, reply)`. Leave the `except` branches' `reply_text(f"...出错...")` as-is.

- [ ] **Step 6: Telegram — send_photo + send image then text**

In `telegram_bot.py`, add a `send_photo` helper after `send_message`:

```python
def send_photo(chat_id: int | str, path: str, caption: str = "") -> bool:
    """sendPhoto 多部分上传（urllib，无新依赖）。"""
    import mimetypes, urllib.request, uuid
    boundary = uuid.uuid4().hex
    p = Path(path)
    try:
        file_bytes = p.read_bytes()
    except OSError:
        return False
    parts = []
    def _field(name, value):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{name}"\r\n\r\n{value}\r\n'.encode())
    _field("chat_id", str(chat_id))
    if caption:
        _field("caption", caption)
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; "
                  f'name="photo"; filename="{p.name}"\r\n'
                  f"Content-Type: {mimetypes.guess_type(p.name)[0] or 'image/png'}"
                  "\r\n\r\n").encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{BASE}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return bool(r and r.get("ok"))
    except Exception as e:
        print(f"[tg] sendPhoto 错误: {e}", file=sys.stderr)
        return False
```

Add a reply helper after `send_photo`:

```python
def _send_reply(chat_id, reply: str) -> None:
    from agent_core import split_reply
    import paths as _paths
    text, imgs = split_reply(reply or "")
    for rel in imgs:
        try:
            ap = _paths.resolve_rel(rel).resolve()
            if ap.exists() and ap.is_relative_to(_paths.data_root().resolve()):
                send_photo(chat_id, str(ap))
        except Exception as e:
            print(f"[tg] 发图失败 {rel}: {e}", file=sys.stderr)
    if text:
        send_message(chat_id, text)
```

In `run()`, replace the text-message `if reply: send_message(chat_id, reply)` with `if reply: _send_reply(chat_id, reply)`. Do the same for the image/document reply paths (lines that call `send_message(chat_id, reply)` after `agent.handle_image`).

- [ ] **Step 7: CLI test mode — show image path**

In `wechat_ilink.py` `run_test()`, replace `print(f"助手> {reply}")` with:

```python
        text, imgs = _split_reply(reply)
        for rel in imgs:
            print(f"助手> [图片] {rel}")
        if text:
            print(f"助手> {text}")
```

- [ ] **Step 8: Docs**

- `Note_Keeper/SKILL.md`: add a `### 可视化（Chart）` subsection under Worksheet — `chart-render` CLI, the spec shape, supported types, `data/<member>/charts/` storage, `notes.chart_retention_days` (default 7), backup-excluded, matplotlib required (graceful degrade), `visualize_data` agent tool, delivery via `\x01IMG:` sentinel + `split_reply`. Add `chart.py` to the code-layout tree.
- `README.md`: under Note Keeper / 远程频道, add one line that the bot can render and send charts of worksheet data; add `pip install matplotlib`（可选，可视化需要）to the install section.

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add .codewhale/skills/Agent_Runtime/wechat_ilink.py .codewhale/skills/Agent_Runtime/telegram_bot.py .codewhale/skills/Remote_Backup/backup_sync.py .codewhale/skills/Note_Keeper/SKILL.md README.md tests/test_chart.py
git commit -m "feat(transport): deliver chart images over wechat/telegram; exclude charts from backup; docs"
```

---

## Self-Review

**Spec coverage:** chart spec + validation (T1), offline render line/bar/pie + CJK + lazy mpl (T2), CLI (T3), delivery sentinel + split_reply + collection (T4), agent tool wiring + member-forced + prompt (T5), transports wechat/telegram/test-mode + backup exclude + docs (T6). matplotlib dep noted (T2 constraint, T6 docs). Storage/prune (T1/T2), retention config (T3). All spec sections mapped.

**Placeholder scan:** No TBD/TODO; every code step shows full code. T6 Step 5/6 reference existing lines by behavior ("the three reply_text success calls") — these are precise edit instructions, not placeholders.

**Type consistency:** `render_chart(spec, member, retention_days=7) -> str` (rel path) used identically in CLI (T3) and tests (T2). `IMG_SENTINEL`/`split_reply` defined T4, consumed T6. `visualize_data` handler pops `spec`, dumps to `--spec` matching CLI `--spec` (T3). `_CHART_COMMANDS`/`_SHEET_TOOLS` membership asserted T5 Step 5. `_excluded` dir rule (T6) matches `paths` charts dir (`charts` segment, T2). `paths.resolve_rel`/`to_rel`/`member_dir`/`data_root` all confirmed to exist.
