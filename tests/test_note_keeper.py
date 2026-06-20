# tests/test_note_keeper.py — Note Keeper skill tests.
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

import note_db

_CLI = str(
    _Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Note_Keeper" / "cli.py"
)


def _run_cli(*args, env=None):
    import os as _os
    import tempfile as _tempfile
    env = {**_os.environ, **(env or {})}
    # note-add 等写命令会 mark_dirty —— 状态文件重定向到临时目录，别碰真实 data/
    env.setdefault("BACKUP_STATE_DIR",
                   _os.path.join(_tempfile.gettempdir(), "fa_note_test_backup"))
    return subprocess.run(
        [_sys.executable, _CLI, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


class TestAddList:
    def test_add_and_list_roundtrip(self, note_db_path):
        nid = note_db.add_note("爸爸", "车位 B2-118", db_path=note_db_path)
        assert nid == 1
        rows = note_db.list_notes("爸爸", db_path=note_db_path)
        assert len(rows) == 1
        r = rows[0]
        assert r["content"] == "车位 B2-118"
        assert r["member"] == "爸爸"
        assert r["pinned"] == 0
        assert r["source_image"] == ""
        assert r["created_at"]  # nonempty ISO timestamp
        assert "T" in r["created_at"]

    def test_id_increments(self, note_db_path):
        n1 = note_db.add_note("爸爸", "第一条", db_path=note_db_path)
        n2 = note_db.add_note("爸爸", "第二条", db_path=note_db_path)
        assert n2 == n1 + 1

    def test_list_respects_limit(self, note_db_path):
        for i in range(10):
            note_db.add_note("爸爸", f"备忘 {i}", db_path=note_db_path)
        rows = note_db.list_notes("爸爸", limit=3, db_path=note_db_path)
        assert len(rows) == 3
        # newest first (highest id)
        assert rows[0]["id"] > rows[1]["id"] > rows[2]["id"]

    def test_add_with_source_image_and_pinned(self, note_db_path):
        nid = note_db.add_note(
            "爸爸", "路由器标签", source_image="photos/router.jpg", pinned=True,
            db_path=note_db_path,
        )
        rows = note_db.list_notes("爸爸", db_path=note_db_path)
        r = rows[0]
        assert r["source_image"] == "photos/router.jpg"
        assert r["pinned"] == 1

    def test_empty_member_raises_valueerror(self, note_db_path):
        with pytest.raises(ValueError, match="member"):
            note_db.add_note("", "内容", db_path=note_db_path)
        with pytest.raises(ValueError, match="member"):
            note_db.add_note("   ", "内容", db_path=note_db_path)

    def test_empty_content_raises_valueerror(self, note_db_path):
        with pytest.raises(ValueError, match="content"):
            note_db.add_note("爸爸", "", db_path=note_db_path)
        with pytest.raises(ValueError, match="content"):
            note_db.add_note("爸爸", "   ", db_path=note_db_path)


class TestSearch:
    def test_search_finds_substring(self, note_db_path):
        note_db.add_note("爸爸", "车位 B2-118", db_path=note_db_path)
        note_db.add_note("爸爸", "WiFi 密码 8888", db_path=note_db_path)
        rows = note_db.search_notes("爸爸", "车位", db_path=note_db_path)
        assert len(rows) == 1
        assert "B2-118" in rows[0]["content"]

    def test_search_misses_non_match(self, note_db_path):
        note_db.add_note("爸爸", "车位 B2-118", db_path=note_db_path)
        rows = note_db.search_notes("爸爸", "XYZ不存在", db_path=note_db_path)
        assert rows == []

    def test_empty_keyword_raises_valueerror(self, note_db_path):
        with pytest.raises(ValueError, match="keyword"):
            note_db.search_notes("爸爸", "", db_path=note_db_path)
        with pytest.raises(ValueError, match="keyword"):
            note_db.search_notes("爸爸", "   ", db_path=note_db_path)


class TestMemberIsolation:
    def test_list_isolated_by_member(self, note_db_path):
        a1 = note_db.add_note("爸爸", "A的备忘1", db_path=note_db_path)
        a2 = note_db.add_note("爸爸", "A的备忘2", db_path=note_db_path)
        b1 = note_db.add_note("妈妈", "B的备忘", db_path=note_db_path)

        a_rows = note_db.list_notes("爸爸", db_path=note_db_path)
        a_ids = {r["id"] for r in a_rows}
        assert a_ids == {a1, a2}
        assert b1 not in a_ids

        b_rows = note_db.list_notes("妈妈", db_path=note_db_path)
        b_ids = {r["id"] for r in b_rows}
        assert b_ids == {b1}
        assert a1 not in b_ids and a2 not in b_ids

    def test_search_isolated_by_member(self, note_db_path):
        note_db.add_note("爸爸", "车位 B2-118", db_path=note_db_path)
        note_db.add_note("妈妈", "妈妈的密码", db_path=note_db_path)
        rows = note_db.search_notes("爸爸", "密码", db_path=note_db_path)
        assert rows == []  # 妈妈的"密码"对爸爸不可见

    def test_delete_other_member_returns_false(self, note_db_path):
        a_id = note_db.add_note("爸爸", "A的备忘", db_path=note_db_path)
        ok = note_db.delete_note("妈妈", a_id, db_path=note_db_path)
        assert ok is False
        # A's note still exists
        rows = note_db.list_notes("爸爸", db_path=note_db_path)
        assert len(rows) == 1
        assert rows[0]["id"] == a_id

    def test_pin_other_member_returns_false(self, note_db_path):
        a_id = note_db.add_note("爸爸", "A的备忘", db_path=note_db_path)
        ok = note_db.set_pinned("妈妈", a_id, True, db_path=note_db_path)
        assert ok is False
        r = note_db.list_notes("爸爸", db_path=note_db_path)[0]
        assert r["pinned"] == 0


class TestPinnedAndRecent:
    def test_pinned_and_recent_combination(self, note_db_path):
        # Pin an old note
        old_id = note_db.add_note("爸爸", "旧置顶备忘", pinned=True, db_path=note_db_path)
        # Add 6 unpinned notes
        unpinned_ids = []
        for i in range(6):
            nid = note_db.add_note("爸爸", f"备忘 {i}", db_path=note_db_path)
            unpinned_ids.append(nid)

        result = note_db.pinned_and_recent("爸爸", recent_limit=5, db_path=note_db_path)
        # 1 pinned + 5 recent unpinned = 6 total
        assert len(result) == 6
        # Pinned note is present
        pinned_ids = {r["id"] for r in result if r["pinned"] == 1}
        assert old_id in pinned_ids
        # The 5 newest unpinned are present, the oldest unpinned is not
        unpinned_in_result = {r["id"] for r in result if r["pinned"] == 0}
        assert len(unpinned_in_result) == 5
        oldest_unpinned = min(unpinned_ids)
        assert oldest_unpinned not in unpinned_in_result

    def test_pinned_and_recent_no_pinned(self, note_db_path):
        for i in range(3):
            note_db.add_note("爸爸", f"备忘 {i}", db_path=note_db_path)
        result = note_db.pinned_and_recent("爸爸", recent_limit=5, db_path=note_db_path)
        assert len(result) == 3
        assert all(r["pinned"] == 0 for r in result)

    def test_pinned_and_recent_fields(self, note_db_path):
        note_db.add_note("爸爸", "测试", source_image="img.jpg", pinned=True, db_path=note_db_path)
        result = note_db.pinned_and_recent("爸爸", db_path=note_db_path)
        r = result[0]
        assert set(r.keys()) == {"id", "content", "source_image", "pinned", "created_at"}
        assert r["source_image"] == "img.jpg"

    def test_set_pinned_toggle(self, note_db_path):
        nid = note_db.add_note("爸爸", "备忘", db_path=note_db_path)
        assert note_db.set_pinned("爸爸", nid, True, db_path=note_db_path)
        r = note_db.list_notes("爸爸", db_path=note_db_path)[0]
        assert r["pinned"] == 1
        assert note_db.set_pinned("爸爸", nid, False, db_path=note_db_path)
        r = note_db.list_notes("爸爸", db_path=note_db_path)[0]
        assert r["pinned"] == 0


class TestCli:
    def test_note_add_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        r = _run_cli("note-add", "--member", "爸爸", "--content", "车位 B2-118")
        assert r.returncode == 0, r.stderr
        assert "已记录备忘 #1" in r.stdout

        r = _run_cli("note-list", "--member", "爸爸")
        assert r.returncode == 0
        assert "#1" in r.stdout
        assert "车位 B2-118" in r.stdout

    def test_note_add_with_pinned_and_image(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        r = _run_cli(
            "note-add", "--member", "爸爸", "--content", "路由器标签",
            "--source-image", "photos/router.jpg", "--pinned",
        )
        assert r.returncode == 0
        r = _run_cli("note-list", "--member", "爸爸")
        assert "[置顶]" in r.stdout
        assert "图片: photos/router.jpg" in r.stdout

    def test_note_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        r = _run_cli("note-list", "--member", "爸爸")
        assert r.returncode == 0
        assert "（无备忘）" in r.stdout

    def test_note_search(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        _run_cli("note-add", "--member", "爸爸", "--content", "车位 B2-118")
        _run_cli("note-add", "--member", "爸爸", "--content", "WiFi 密码")
        r = _run_cli("note-search", "--member", "爸爸", "--keyword", "车位")
        assert r.returncode == 0
        assert "B2-118" in r.stdout
        assert "WiFi" not in r.stdout

    def test_note_search_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        _run_cli("note-add", "--member", "爸爸", "--content", "车位")
        r = _run_cli("note-search", "--member", "爸爸", "--keyword", "不存在")
        assert r.returncode == 0
        assert "（无匹配）" in r.stdout

    def test_note_delete_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        _run_cli("note-add", "--member", "爸爸", "--content", "备忘")
        r = _run_cli("note-delete", "--member", "爸爸", "--id", "1")
        assert r.returncode == 0
        assert "已删除备忘 #1" in r.stdout
        # Verify gone
        r = _run_cli("note-list", "--member", "爸爸")
        assert "（无备忘）" in r.stdout

    def test_note_delete_wrong_id_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        # No notes at all
        r = _run_cli("note-delete", "--member", "爸爸", "--id", "999")
        assert r.returncode == 1
        assert "[错误] 无此备忘" in r.stderr

    def test_note_delete_other_member_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        _run_cli("note-add", "--member", "爸爸", "--content", "爸爸的备忘")
        r = _run_cli("note-delete", "--member", "妈妈", "--id", "1")
        assert r.returncode == 1
        assert "[错误] 无此备忘" in r.stderr
        # 爸爸 still sees it
        r = _run_cli("note-list", "--member", "爸爸")
        assert "爸爸的备忘" in r.stdout

    def test_note_pin_and_unpin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        _run_cli("note-add", "--member", "爸爸", "--content", "备忘")
        r = _run_cli("note-pin", "--member", "爸爸", "--id", "1")
        assert r.returncode == 0
        assert "已置顶备忘 #1" in r.stdout
        r = _run_cli("note-list", "--member", "爸爸")
        assert "[置顶]" in r.stdout
        r = _run_cli("note-pin", "--member", "爸爸", "--id", "1", "--unpin")
        assert r.returncode == 0
        assert "已取消置顶 #1" in r.stdout
        r = _run_cli("note-list", "--member", "爸爸")
        assert "[置顶]" not in r.stdout

    def test_note_pin_wrong_id_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        r = _run_cli("note-pin", "--member", "爸爸", "--id", "999")
        assert r.returncode == 1
        assert "[错误] 无此备忘" in r.stderr

    def test_missing_member_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        r = _run_cli("note-add", "--content", "无成员")
        assert r.returncode == 2

    def test_member_not_leaked_across_cli(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTE_DB_PATH", str(tmp_path / "cli.db"))
        _run_cli("note-add", "--member", "爸爸", "--content", "爸爸的备忘")
        _run_cli("note-add", "--member", "妈妈", "--content", "妈妈的备忘")
        r = _run_cli("note-list", "--member", "爸爸")
        assert "爸爸的备忘" in r.stdout
        assert "妈妈的备忘" not in r.stdout
        r = _run_cli("note-list", "--member", "妈妈")
        assert "妈妈的备忘" in r.stdout
        assert "爸爸的备忘" not in r.stdout


class TestPerMemberStore:
    """无 NOTE_DB_PATH 覆盖时，备忘按成员落到 data/<成员目录>/notes/notes.db。

    依赖真实 data/members.json（Alex Lee→dir Alex, Sam Lee→dir Sam）。
    """

    def test_note_add_goes_to_member_store(self, tmp_path):
        env = {"DATA_ROOT": str(tmp_path / "data")}
        r = _run_cli("note-add", "--member", "Alex Lee",
                     "--content", "wifi pw abcd", env=env)
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "data" / "Alex" / "notes" / "notes.db").exists()
        r = _run_cli("note-list", "--member", "Alex Lee", env=env)
        assert "wifi pw abcd" in r.stdout

    def test_members_have_separate_stores(self, tmp_path):
        env = {"DATA_ROOT": str(tmp_path / "data")}
        _run_cli("note-add", "--member", "Alex Lee",
                 "--content", "alex secret", env=env)
        r = _run_cli("note-list", "--member", "Sam Lee", env=env)
        assert "alex secret" not in r.stdout
        assert not (tmp_path / "data" / "Alex" / "notes" / "notes.db").samefile(
            tmp_path / "data" / "Sam" / "notes" / "notes.db") \
            if (tmp_path / "data" / "Sam" / "notes" / "notes.db").exists() else True


class TestAgentRegistration:
    """Agent 端注册检查：5 个备忘工具在 schema/map/成员隔离集中都已挂上。"""

    def test_note_tools_registered(self):
        import agent_core
        names = {"save_note", "list_notes", "search_notes", "delete_note", "pin_note"}
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert names <= schema_names
        assert names <= set(agent_core._TOOL_MAP)
        assert names == agent_core._NOTE_TOOLS

    def test_note_commands_allowed_and_routed(self):
        import agent_core
        cmds = {"note-add", "note-list", "note-search", "note-delete", "note-pin"}
        assert cmds <= agent_core.ALLOWED_COMMANDS
        for c in cmds:
            assert agent_core._cli_path(c).parent.name == "Note_Keeper"

    def test_apply_member_forces_member_on_note_tools(self):
        import agent_core
        out = agent_core._apply_member("search_notes", {"member": "妈妈", "keyword": "x"}, "爸爸")
        assert out["member"] == "爸爸"
        out = agent_core._apply_member("delete_note", {"member": "妈妈", "id": 1}, "爸爸")
        assert out["member"] == "爸爸"


class TestRelocateNoteImage:
    """save_note 图片搬移：成员 inbox（data_root 内）→ 该成员 notes/YYYY-MM/，返回 data 相对路径。"""

    def _setup(self, monkeypatch, tmp_path):
        import json as _json
        import agent_core
        import members
        mp = tmp_path / "members.json"
        mp.write_text(_json.dumps({"Alex Lee": {"dir": "Alex"}}), encoding="utf-8")
        monkeypatch.setattr(members, "MEMBERS_PATH", mp)
        monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
        return agent_core

    def test_moves_inbox_image(self, monkeypatch, tmp_path):
        ac = self._setup(monkeypatch, tmp_path)
        import paths
        img = paths.member_inbox_dir("Alex Lee") / "a.jpg"
        img.write_bytes(b"x")
        out = ac._relocate_note_image(str(img), "Alex Lee")
        assert not img.exists()
        assert out.startswith("Alex/notes/") and out.endswith("a.jpg")
        assert paths.resolve_rel(out).read_bytes() == b"x"

    def test_leaves_outside_paths_alone(self, monkeypatch, tmp_path):
        ac = self._setup(monkeypatch, tmp_path)
        other = tmp_path / "elsewhere.jpg"; other.write_bytes(b"x")   # 不在 data_root 下
        assert ac._relocate_note_image(str(other), "Alex Lee") == str(other)
        assert other.exists()

    def test_missing_file_returns_original(self, monkeypatch, tmp_path):
        ac = self._setup(monkeypatch, tmp_path)
        import paths
        ghost = str(paths.member_inbox_dir("Alex Lee") / "nope.jpg")
        assert ac._relocate_note_image(ghost, "Alex Lee") == ghost

    def test_no_member_returns_original(self, monkeypatch, tmp_path):
        ac = self._setup(monkeypatch, tmp_path)
        import paths
        img = paths.member_inbox_dir("Alex Lee") / "a.jpg"; img.write_bytes(b"x")
        assert ac._relocate_note_image(str(img), "") == str(img)
        assert img.exists()

    def test_name_collision_gets_suffix(self, monkeypatch, tmp_path):
        ac = self._setup(monkeypatch, tmp_path)
        import paths
        taken = paths.member_notes_image_dir("Alex Lee")
        (taken / "a.jpg").write_bytes(b"old")
        img = paths.member_inbox_dir("Alex Lee") / "a.jpg"; img.write_bytes(b"new")
        out = ac._relocate_note_image(str(img), "Alex Lee")
        assert _Path(out).name == "a_1.jpg"
        assert paths.resolve_rel(out).read_bytes() == b"new"
