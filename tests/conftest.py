# tests/conftest.py — pytest fixtures for Expense_Tracker skill tests.
import sys
from pathlib import Path

# Make the skill directory importable from any cwd, ahead of all other imports.
SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Expense_Tracker"
)
sys.path.insert(0, str(SKILL_DIR))

AGENT_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Agent_Runtime"
)
sys.path.insert(0, str(AGENT_DIR))

DOC_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Document_Keeper"
)
sys.path.insert(0, str(DOC_DIR))

BACKUP_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Remote_Backup"
)
sys.path.insert(0, str(BACKUP_DIR))

NOTE_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Note_Keeper"
)
sys.path.insert(0, str(NOTE_DIR))

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
