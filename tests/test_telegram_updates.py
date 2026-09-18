"""telegram_bot.handle_updates：附言 / 来件 / 文字在一批 update 里的派发。"""
import telegram_bot as tg


class Rec:
    """只记 gate / on_media / on_text 调用的 Transport 替身。"""

    def __init__(self):
        self.calls = []

    def gate(self, chat_id):
        return "Alex"

    def on_media(self, target, user, member, path):
        self.calls.append(("media", str(path) if path else None))

    def on_text(self, target, user, member, text, quoted=None):
        self.calls.append(("text", text))


def _photo(uid, caption="", fid="f"):
    msg = {"chat": {"id": 1}, "photo": [{"file_id": fid}]}
    if caption:
        msg["caption"] = caption
    return {"update_id": uid, "message": msg}


def _doc(uid, name, caption=""):
    msg = {"chat": {"id": 1}, "document": {"file_id": "d", "file_name": name},
           "caption": caption}
    return {"update_id": uid, "message": msg}


def _setup(monkeypatch, photo_ok=True):
    sent = []
    monkeypatch.setattr(tg, "download_photo",
                        lambda fid, member: f"{fid}.jpg" if photo_ok else None)
    monkeypatch.setattr(tg, "download_document", lambda fid, name, member: f"{name}")
    monkeypatch.setattr(tg, "send_message", lambda chat_id, text: sent.append(text))
    return sent


def test_offset_advances(monkeypatch):
    _setup(monkeypatch)
    assert tg.handle_updates(Rec(), [_photo(5), _photo(9)], 3) == 9


def test_failed_download_drops_caption(monkeypatch):
    _setup(monkeypatch, photo_ok=False)
    t = Rec()
    tg.handle_updates(t, [_photo(1, "记账")], 0)
    assert t.calls == [("media", None)]


def test_unsupported_doc_drops_caption(monkeypatch):
    sent = _setup(monkeypatch)
    t = Rec()
    tg.handle_updates(t, [_doc(1, "a.docx", "记账")], 0)
    assert t.calls == [] and "暂不支持" in sent[0]
