# tests/conftest.py — pytest fixtures for Expense_Tracker skill tests.
import sys
from pathlib import Path

# Make the skill directory importable from any cwd, ahead of all other imports.
SKILL_DIR = (
    Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Expense_Tracker"
)
sys.path.insert(0, str(SKILL_DIR))

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
