# tests/test_chart.py — Note_Keeper 可视化（chart.py）+ 投递（split_reply）测试
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


# ── Task 1: validate / slug / prune ─────────────────────────

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
    assert chart._slug("2025 成绩!!").replace("_", "")  # not empty after strip
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


# ── Task 2: render_chart ────────────────────────────────────

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


# ── Task 3: CLI chart-render ─────────────────────────────────

def _cli(data_root, *args):
    env = dict(os.environ, DATA_ROOT=str(data_root), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(SKILL / "cli.py"), *args],
        capture_output=True, text=True, encoding="utf-8", env=env)


def test_cli_chart_render(data_root):
    spec = json.dumps({"type": "bar", "title": "测试", "x_labels": ["a", "b"],
                       "series": [{"name": "s", "values": [1, 2]}]})
    r = _cli(data_root, "chart-render", "--member", "爸爸", "--spec", spec)
    assert r.returncode == 0, r.stderr
    rel = r.stdout.strip()
    assert rel.endswith(".png")
    assert (Path(data_root) / rel).exists()


def test_cli_chart_bad_json(data_root):
    r = _cli(data_root, "chart-render", "--member", "爸爸", "--spec", "{bad")
    assert r.returncode == 1


# ── Task 4: split_reply ─────────────────────────────────────

def test_split_reply():
    import agent_core as ac
    text, imgs, docs = ac.split_reply("hello\n" + ac.IMG_SENTINEL + "Family/charts/a.png")
    assert text == "hello" and imgs == ["Family/charts/a.png"] and docs == []
    # multiple
    r = "line1\nline2\n" + ac.IMG_SENTINEL + "a.png\n" + ac.IMG_SENTINEL + "b.png"
    text, imgs, docs = ac.split_reply(r)
    assert text == "line1\nline2" and imgs == ["a.png", "b.png"] and docs == []
    # no image — passthrough
    text, imgs, docs = ac.split_reply("just text")
    assert text == "just text" and imgs == [] and docs == []


# ── Task 6: backup excludes charts/ ─────────────────────────

def test_backup_excludes_charts():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                          / ".codewhale" / "skills" / "Remote_Backup"))
    import backup_sync as bs
    assert bs._excluded("Family/charts/x.png") is True
    assert bs._excluded("Jimbo/charts/2025_x.png") is True
    assert bs._excluded("Jimbo/notes/notes.db") is False
