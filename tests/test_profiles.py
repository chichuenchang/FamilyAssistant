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
