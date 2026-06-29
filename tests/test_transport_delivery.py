# tests/test_transport_delivery.py — 哨兵 → 传输层投递分发（先图、再文档、最后文字）。
# 覆盖 _send_reply：split_reply 三元组拆分 + data_root 内/存在性闸门 + 投递顺序。
import agent_core
import telegram_bot


def _seed(tmp_path):
    """在 DATA_ROOT 下造真实图/文档文件，返回 (img_rel, doc_rel)。"""
    img = tmp_path / "Alex" / "charts" / "c.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\x89PNG\r\n")
    doc = tmp_path / "Family" / "documents" / "lease" / "d.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"%PDF-1.4")
    return "Alex/charts/c.png", "Family/documents/lease/d.pdf"


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram_bot, "send_photo", lambda cid, p: calls.append(("photo", p)))
    monkeypatch.setattr(telegram_bot, "send_document", lambda cid, p: calls.append(("doc", p)))
    monkeypatch.setattr(telegram_bot, "send_message", lambda cid, t: calls.append(("text", t)))
    return calls


def test_send_reply_dispatches_image_then_doc_then_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    img_rel, doc_rel = _seed(tmp_path)
    calls = _capture(monkeypatch)
    reply = f"看图说明\n{agent_core.IMG_SENTINEL}{img_rel}\n{agent_core.DOC_SENTINEL}{doc_rel}"
    telegram_bot._send_reply(123, reply)
    assert [c[0] for c in calls] == ["photo", "doc", "text"]
    assert calls[0][1].endswith("c.png")
    assert calls[1][1].endswith("d.pdf")
    assert calls[2][1] == "看图说明"


def test_send_reply_drops_missing_and_escaping_paths(tmp_path, monkeypatch):
    # 不存在的图（存在性闸门）+ 逃逸 data_root 的文档（根闸门）→ 都不发，文字照发。
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    calls = _capture(monkeypatch)
    reply = (f"hi\n{agent_core.IMG_SENTINEL}Alex/charts/missing.png"
             f"\n{agent_core.DOC_SENTINEL}../../etc/passwd")
    telegram_bot._send_reply(123, reply)
    assert calls == [("text", "hi")]


def test_send_reply_text_only_when_no_sentinels(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    calls = _capture(monkeypatch)
    telegram_bot._send_reply(123, "纯文字回复")
    assert calls == [("text", "纯文字回复")]
