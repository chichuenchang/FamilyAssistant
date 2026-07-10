# tests/test_profiles.py — 家庭成员资料（profiles 表，documents.db）。
import sqlite3

import pytest

import doc_db
import doc_models


def test_db_path_is_documents_db():
    assert str(doc_models.DB_PATH).replace("\\", "/").endswith("data/Family/documents.db")


def test_schema_creates_profiles_table(tmp_path):
    p = str(tmp_path / "documents.db")
    doc_db.init_db(p)
    conn = sqlite3.connect(p)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)")]
    assert cols == ["id", "member", "field", "value", "updated_at"]
    conn.close()


@pytest.fixture
def pdb(tmp_path):
    p = str(tmp_path / "documents.db")
    doc_db.init_db(p)
    return p


class TestProfilesCRUD:
    def test_set_and_list(self, pdb):
        doc_db.set_profile("Jim Zheng", "生日", "1987-06-11", db_path=pdb)
        doc_db.set_profile("Family", "住址", "309-10530 56 Ave NW", db_path=pdb)
        rows = doc_db.list_profiles(db_path=pdb)
        assert [(r["member"], r["field"], r["value"]) for r in rows] == [
            ("Family", "住址", "309-10530 56 Ave NW"),
            ("Jim Zheng", "生日", "1987-06-11"),
        ]
        assert all(r["updated_at"] for r in rows)

    def test_upsert_overwrites(self, pdb):
        doc_db.set_profile("Jim Zheng", "电话", "111", db_path=pdb)
        doc_db.set_profile("Jim Zheng", "电话", "825-965-9090", db_path=pdb)
        rows = doc_db.list_profiles("Jim Zheng", db_path=pdb)
        assert len(rows) == 1 and rows[0]["value"] == "825-965-9090"

    def test_list_filter_by_member(self, pdb):
        doc_db.set_profile("A", "x", "1", db_path=pdb)
        doc_db.set_profile("B", "y", "2", db_path=pdb)
        assert [r["member"] for r in doc_db.list_profiles("B", db_path=pdb)] == ["B"]

    def test_unset(self, pdb):
        doc_db.set_profile("A", "x", "1", db_path=pdb)
        assert doc_db.unset_profile("A", "x", db_path=pdb) is True
        assert doc_db.unset_profile("A", "x", db_path=pdb) is False
        assert doc_db.list_profiles(db_path=pdb) == []

    def test_empty_rejected(self, pdb):
        for m, f, v in [("", "x", "1"), ("A", "", "1"), ("A", "x", "")]:
            with pytest.raises(ValueError):
                doc_db.set_profile(m, f, v, db_path=pdb)


import os
import subprocess
import sys as _sys
from pathlib import Path as _Path

_DOC_CLI = str(_Path(__file__).resolve().parent.parent
               / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")


def _run_cli(*args, db):
    env = {**os.environ, "DOC_KEEPER_DB": db}
    return subprocess.run([_sys.executable, _DOC_CLI, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


class TestProfileCLI:
    def test_set_list_unset_roundtrip(self, tmp_path):
        db = str(tmp_path / "documents.db")
        r = _run_cli("profile-set", "--member-name", "Family",
                     "--field", "住址", "--value", "309-10530 56 Ave NW", db=db)
        assert r.returncode == 0 and "✅" in r.stdout
        r = _run_cli("profile-list", db=db)
        assert "【Family】" in r.stdout and "住址: 309-10530 56 Ave NW" in r.stdout
        r = _run_cli("profile-unset", "--member-name", "Family",
                     "--field", "住址", db=db)
        assert "已删除" in r.stdout
        r = _run_cli("profile-list", db=db)
        assert "没有成员资料" in r.stdout

    def test_unset_missing_errors(self, tmp_path):
        db = str(tmp_path / "documents.db")
        r = _run_cli("profile-unset", "--member-name", "Family",
                     "--field", "无", db=db)
        assert r.returncode == 1 and "[错误]" in r.stdout


def test_cli_member_validation_without_override(tmp_path):
    # 无 DOC_KEEPER_DB → 校验生效。未登记名字必须报错（DATA_ROOT 指向 tmp 不落真库）。
    env = {**os.environ, "DATA_ROOT": str(tmp_path / "data")}
    env.pop("DOC_KEEPER_DB", None)
    r = subprocess.run([_sys.executable, _DOC_CLI, "profile-set",
                        "--member-name", "Nobody_XYZ", "--field", "x",
                        "--value", "1"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    assert r.returncode == 1
    assert "成员未登记" in r.stdout


class TestAgentWiring:
    def test_commands_allowed_and_routed(self):
        import agent_core
        for c in ("profile-set", "profile-unset", "profile-list"):
            assert c in agent_core.ALLOWED_COMMANDS
            assert agent_core._cli_path(c).parent.name == "Document_Keeper"

    def test_tools_registered(self):
        import agent_core
        names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert {"set_profile_field", "remove_profile_field"} <= names
        assert {"set_profile_field", "remove_profile_field"} <= set(agent_core._TOOL_MAP)

    def test_not_member_injected(self):
        # 目标成员是显式数据（member-name），不能被发送者身份覆盖
        import agent_core
        out = agent_core._apply_member(
            "set_profile_field",
            {"member-name": "Euphie", "field": "生日", "value": "2016-10-18"},
            "Jim Zheng")
        assert "member" not in out
        assert out["member-name"] == "Euphie"

    def test_profiles_context_renders(self, tmp_path):
        import agent_core
        db = str(tmp_path / "documents.db")
        doc_db.init_db(db)
        doc_db.set_profile("Jim Zheng", "生日", "1987-06-11", db_path=db)
        block = agent_core._profiles_context(db_path=db)
        assert "家庭成员资料" in block and "1987-06-11" in block
        assert agent_core._profiles_context(db_path=str(tmp_path / "empty.db")) == ""

    def test_system_prompt_mentions_profiles(self):
        import agent_core
        p = agent_core._build_system_prompt()
        assert "set_profile_field" in p
