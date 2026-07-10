# tests/test_wechat_quote.py — 微信引用/回复消息注入 agent 上下文。
import wechat_ilink


def test_with_quote_prepends_quoted_title():
    out = wechat_ilink._with_quote("把这个记到日历", "周五下午3点牙医预约")
    assert out == "[引用: 周五下午3点牙医预约]\n把这个记到日历"


def test_with_quote_none_title_returns_text_unchanged():
    assert wechat_ilink._with_quote("记账 午餐45", None) == "记账 午餐45"


def test_with_quote_empty_title_returns_text_unchanged():
    assert wechat_ilink._with_quote("记账 午餐45", "") == "记账 午餐45"


def test_with_quote_preserves_text_verbatim():
    text = "  多行\n内容  "
    out = wechat_ilink._with_quote(text, "[图片]")
    assert out.endswith(text)
    assert out.startswith("[引用: [图片]]\n")
