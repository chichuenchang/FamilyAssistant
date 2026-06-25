# tests/test_send_files.py — agent 发文档/文件 测试
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC_SKILL = ROOT / ".codewhale" / "skills" / "Document_Keeper"
AR = ROOT / ".codewhale" / "skills" / "Agent_Runtime"
sys.path.insert(0, str(DOC_SKILL))
sys.path.insert(0, str(AR))


# ── Task 1: doc-file CLI ────────────────────────────────────

def _seed_doc(db, file_path):
    import doc_db
    doc_db.init_db(db_path=db)
    res = doc_db.add_document(title="租约", doc_type="lease", file_path=file_path,
                              db_path=db)
    return res[0] if isinstance(res, tuple) else res


def _doc_cli(db, *args):
    env = dict(os.environ, DOC_KEEPER_DB=db, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(DOC_SKILL / "cli.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_doc_file_prints_path(tmp_path):
    db = str(tmp_path / "doc.db")
    doc_id = _seed_doc(db, "Family/documents/lease/lease.pdf")
    r = _doc_cli(db, "doc-file", "--id", str(doc_id))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Family/documents/lease/lease.pdf"


def test_doc_file_no_file(tmp_path):
    db = str(tmp_path / "doc.db")
    doc_id = _seed_doc(db, "")
    assert _doc_cli(db, "doc-file", "--id", str(doc_id)).returncode == 1


def test_doc_file_unknown_id(tmp_path):
    db = str(tmp_path / "doc.db")
    import doc_db
    doc_db.init_db(db_path=db)
    assert _doc_cli(db, "doc-file", "--id", "999").returncode == 1


# ── Task 2: 3-tuple split_reply + DOC sentinel ──────────────

def test_split_reply_3tuple():
    import agent_core as ac
    r = ("hi\n" + ac.IMG_SENTINEL + "a.png\n" + ac.DOC_SENTINEL + "Family/x.pdf")
    text, imgs, docs = ac.split_reply(r)
    assert text == "hi" and imgs == ["a.png"] and docs == ["Family/x.pdf"]
    # passthrough
    assert ac.split_reply("plain") == ("plain", [], [])
    # doc only
    t, i, d = ac.split_reply(ac.DOC_SENTINEL + "p.pdf")
    assert t == "" and i == [] and d == ["p.pdf"]


# ── Task 3: send_file gate ──────────────────────────────────

def test_resolve_sendable_gate(tmp_path, monkeypatch):
    # No members.json → member_dir_name falls back to slug: "Alex"->"alex", "Bob"->"bob".
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    import importlib, paths as _p
    importlib.reload(_p)
    import agent_core as ac
    importlib.reload(ac)
    fam = tmp_path / "Family" / "documents"; fam.mkdir(parents=True)
    (fam / "lease.pdf").write_bytes(b"x")
    alex = tmp_path / "alex" / "notes"; alex.mkdir(parents=True)
    (alex / "card.png").write_bytes(b"x")
    bob = tmp_path / "bob" / "notes"; bob.mkdir(parents=True)
    (bob / "secret.png").write_bytes(b"x")

    assert ac._resolve_sendable("Family/documents/lease.pdf", "Alex") == "Family/documents/lease.pdf"
    assert ac._resolve_sendable("alex/notes/card.png", "Alex") == "alex/notes/card.png"
    assert ac._resolve_sendable("bob/notes/secret.png", "Alex") is None
    assert ac._resolve_sendable("../outside.txt", "Alex") is None
    assert ac._resolve_sendable("Family/documents/missing.pdf", "Alex") is None


# ── Task 4: Telegram sendDocument multipart ─────────────────

def test_tg_send_document_multipart(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    import importlib, telegram_bot as tg
    importlib.reload(tg)
    f = tmp_path / "lease.pdf"; f.write_bytes(b"%PDF-1.4 test")
    captured = {}

    class FakeResp:
        def read(self): return b'{"ok":true}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["ct"] = req.headers.get("Content-type") or req.headers.get("Content-Type")
        captured["body"] = req.data
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ok = tg.send_document(123, str(f))
    assert ok is True
    assert b"multipart/form-data" in captured["ct"].encode()
    assert b'name="document"' in captured["body"]
    assert b'name="chat_id"' in captured["body"]
    assert b"%PDF-1.4 test" in captured["body"]
