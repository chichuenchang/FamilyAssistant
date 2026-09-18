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

    def on_text(self, target, user, member, text, quoted=None, media=None):
        self.calls.append(("text", text, media))


def _photo(uid, caption="", fid="f", group=None):
    msg = {"chat": {"id": 1}, "photo": [{"file_id": fid}]}
    if caption:
        msg["caption"] = caption
    if group:
        msg["media_group_id"] = group
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


def _text(uid, text):
    return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text}}


def test_caption_takes_only_own_media_text_takes_none(monkeypatch):
    _setup(monkeypatch)
    t = Rec()
    tg.handle_updates(t, [_photo(1, "记账", fid="r"), _text(2, "顺便问天气")], 0)
    assert t.calls == [("text", "记账", ["r.jpg"]), ("text", "顺便问天气", None)]


def test_album_caption_gets_whole_album(monkeypatch):
    _setup(monkeypatch)
    t = Rec()
    tg.handle_updates(t, [_photo(1, "记账", fid="a", group="g"),
                          _photo(2, fid="b", group="g"),
                          _photo(3, fid="c", group="g")], 0)
    assert t.calls == [("text", "记账", ["a.jpg", "b.jpg", "c.jpg"])]


def test_uncaptioned_media_held(monkeypatch):
    _setup(monkeypatch)
    t = Rec()
    tg.handle_updates(t, [_photo(1, fid="a"), _photo(2, fid="b", group="g")], 0)
    assert t.calls == [("media", "a.jpg"), ("media", "b.jpg")]
