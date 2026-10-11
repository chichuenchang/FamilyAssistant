# tests/test_image_gc.py — 陈旧来图清理（Calendar_Keeper/image_gc.py）：按月目录年龄删。
from datetime import datetime as dt

import pytest

import image_gc
import members
import paths


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DATA_ROOT=tmp，单成员 M；inbox/schedule/tasks/notes 各有新旧月目录。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("IMAGE_GC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CALENDAR_CONFIG", str(tmp_path / "no-config.json"))  # 用回退参数
    monkeypatch.setattr(members, "member_names", lambda *a, **k: ["M"])
    root = paths.member_dir("M")
    made = {}
    for area in ("inbox", "schedule", "tasks", "notes"):
        for month in ("2025-05", "2025-06"):
            d = root / area / month
            d.mkdir(parents=True)
            (d / "a.jpg").write_bytes(b"x")
            made[(area, month)] = d
    (root / "schedule" / "schedule.db").write_bytes(b"db")   # 非月目录：绝不碰
    return root, made


def test_default_retention_is_one_year():
    assert image_gc._FALLBACK["image_retention_years"] == 1


def test_prune_deletes_months_before_cutoff(env):
    root, made = env
    rep = image_gc.prune_stale_images(now=dt(2026, 6, 19))
    for area in ("inbox", "schedule", "tasks"):
        assert not made[(area, "2025-05")].exists(), area
        assert made[(area, "2025-06")].exists(), area      # 截止月本身保留
    assert made[("notes", "2025-05")].exists()             # notes 库链接其图，不归这里管
    assert (root / "schedule" / "schedule.db").exists()
    assert rep["dirs_deleted"] == 3 and rep["files_deleted"] == 3


def test_prune_dry_run_changes_nothing(env):
    _, made = env
    rep = image_gc.prune_stale_images(now=dt(2026, 6, 19), dry_run=True)
    assert rep["dirs_deleted"] == 3 and rep["files_deleted"] == 0
    assert all(d.exists() for d in made.values())


def test_prune_retention_window(env):
    rep = image_gc.prune_stale_images(now=dt(2026, 6, 19), retention_years=10)
    assert rep["dirs_deleted"] == 0


def test_tick_throttles(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("IMAGE_GC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CALENDAR_CONFIG", str(tmp_path / "none.json"))   # 回退 interval=30
    monkeypatch.setattr(members, "member_names", lambda *a, **k: [])
    assert image_gc.image_gc_tick(now=dt(2026, 6, 19, 9, 0)) is True
    assert image_gc.image_gc_tick(now=dt(2026, 6, 25, 9, 0)) is False     # 6 天 < 30
    assert image_gc.image_gc_tick(now=dt(2026, 7, 25, 9, 0)) is True      # > 30 天


def test_tick_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_GC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CALENDAR_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.setattr(image_gc, "prune_stale_images",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert image_gc.image_gc_tick(now=dt(2026, 6, 19, 9, 0)) is False
