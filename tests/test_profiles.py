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
