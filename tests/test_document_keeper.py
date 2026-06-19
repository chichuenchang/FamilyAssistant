# tests/test_document_keeper.py — Document Keeper skill tests.
import json
from datetime import date, timedelta

import pytest

import doc_models


def test_doc_models_constants():
    assert "other" in doc_models.DOC_TYPES or doc_models.DOC_TYPES
    assert doc_models.REMINDER_LEAD_DAYS >= 1
    assert doc_models.DOC_STATUSES == ("active", "expired", "archived", "superseded")
    assert "documents" in doc_models.SCHEMA


def test_doc_models_fallback_on_missing_config(tmp_path):
    assert doc_models._load_config_from(tmp_path / "nope.json") == {}


def test_default_db_is_family_ledger():
    """Documents share the Family ledger; files live under data/Family/documents."""
    assert doc_models.DB_PATH.as_posix().endswith("data/Family/ledger.db")
    assert doc_models.DOCUMENTS_DIR.as_posix().endswith("data/Family/documents")


import importlib.util as _ilu_doc  # noqa: E402
from pathlib import Path as _Path_doc  # noqa: E402

_DOC_CLI_PATH = (_Path_doc(__file__).resolve().parent.parent
                 / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")
_dspec = _ilu_doc.spec_from_file_location("doc_cli", _DOC_CLI_PATH)
doc_cli = _ilu_doc.module_from_spec(_dspec)
_dspec.loader.exec_module(doc_cli)


def test_store_file_returns_family_rel(tmp_path, monkeypatch):
    """A stored document path is recorded relative to data_root (Family/documents/...)."""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(doc_cli, "ROOT", tmp_path)
    monkeypatch.setattr(doc_cli, "DOCUMENTS_DIR", tmp_path / "data" / "Family" / "documents")
    src = tmp_path / "lease.pdf"
    src.write_bytes(b"pdf")
    rel = doc_cli._store_file(str(src), "lease", "2026 Lease")
    assert rel.startswith("Family/documents/lease/")
    assert rel.endswith(".pdf")


import doc_db


def _add(doc_db_path, **kw):
    args = dict(doc_type="lease", title="2026公寓租约", db_path=doc_db_path)
    args.update(kw)
    return doc_db.add_document(**args)


class TestAddGet:
    def test_add_and_get_roundtrip(self, doc_db_path):
        doc_id, dup = _add(doc_db_path, issuer="房东张三", doc_number="L-001",
                           issue_date="2026-03-01", expiry_date="2027-02-28",
                           action_note="提前60天通知房东", ocr_text="租赁合同 甲方 乙方")
        assert doc_id > 0 and dup is None
        d = doc_db.get_document(doc_id, db_path=doc_db_path)
        assert d["title"] == "2026公寓租约"
        assert d["expiry_date"] == "2027-02-28"
        assert d["status"] == "active"
        assert d["acknowledged"] == 0
        assert isinstance(d["data"], dict)

    def test_get_document_missing(self, doc_db_path):
        assert doc_db.get_document(999, db_path=doc_db_path) is None

    def test_invalid_doc_type_rejected(self, doc_db_path):
        with pytest.raises(ValueError):
            _add(doc_db_path, doc_type="nonsense")

    def test_invalid_date_rejected(self, doc_db_path):
        with pytest.raises(ValueError):
            _add(doc_db_path, expiry_date="2027/02/28")

    def test_init_idempotent_on_existing_ledger(self, db):
        # ``db`` fixture = Expense_Tracker-initialised ledger; adding the
        # documents table to it must not disturb existing tables.
        doc_db.init_db(db_path=db)
        doc_db.init_db(db_path=db)  # twice = no-op
        doc_id, _ = _add(db)
        assert doc_id > 0


class TestList:
    def test_filters_and_keyword(self, doc_db_path):
        _add(doc_db_path, title="租约A", ocr_text="甲方乙方", member="爸爸")
        _add(doc_db_path, doc_type="insurance", title="车险保单", ocr_text="保险金额")
        rows = doc_db.get_documents(doc_type="lease", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["租约A"]
        rows = doc_db.get_documents(keyword="保险", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["车险保单"]
        rows = doc_db.get_documents(member="爸爸", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["租约A"]

    def test_hidden_statuses_excluded_by_default(self, doc_db_path):
        doc_id, _ = _add(doc_db_path, title="旧租约")
        doc_db.update_document(doc_id, status="superseded", db_path=doc_db_path)
        assert doc_db.get_documents(db_path=doc_db_path) == []
        rows = doc_db.get_documents(status="superseded", db_path=doc_db_path)
        assert [r["title"] for r in rows] == ["旧租约"]


class TestDuplicates:
    def test_same_number_blocked(self, doc_db_path):
        _add(doc_db_path, doc_number="P-123")
        doc_id, dup = _add(doc_db_path, title="同一份", doc_number="P-123")
        assert doc_id == 0 and dup is not None

    def test_same_number_force(self, doc_db_path):
        _add(doc_db_path, doc_number="P-123")
        doc_id, dup = _add(doc_db_path, title="同一份", doc_number="P-123", force=True)
        assert doc_id > 0 and dup is None

    def test_same_file_hash_blocked(self, doc_db_path, tmp_path):
        f = tmp_path / "lease.jpg"
        f.write_bytes(b"identical bytes")
        _add(doc_db_path, file_path=str(f))
        doc_id, dup = _add(doc_db_path, title="重发同图", file_path=str(f))
        assert doc_id == 0 and dup is not None

    def test_superseded_not_counted_as_dup(self, doc_db_path):
        old_id, _ = _add(doc_db_path, doc_number="P-123")
        doc_db.update_document(old_id, status="superseded", db_path=doc_db_path)
        doc_id, dup = _add(doc_db_path, title="续约新合同", doc_number="P-123")
        assert doc_id > 0 and dup is None


class TestUpdateAckRemove:
    def test_update_fields(self, doc_db_path):
        doc_id, _ = _add(doc_db_path)
        assert doc_db.update_document(doc_id, issuer="新房东", db_path=doc_db_path)
        assert doc_db.get_document(doc_id, db_path=doc_db_path)["issuer"] == "新房东"

    def test_update_unknown_field_rejected(self, doc_db_path):
        doc_id, _ = _add(doc_db_path)
        with pytest.raises(ValueError):
            doc_db.update_document(doc_id, created_at="2020-01-01", db_path=doc_db_path)

    def test_ack_then_expiry_change_resets_ack(self, doc_db_path):
        doc_id, _ = _add(doc_db_path, expiry_date="2026-07-01")
        assert doc_db.ack_document(doc_id, db_path=doc_db_path)
        assert doc_db.get_document(doc_id, db_path=doc_db_path)["acknowledged"] == 1
        doc_db.update_document(doc_id, expiry_date="2027-07-01", db_path=doc_db_path)
        assert doc_db.get_document(doc_id, db_path=doc_db_path)["acknowledged"] == 0

    def test_remove_keeps_file_by_default(self, doc_db_path, tmp_path):
        f = tmp_path / "doc.jpg"
        f.write_bytes(b"x")
        doc_id, _ = _add(doc_db_path, file_path=str(f))
        assert doc_db.remove_document(doc_id, db_path=doc_db_path)
        assert f.exists()
        assert doc_db.get_document(doc_id, db_path=doc_db_path) is None

    def test_remove_with_delete_file(self, doc_db_path, tmp_path):
        f = tmp_path / "doc.jpg"
        f.write_bytes(b"x")
        doc_id, _ = _add(doc_db_path, file_path=str(f))
        assert doc_db.remove_document(doc_id, delete_file=True, db_path=doc_db_path)
        assert not f.exists()


class TestDue:
    TODAY = "2026-06-11"

    def test_window_and_days_left(self, doc_db_path):
        _add(doc_db_path, title="月内到期", expiry_date="2026-07-01")   # 20 天后，<30 默认窗口
        _add(doc_db_path, title="远期", expiry_date="2026-12-31")       # 窗口外
        _add(doc_db_path, title="已过期", expiry_date="2026-06-01")
        due = doc_db.due_documents(today=self.TODAY, db_path=doc_db_path)
        titles = [d["title"] for d in due]
        assert "月内到期" in titles and "已过期" in titles and "远期" not in titles
        by_title = {d["title"]: d for d in due}
        assert by_title["月内到期"]["days_left"] == 20
        assert by_title["已过期"]["days_left"] == -10

    def test_per_doc_remind_days_override(self, doc_db_path):
        _add(doc_db_path, title="提前90天", expiry_date="2026-09-01", remind_days=90)
        _add(doc_db_path, title="默认窗口", expiry_date="2026-09-01")
        titles = [d["title"] for d in doc_db.due_documents(today=self.TODAY, db_path=doc_db_path)]
        assert titles == ["提前90天"]

    def test_explicit_days_param_wins(self, doc_db_path):
        _add(doc_db_path, title="远期", expiry_date="2026-12-31", remind_days=5)
        titles = [d["title"] for d in doc_db.due_documents(days=365, today=self.TODAY, db_path=doc_db_path)]
        assert titles == ["远期"]

    def test_unacknowledged_sorted_first(self, doc_db_path):
        a, _ = _add(doc_db_path, title="已确认", expiry_date="2026-06-15")
        _add(doc_db_path, title="未确认", expiry_date="2026-06-20")
        doc_db.ack_document(a, db_path=doc_db_path)
        titles = [d["title"] for d in doc_db.due_documents(today=self.TODAY, db_path=doc_db_path)]
        assert titles == ["未确认", "已确认"]

    def test_non_active_excluded(self, doc_db_path):
        doc_id, _ = _add(doc_db_path, title="已归档", expiry_date="2026-06-15")
        doc_db.update_document(doc_id, status="archived", db_path=doc_db_path)
        assert doc_db.due_documents(today=self.TODAY, db_path=doc_db_path) == []

    def test_due_on_uninitialized_ledger_returns_empty(self, tmp_path):
        # Production bug: the reminder polls every cycle, but on a ledger where
        # no document was ever added init_db() never ran, so the documents table
        # is missing. Reading it must return empty, not raise
        # "no such table: documents".
        fresh = str(tmp_path / "virgin.db")
        assert doc_db.due_documents(today=self.TODAY, db_path=fresh) == []


import subprocess
import sys as _sys
from pathlib import Path as _Path

_CLI = str(_Path(__file__).resolve().parent.parent
           / ".codewhale" / "skills" / "Document_Keeper" / "cli.py")


def _run_cli(*args):
    # BACKUP_STATE_DIR: 写命令会标记备份脏位，重定向到临时目录避免碰真实 data/
    import os as _os
    import tempfile as _tempfile
    env = {**_os.environ, "BACKUP_STATE_DIR": _tempfile.mkdtemp()}
    return subprocess.run(
        [_sys.executable, _CLI, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


class TestCli:
    def test_doc_add_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        r = _run_cli("doc-add", "--type", "lease", "--title", "测试租约",
                     "--expiry", "2027-01-01")
        assert r.returncode == 0, r.stderr
        assert "#1" in r.stdout
        r = _run_cli("doc-list")
        assert r.returncode == 0
        assert "测试租约" in r.stdout

    def test_doc_add_bad_type_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        r = _run_cli("doc-add", "--type", "nope", "--title", "x")
        assert r.returncode != 0

    def test_doc_add_bad_date_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        r = _run_cli("doc-add", "--type", "lease", "--title", "x",
                     "--expiry", "01/01/2027")
        assert r.returncode == 1

    def test_doc_due_and_ack(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_KEEPER_DB", str(tmp_path / "cli.db"))
        _run_cli("doc-add", "--type", "lease", "--title", "快到期",
                 "--expiry", "2026-06-20")
        r = _run_cli("doc-due")
        assert "快到期" in r.stdout
        r = _run_cli("doc-ack", "--id", "1")
        assert r.returncode == 0
        r = _run_cli("doc-show", "--id", "1")
        assert "已确认" in r.stdout


import reminder


@pytest.fixture
def reminder_env(doc_db_path, tmp_path, monkeypatch):
    """Isolated state file + fake member registry + temp db."""
    monkeypatch.setattr(reminder, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(reminder, "load_members",
                        lambda: {"爸爸": {"telegram": ["111", "222"], "wechat": ["w1"]}})
    return doc_db_path


class TestReminder:
    def test_due_message_lists_unacknowledged_only(self, reminder_env):
        _add(reminder_env, title="快到期", expiry_date=date.today().isoformat())
        acked, _ = _add(reminder_env, title="已确认", expiry_date=date.today().isoformat())
        doc_db.ack_document(acked, db_path=reminder_env)
        msg = reminder.due_message(db_path=reminder_env)
        assert "快到期" in msg and "已确认" not in msg

    def test_due_message_none_when_nothing(self, reminder_env):
        assert reminder.due_message(db_path=reminder_env) is None

    def test_push_once_per_day_per_channel(self, reminder_env):
        _add(reminder_env, title="快到期", expiry_date=date.today().isoformat())
        sent = []
        ok = reminder.check_and_push(lambda cid, text: sent.append(cid),
                                     "telegram", db_path=reminder_env)
        assert ok and sent == ["111", "222"]
        sent.clear()
        ok = reminder.check_and_push(lambda cid, text: sent.append(cid),
                                     "telegram", db_path=reminder_env)
        assert not ok and sent == []        # 同日不再推
        ok = reminder.check_and_push(lambda cid, text: sent.append(cid),
                                     "wechat", db_path=reminder_env)
        assert ok and sent == ["w1"]        # 不同频道独立状态

    def test_push_failure_keeps_state_for_retry(self, reminder_env):
        _add(reminder_env, title="快到期", expiry_date=date.today().isoformat())

        def boom(cid, text):
            raise RuntimeError("network down")

        assert not reminder.check_and_push(boom, "telegram", db_path=reminder_env)
        sent = []
        assert reminder.check_and_push(lambda cid, text: sent.append(cid),
                                       "telegram", db_path=reminder_env)
        assert sent == ["111", "222"]       # 失败未记状态，下一轮重试成功

    def test_nothing_due_marks_day_done(self, reminder_env):
        calls = []
        assert not reminder.check_and_push(lambda cid, text: calls.append(cid),
                                           "telegram", db_path=reminder_env)
        assert calls == []
        assert reminder.STATE_FILE.exists()


import agent_core


class TestAgentIntegration:
    def test_doc_commands_route_to_document_keeper_cli(self):
        p = agent_core._cli_path("doc-add")
        assert p.parts[-2] == "Document_Keeper"
        p = agent_core._cli_path("add")
        assert p.parts[-2] == "Expense_Tracker"

    def test_doc_commands_whitelisted_except_remove(self):
        for cmd in ("doc-add", "doc-list", "doc-show", "doc-due", "doc-update", "doc-ack"):
            assert cmd in agent_core.ALLOWED_COMMANDS
        assert "doc-remove" not in agent_core.ALLOWED_COMMANDS

    def test_add_document_is_member_write_tool(self):
        out = agent_core._apply_member("add_document",
                                       {"type": "lease", "member": "假冒"}, "爸爸")
        assert out["member"] == "爸爸"

    def test_document_tools_registered(self):
        for name in ("add_document", "list_documents", "show_document",
                     "due_documents", "update_document", "ack_document"):
            assert name in agent_core._TOOL_MAP
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert "add_document" in schema_names and "due_documents" in schema_names
