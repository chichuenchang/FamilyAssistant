# tests/test_image_gc.py — 陈旧来图清理（Calendar_Keeper/image_gc.py）。
from datetime import datetime as dt

import pytest

import image_gc
import cal_db
import paths
import members


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DATA_ROOT=tmp，单成员 M，schedule 库有一旧一新带图行。"""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("IMAGE_GC_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CALENDAR_CONFIG", str(tmp_path / "no-config.json"))  # 用回退参数
    monkeypatch.setattr(members, "member_names", lambda *a, **k: ["M"])

    sdb = str(paths.member_store("M", "schedule"))
    imgdir = paths.member_domain_image_dir("M", "schedule")
    old_img = imgdir / "old.jpg"; old_img.write_bytes(b"x")
    new_img = imgdir / "new.jpg"; new_img.write_bytes(b"y")
    old_id = cal_db.add_item(kind="event", title="old", start_at="2020-01-01T10:00",
                             member="M", source_image=paths.to_rel(old_img), db_path=sdb)
    new_id = cal_db.add_item(kind="event", title="new", start_at="2026-05-01T10:00",
                             member="M", source_image=paths.to_rel(new_img), db_path=sdb)
    return tmp_path, sdb, old_id, new_id, old_img, new_img


def test_item_date_fallback():
    assert image_gc._item_date(
        {"start_at": "2020-01-01T10:00", "created_at": "2025-01-01T00:00:00"}) == "2020-01-01"
    assert image_gc._item_date(
        {"start_at": "", "created_at": "2025-01-01T00:00:00"}) == "2025-01-01"


def test_prune_clears_old_keeps_recent(env):
    tmp, sdb, old_id, new_id, old_img, new_img = env
    rep = image_gc.prune_stale_images(now=dt(2026, 6, 19), retention_years=2)
    assert rep["cleared"] == 1 and rep["files_deleted"] == 1
    # 旧：文件删、链接清、行仍在
    assert not old_img.exists()
    old = cal_db.get_item(old_id, db_path=sdb)
    assert old["source_image"] == "" and old["status"] == "active"
    # 新：原样
    assert new_img.exists()
    assert cal_db.get_item(new_id, db_path=sdb)["source_image"] != ""


def test_prune_dry_run_changes_nothing(env):
    tmp, sdb, old_id, new_id, old_img, new_img = env
    rep = image_gc.prune_stale_images(now=dt(2026, 6, 19), retention_years=2, dry_run=True)
    assert rep["cleared"] == 1 and rep["files_deleted"] == 0
    assert old_img.exists()
    assert cal_db.get_item(old_id, db_path=sdb)["source_image"] != ""


def test_prune_retention_window(env):
    # retention=10 年 → 2020 的也在保留期内，不清
    rep = image_gc.prune_stale_images(now=dt(2026, 6, 19), retention_years=10)
    assert rep["cleared"] == 0


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
