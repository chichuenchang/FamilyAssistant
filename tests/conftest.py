# tests/conftest.py — pytest fixtures; skill sys.path via bootstrap.
import sys
from pathlib import Path

# 全部 skill 目录经 Agent_Runtime/bootstrap 一次挂上。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".codewhale" / "skills" / "Agent_Runtime"))
import bootstrap  # noqa: E402,F401

import pytest
import db as dbm  # the fixture below is named ``db`` — alias avoids shadowing


@pytest.fixture
def db(tmp_path):
    """Create a temporary SQLite database, initialise it, and yield its path.

    Every test passes this path to *every* db.py function so the real
    ``data/ledger.db`` is never touched.
    """
    db_path = str(tmp_path / "test.db")
    dbm.init_db(db_path=db_path)
    return db_path


@pytest.fixture
def doc_db_path(tmp_path):
    """Temporary SQLite database initialised with the documents table."""
    import doc_db as doc_dbm
    path = str(tmp_path / "docs.db")
    doc_dbm.init_db(db_path=path)
    return path


@pytest.fixture
def note_db_path(tmp_path):
    """Temporary SQLite database initialised with the notes table."""
    import note_db as note_dbm
    path = str(tmp_path / "notes.db")
    conn = note_dbm._connect(db_path=path)
    conn.close()
    return path


@pytest.fixture
def cal_db_path(tmp_path):
    """Temporary SQLite database initialised with the schedule_items table."""
    import cal_db as cal_dbm
    path = str(tmp_path / "cal.db")
    conn = cal_dbm._connect(db_path=path)
    conn.close()
    return path
