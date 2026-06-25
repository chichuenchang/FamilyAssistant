# Worksheet Visualization — Design

> Date: 2026-06-24
> Skill: Note_Keeper (extension) + Agent_Runtime transports
> Status: approved

## Problem

Users keep numeric data in worksheets (test scores, blood pressure, weight). They
want to *see* it: "show me the visualization of my 2025 test scores". Today the
assistant can only reply text. It needs to render a chart image and send it back
over WeChat / Telegram.

## Goals

- A chart-rendering tool the agent calls with an explicit spec it builds from
  worksheet data (the LLM extracts/filters the right numbers; the tool just draws).
- line / bar / pie charts.
- Render offline (privacy: family data never leaves the machine) → PNG in the
  member's private dir.
- Deliver the PNG back to the user over both transports (WeChat, Telegram) and
  show it in CLI test mode.
- Graceful degradation when matplotlib is absent.

## Non-goals

- The tool parsing/querying the sheet itself (the LLM supplies the numbers).
- Interactive/animated charts, multi-chart dashboards, styling themes.
- Persisting charts as user data (they are regenerable, ephemeral).
- Charting arbitrary non-worksheet data flows (the spec is generic, but the
  driving use case is worksheets).

## Placement & dependencies

- New `.codewhale/skills/Note_Keeper/chart.py` — pure render module.
- New CLI subcommand `chart-render` in Note_Keeper `cli.py`.
- **matplotlib** added as a dependency (Agg backend, no display). Imported
  **lazily inside `chart.py`** so the rest of Note_Keeper still imports without it.
  Missing → CLI exits 1 with `[错误] 未安装 matplotlib（pip install matplotlib）`;
  the agent tool relays that message (same pattern as `yt-dlp` in Web_Reach).
- CJK font fallback configured (`Microsoft YaHei`, `SimHei`, `Arial Unicode MS`,
  `DejaVu Sans`; `axes.unicode_minus=False`) so Chinese titles/labels don't tofu.

## Chart spec (LLM → tool)

The LLM reads the sheet (pinned context or `show_worksheet`), extracts the right
numbers, and passes a full spec object:

```json
{
  "type": "line",                       // line | bar | pie
  "title": "2025 测试成绩",
  "x_label": "月份",                     // optional (ignored for pie)
  "y_label": "分数",                     // optional (ignored for pie)
  "x_labels": ["1月", "2月", "3月"],
  "series": [
    {"name": "数学", "values": [88, 92, 95]},
    {"name": "英语", "values": [80, 85, 90]}
  ]
}
```

- **line / bar**: one or more series; legend shown when >1 series.
- **pie**: exactly one series; its `values` map to `x_labels` (slice labels).
- **Validation** (`ValueError` on failure):
  - `type` ∈ {line, bar, pie}.
  - `title` non-empty string.
  - `x_labels` non-empty list.
  - `series` non-empty list; each item has non-empty `name` and a `values` list
    whose length equals `len(x_labels)`.
  - pie: exactly one series.
- Output: a PNG saved to `data/<member>/charts/<ts>_<slug>.png`; the function
  returns the **data-relative** path (`paths.to_rel`). `<slug>` = sanitized title
  (alnum/CJK kept, others → `_`, capped length); `<ts>` = `YYYYMMDD_HHMMSS`.

## chart.py API

```python
render_chart(spec: dict, member: str, db_path=None) -> str
    # validate, prune old charts, render PNG, return data-relative path.
    # raises ValueError on bad spec; raises RuntimeError("matplotlib 未安装") if import fails.

_validate_spec(spec: dict) -> None        # ValueError on any rule above
_slug(title: str) -> str                  # filesystem-safe slug
_prune_old(charts_dir: Path, retention_days: int) -> int   # delete stale PNGs, return count
```

Chart files live under `paths.member_dir(member) / "charts"` (NOT a registered
`_DOMAINS` value — `member_store_dir` would reject "charts"; use `member_dir`
directly and `mkdir(parents=True, exist_ok=True)`). The returned path is made
data-relative via `paths.to_rel`; transports resolve back with `paths.resolve_rel`.

## Storage / GC

- Charts → `data/<member>/charts/`. **Excluded from backup** (regenerable): the
  backup `_excluded(rel)` check (currently basename-only) is extended with a
  hard-excluded **directory** set `{"charts"}` — any rel path containing a
  `charts/` path component is skipped, even if a member scope covers the whole
  member dir.
- **Prune-on-render**: `render_chart` first deletes PNGs in the member's charts
  dir older than `notes.chart_retention_days` (config, default 7). No separate
  GC tick. Bounded disk, no new wiring.

## Delivery (PNG → user)

`Agent.handle()` still returns a `str`. The image path travels back via a
**code-appended sentinel**, never via LLM text:

1. In the tool loop, when a `visualize_data` call returns a non-error result (a
   path, not `[错误]…`), collect it into a per-call `produced_images` list.
2. After building `final`, append one line per image:
   `final += "\n\x01IMG:" + rel_path`. `\x01` (SOH control char) never occurs in
   real replies.
3. New module helper:

```python
IMG_SENTINEL = "\x01IMG:"
def split_reply(reply: str) -> tuple[str, list[str]]:
    # returns (visible_text_without_sentinels, [rel_path, ...])
```

Transports call `split_reply`, resolve each rel path to absolute via
`paths.from_rel` / `data_root`, send images first, then the text:

- **WeChat** (`wechat_ilink.py`): `msg.reply_image(abs_path)` per image, then
  `msg.reply_text(text)` if text non-empty. Applies in `handle_text` and
  `handle_image` handlers.
- **Telegram** (`telegram_bot.py`): new `send_photo(chat_id, abs_path, caption="")`
  using `sendPhoto` multipart upload (urllib, no new dep). Send photos, then
  `send_message(text)`.
- **CLI test mode** (`run_test`): print `[图片] <abs_path>` per image, then the text.

If an image file is missing/unsendable, log and continue with the text (delivery
of the chart must never crash message handling).

## Agent wiring (`agent_core.py`)

- `_CHART_COMMANDS = {"chart-render"}`; `_cli_path` routes it to Note_Keeper;
  `ALLOWED_COMMANDS |= _CHART_COMMANDS`.
- Tool handler `_tool_visualize_data(args)`: pop `spec` (object), `json.dumps`
  into `--spec`, run `chart-render`. Member-forced (added to `_SHEET_TOOLS` so the
  resolved member is injected → charts land in that member's dir).
- Tool schema `visualize_data(spec)` with `type/title/x_labels/series/...` shape
  described to the model.
- `produced_images` collection + sentinel append in `handle()` (above).
- `split_reply` helper (above).
- System-prompt guidance: when the user asks for a chart/visualization/trend
  picture, pull the relevant numbers from the worksheet and call `visualize_data`;
  if the data isn't available, fetch via `show_worksheet` first.

## Privacy & safety

- Rendering is fully offline; data never leaves the machine.
- Charts are member-private (`data/<member>/charts/`), member injected by code.
- Sentinel paths are data-relative; transports resolve only within `data_root`.
  A resolved path that escapes `data_root` or doesn't exist is skipped (no
  arbitrary-file send).

## Testing (`tests/test_chart.py`)

- `render_chart`: line / bar / pie each produce a PNG that exists, is non-empty,
  and starts with the PNG magic (`\x89PNG`). Use a temp `DATA_ROOT`.
- `_validate_spec`: bad type, empty title, empty x_labels, empty series,
  values/x_labels length mismatch, pie with >1 series → each raises `ValueError`.
- `_slug`: CJK kept, unsafe chars → `_`, length cap.
- `_prune_old`: deletes a back-dated file, keeps a fresh one, returns count.
- `split_reply` (in `tests/test_chart.py` or `test_worksheet.py`): text+1 image,
  text+N images, no image (passthrough unchanged), sentinel lines stripped from text.
- CLI smoke: `chart-render --member X --spec '<json>'` prints a path under
  `charts/`; resulting file exists.
- agent: `visualize_data` in tool map + schemas; in `_SHEET_TOOLS` (member-forced).

matplotlib is required for the render/CLI/agent tests; they assume it is installed
(it is, in this environment). The graceful-degradation branch is covered by a unit
test that monkeypatches the import to fail and asserts the `RuntimeError`/CLI error.

## Out of scope / future

- Scatter / stacked / dual-axis charts.
- Caption auto-generation, chart theming.
- Re-send/delivery-retry of a previously generated chart.
