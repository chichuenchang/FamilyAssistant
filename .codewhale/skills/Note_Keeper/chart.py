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
    """校验 spec → 渲染 PNG → 返回 data 相对路径。

    spec 非法 → ValueError；matplotlib 缺失 → RuntimeError。
    """
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
