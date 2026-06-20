# tests/test_telegram_pdf.py — Telegram PDF document download.
import urllib.request

import telegram_bot as tg


def test_download_document_saves_pdf_to_inbox(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(tg, "TOKEN", "T")
    monkeypatch.setattr(tg, "_api",
                        lambda action, params: {"ok": True,
                                                "result": {"file_path": "docs/x.pdf"}})

    class _Resp:
        def read(self):
            return b"%PDF-bytes"

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=30: _Resp())

    dest = tg.download_document("fid", "ConsentForm.pdf", member="Alex Lee")
    assert dest is not None
    assert dest.suffix == ".pdf"
    assert dest.read_bytes() == b"%PDF-bytes"
    assert "inbox" in dest.as_posix()
