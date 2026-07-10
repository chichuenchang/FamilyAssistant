# tests/test_path_discipline.py — 数据路径纪律：分库必传 db_path、
# 上下文注入按成员库解析、data_root 解析各处同源（2026-07-09 一致性审计修复）。
import json

import pytest

import agent_core
import cal_db
import members
import note_db
import sheet_db
import backup_sync


class TestDbPathRequired:
    """旧单库兜底（data/ledger.db）已移除：未传 db_path 必须显式报错，不静默建错库。"""

    def test_note_db_connect_requires_db_path(self):
        with pytest.raises(ValueError, match="db_path"):
            note_db._connect()

    def test_cal_db_connect_requires_db_path(self):
        with pytest.raises(ValueError, match="db_path"):
            cal_db._connect()

    def test_sheet_db_connect_requires_db_path(self):
        with pytest.raises(ValueError, match="db_path"):
            sheet_db._connect()


class TestContextInjectionUsesMemberStore:
    """agent 上下文注入按成员库解析（曾静默落旧单库 → 置顶备忘/工作表注入永远为空）。"""

    def _setup(self, monkeypatch, tmp_path):
        mp = tmp_path / "members.json"
        mp.write_text(json.dumps({"Alex Lee": {"dir": "Alex"}}), encoding="utf-8")
        monkeypatch.setattr(members, "MEMBERS_PATH", mp)
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
        return tmp_path / "data" / "Alex" / "notes" / "notes.db"

    def test_notes_context_reads_member_store(self, monkeypatch, tmp_path):
        store = self._setup(monkeypatch, tmp_path)
        note_db.add_note("Alex Lee", "wifi 密码 abcd", pinned=True,
                         db_path=str(store))
        out = agent_core._notes_context("Alex Lee")
        assert "wifi 密码 abcd" in out

    def test_worksheets_context_reads_member_store(self, monkeypatch, tmp_path):
        store = self._setup(monkeypatch, tmp_path)
        sheet_db.create_sheet("Alex Lee", "护照信息", "kv", db_path=str(store))
        sheet_db.set_field("Alex Lee", "护照信息", "号码", "E12345678",
                           db_path=str(store))
        sheet_db.set_pinned("Alex Lee", "护照信息", True, db_path=str(store))
        out = agent_core._worksheets_context("Alex Lee")
        assert "E12345678" in out


class TestDataRootResolution:
    """data_root 解析同源：members.json 与备份 rel 前缀跟随 DATA_ROOT/config。"""

    def test_default_members_path_honors_data_root_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "d2"))
        assert members._default_members_path() == tmp_path / "d2" / "members.json"

    def test_default_members_path_fallback_config(self, monkeypatch):
        monkeypatch.delenv("DATA_ROOT", raising=False)
        p = members._default_members_path()
        assert p.name == "members.json"
        assert p.is_absolute()

    def test_backup_data_dirname_honors_data_root_under_root(self, monkeypatch):
        monkeypatch.setenv("DATA_ROOT", str(backup_sync.ROOT / "data2"))
        assert backup_sync._data_dirname() == "data2"

    def test_backup_data_dirname_ignores_data_root_outside_root(self, monkeypatch,
                                                                tmp_path):
        # ROOT 外（测试 tmp）→ 退回 config 解析，不产出越界相对段
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "elsewhere"))
        monkeypatch.delenv("BACKUP_CONFIG", raising=False)
        assert backup_sync._data_dirname() == "data"
