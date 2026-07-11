# tests/test_wechat_quote_resolve.py — 引用消息解析：iLink ref_msg 只带 msg_id/时间戳，
# 不带原文（实测 2026-07-10），需从近期消息缓存反查。
import wechat_ilink


def setup_function(_fn):
    wechat_ilink._RECENT_MSGS.clear()


def test_no_ref_msg_returns_none():
    assert wechat_ilink._quoted_text({"text_item": {"text": "hi"}}) is None


def test_title_present_wins():
    item = {"ref_msg": {"title": "周五牙医预约"}}
    assert wechat_ilink._quoted_text(item) == "周五牙医预约"


def test_resolves_cached_message_by_msg_id():
    wechat_ilink._remember_msg(7481577540953012616, "测试一下，你现在能看到我引用的哪一句话吗")
    item = {"ref_msg": {"message_item": {"msg_id": "7481577540953012616",
                                         "create_time_ms": 1783747085000}}}
    assert wechat_ilink._quoted_text(item) == "测试一下，你现在能看到我引用的哪一句话吗"


def test_unknown_msg_id_falls_back_to_timestamp_placeholder():
    item = {"ref_msg": {"message_item": {"msg_id": "999", "create_time_ms": 1783747085000}}}
    out = wechat_ilink._quoted_text(item)
    assert out is not None
    assert "原文不可见" in out


def test_ref_without_msg_id_returns_none():
    assert wechat_ilink._quoted_text({"ref_msg": {"message_item": {}}}) is None


def test_remember_msg_caps_size():
    for i in range(wechat_ilink._RECENT_MSGS_CAP + 50):
        wechat_ilink._remember_msg(i, f"m{i}")
    assert len(wechat_ilink._RECENT_MSGS) == wechat_ilink._RECENT_MSGS_CAP
    assert "0" not in wechat_ilink._RECENT_MSGS          # 最旧被逐出
    assert wechat_ilink._RECENT_MSGS[str(wechat_ilink._RECENT_MSGS_CAP + 49)] == f"m{wechat_ilink._RECENT_MSGS_CAP + 49}"


def test_remember_msg_ignores_empty():
    wechat_ilink._remember_msg(None, "text")
    wechat_ilink._remember_msg(123, "")
    assert len(wechat_ilink._RECENT_MSGS) == 0
