# tests/test_telegram_quote.py — Telegram 引用/回复注入：reply_to_message 自带原文，直接取用。
import telegram_bot


def test_no_reply_returns_none():
    assert telegram_bot._tg_quoted_text({"text": "hi"}) is None


def test_reply_text_returned():
    msg = {"reply_to_message": {"text": "周五下午3点牙医预约"}}
    assert telegram_bot._tg_quoted_text(msg) == "周五下午3点牙医预约"


def test_reply_caption_used_when_no_text():
    msg = {"reply_to_message": {"caption": "上个月电费单", "photo": [{"file_id": "x"}]}}
    assert telegram_bot._tg_quoted_text(msg) == "上个月电费单"


def test_reply_photo_placeholder():
    msg = {"reply_to_message": {"photo": [{"file_id": "x"}]}}
    assert telegram_bot._tg_quoted_text(msg) == "[图片]"


def test_reply_document_placeholder_with_name():
    msg = {"reply_to_message": {"document": {"file_name": "lease.pdf"}}}
    out = telegram_bot._tg_quoted_text(msg)
    assert "lease.pdf" in out


def test_long_reply_truncated():
    msg = {"reply_to_message": {"text": "长" * 500}}
    out = telegram_bot._tg_quoted_text(msg)
    assert len(out) <= 201  # 200 + 省略号


def test_with_quote_injection():
    out = telegram_bot._with_quote("把这个记到日历", "周五下午3点牙医预约")
    assert out == "[引用: 周五下午3点牙医预约]\n把这个记到日历"
    assert telegram_bot._with_quote("原样", None) == "原样"
