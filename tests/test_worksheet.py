# tests/test_worksheet.py — Note_Keeper worksheet (sheet_db) 测试
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Note_Keeper"
sys.path.insert(0, str(SKILL))
import sheet_db  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "notes.db")


# ── Task 1: create / get / list ─────────────────────────────

def test_create_kv_and_get(db):
    sid = sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert isinstance(sid, int) and sid > 0
    s = sheet_db.get_sheet("爸爸", "房贷", db_path=db)
    assert s["title"] == "房贷" and s["kind"] == "kv"
    assert s["kv_data"] == {} and s["rows"] == []


def test_create_table_and_list(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    rows = sheet_db.list_sheets("爸爸", db_path=db)
    assert len(rows) == 1
    assert rows[0]["title"] == "血压" and rows[0]["kind"] == "table"
    assert rows[0]["size"] == 0


def test_duplicate_title_raises(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    with pytest.raises(ValueError):
        sheet_db.create_sheet("爸爸", "房贷", "table", db_path=db)


def test_create_bad_kind_raises(db):
    with pytest.raises(ValueError):
        sheet_db.create_sheet("爸爸", "x", "grid", db_path=db)


def test_get_missing_returns_none(db):
    assert sheet_db.get_sheet("爸爸", "无此表", db_path=db) is None


# ── Task 2: kv set / unset ──────────────────────────────────

def test_kv_set_overwrite_unset(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert sheet_db.set_field("爸爸", "房贷", "利率", "5.2%", db_path=db) is True
    assert sheet_db.set_field("爸爸", "房贷", "到期", "2027-03", db_path=db) is True
    s = sheet_db.get_sheet("爸爸", "房贷", db_path=db)
    assert s["kv_data"] == {"利率": "5.2%", "到期": "2027-03"}
    # overwrite
    assert sheet_db.set_field("爸爸", "房贷", "利率", "4.9%", db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "房贷", db_path=db)["kv_data"]["利率"] == "4.9%"
    # unset
    assert sheet_db.unset_field("爸爸", "房贷", "到期", db_path=db) is True
    assert "到期" not in sheet_db.get_sheet("爸爸", "房贷", db_path=db)["kv_data"]


def test_kv_unset_missing_field_false(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert sheet_db.unset_field("爸爸", "房贷", "无此字段", db_path=db) is False


def test_set_field_on_table_false(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    assert sheet_db.set_field("爸爸", "血压", "x", "1", db_path=db) is False


def test_set_field_missing_sheet_false(db):
    assert sheet_db.set_field("爸爸", "无表", "x", "1", db_path=db) is False


# ── Task 3: table row add / edit / delete ───────────────────

def test_table_add_edit_delete_row(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    rid = sheet_db.add_row("爸爸", "血压", {"date": "06-24", "sys": 120}, db_path=db)
    assert isinstance(rid, int) and rid > 0
    # dynamic new column on a later row
    rid2 = sheet_db.add_row("爸爸", "血压", {"date": "06-25", "sys": 118, "note": "ok"}, db_path=db)
    s = sheet_db.get_sheet("爸爸", "血压", db_path=db)
    assert len(s["rows"]) == 2
    assert s["rows"][1]["row_data"]["note"] == "ok"
    # edit overwrites
    assert sheet_db.edit_row("爸爸", "血压", rid, {"date": "06-24", "sys": 125}, db_path=db) is True
    s = sheet_db.get_sheet("爸爸", "血压", db_path=db)
    assert s["rows"][0]["row_data"] == {"date": "06-24", "sys": 125}
    # delete
    assert sheet_db.delete_row("爸爸", "血压", rid2, db_path=db) is True
    assert len(sheet_db.get_sheet("爸爸", "血压", db_path=db)["rows"]) == 1


def test_add_row_on_kv_returns_none(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", db_path=db)
    assert sheet_db.add_row("爸爸", "房贷", {"x": 1}, db_path=db) is None


def test_add_row_bad_data_raises(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    with pytest.raises(ValueError):
        sheet_db.add_row("爸爸", "血压", "notadict", db_path=db)


def test_edit_delete_missing_row_false(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    assert sheet_db.edit_row("爸爸", "血压", 999, {"x": 1}, db_path=db) is False
    assert sheet_db.delete_row("爸爸", "血压", 999, db_path=db) is False


# ── Task 4: rename / pin / delete / isolation ───────────────

def test_rename_pin_delete(db):
    sheet_db.create_sheet("爸爸", "血压", "table", db_path=db)
    assert sheet_db.rename_sheet("爸爸", "血压", "血压记录", db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "血压", db_path=db) is None
    assert sheet_db.get_sheet("爸爸", "血压记录", db_path=db) is not None
    # pin
    assert sheet_db.set_pinned("爸爸", "血压记录", True, db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "血压记录", db_path=db)["pinned"] is True
    # delete cascades rows
    sheet_db.add_row("爸爸", "血压记录", {"sys": 120}, db_path=db)
    assert sheet_db.delete_sheet("爸爸", "血压记录", db_path=db) is True
    assert sheet_db.get_sheet("爸爸", "血压记录", db_path=db) is None


def test_rename_clash_false(db):
    sheet_db.create_sheet("爸爸", "A", "kv", db_path=db)
    sheet_db.create_sheet("爸爸", "B", "kv", db_path=db)
    assert sheet_db.rename_sheet("爸爸", "A", "B", db_path=db) is False


def test_pinned_sheets_full_content(db):
    sheet_db.create_sheet("爸爸", "房贷", "kv", pinned=True, db_path=db)
    sheet_db.set_field("爸爸", "房贷", "利率", "5%", db_path=db)
    sheet_db.create_sheet("爸爸", "杂", "kv", db_path=db)  # not pinned
    pins = sheet_db.pinned_sheets("爸爸", db_path=db)
    assert len(pins) == 1 and pins[0]["title"] == "房贷"
    assert pins[0]["kv_data"] == {"利率": "5%"}


def test_member_isolation(db):
    sheet_db.create_sheet("爸爸", "私", "kv", db_path=db)
    sheet_db.set_field("爸爸", "私", "k", "v", db_path=db)
    # 妈妈 cannot see/touch 爸爸's sheet
    assert sheet_db.get_sheet("妈妈", "私", db_path=db) is None
    assert sheet_db.set_field("妈妈", "私", "k", "x", db_path=db) is False
    assert sheet_db.delete_sheet("妈妈", "私", db_path=db) is False
    assert sheet_db.list_sheets("妈妈", db_path=db) == []
    # 爸爸's data intact
    assert sheet_db.get_sheet("爸爸", "私", db_path=db)["kv_data"] == {"k": "v"}


# ── Task 5: CLI smoke ───────────────────────────────────────

def _cli(db, *args):
    env = dict(os.environ, NOTE_DB_PATH=db, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, str(SKILL / "cli.py"), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return r


def test_cli_kv_flow(db):
    assert _cli(db, "sheet-create", "--member", "爸爸", "--title", "房贷",
                "--kind", "kv").returncode == 0
    assert _cli(db, "sheet-set", "--member", "爸爸", "--title", "房贷",
                "--field", "利率", "--value", "5%").returncode == 0
    out = _cli(db, "sheet-show", "--member", "爸爸", "--title", "房贷").stdout
    assert "利率" in out and "5%" in out


def test_cli_table_flow(db):
    _cli(db, "sheet-create", "--member", "爸爸", "--title", "血压", "--kind", "table")
    r = _cli(db, "sheet-row-add", "--member", "爸爸", "--title", "血压",
             "--data", '{"date":"06-24","sys":120}')
    assert r.returncode == 0
    out = _cli(db, "sheet-show", "--member", "爸爸", "--title", "血压").stdout
    assert "120" in out
    assert "血压" in _cli(db, "sheet-list", "--member", "爸爸").stdout


def test_cli_bad_json_fails(db):
    _cli(db, "sheet-create", "--member", "爸爸", "--title", "血压", "--kind", "table")
    r = _cli(db, "sheet-row-add", "--member", "爸爸", "--title", "血压", "--data", "{bad")
    assert r.returncode == 1


# ── Task 7: agent_core context injection ────────────────────

def test_worksheets_context_render(tmp_path):
    AR = Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"
    sys.path.insert(0, str(AR))
    db = str(tmp_path / "notes.db")
    sheet_db.create_sheet("爸爸", "房贷", "kv", pinned=True, db_path=db)
    sheet_db.set_field("爸爸", "房贷", "利率", "5%", db_path=db)
    import agent_core
    out = agent_core._worksheets_context("爸爸", db_path=db)
    assert "房贷" in out and "利率" in out and "5%" in out


def test_worksheets_context_row_cap(tmp_path):
    db = str(tmp_path / "notes.db")
    sheet_db.create_sheet("爸爸", "大表", "table", pinned=True, db_path=db)
    for i in range(90):
        sheet_db.add_row("爸爸", "大表", {"i": i}, db_path=db)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"))
    import agent_core
    out = agent_core._worksheets_context("爸爸", db_path=db)
    assert "还有" in out  # truncation note present (cap default 80)
