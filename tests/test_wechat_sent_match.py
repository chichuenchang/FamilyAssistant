# tests/test_wechat_sent_match.py — 引用 bot 回复的时间戳匹配：服务端不回传出站
# message_id（send 响应 {}，轮询也不回显 BOT 消息），只能按发送时间对齐。
import wechat_ilink


def setup_function(_fn):
    wechat_ilink._SENT_REPLIES.clear()
    wechat_ilink._RECENT_MSGS.clear()


def test_match_within_window():
    wechat_ilink._remember_sent("好的，已加到日历", ts_ms=1_000_000)
    assert wechat_ilink._match_sent_by_time(1_002_000) == "好的，已加到日历"


def test_no_match_outside_window():
    wechat_ilink._remember_sent("好的，已加到日历", ts_ms=1_000_000)
    assert wechat_ilink._match_sent_by_time(1_000_000 + wechat_ilink._SENT_MATCH_WINDOW_MS + 1) is None


def test_closest_wins():
    wechat_ilink._remember_sent("回复A", ts_ms=1_000_000)
    wechat_ilink._remember_sent("回复B", ts_ms=1_006_000)
    assert wechat_ilink._match_sent_by_time(1_005_000) == "回复B"


def test_sent_cap():
    for i in range(wechat_ilink._SENT_REPLIES_CAP + 20):
        wechat_ilink._remember_sent(f"r{i}", ts_ms=i * 1000)
    assert len(wechat_ilink._SENT_REPLIES) == wechat_ilink._SENT_REPLIES_CAP


def test_quoted_text_falls_back_to_sent_match():
    wechat_ilink._remember_sent("已添加活动 #125", ts_ms=1783747085000)
    item = {"ref_msg": {"message_item": {"msg_id": "42", "create_time_ms": 1783747086000}}}
    out = wechat_ilink._quoted_text(item)
    assert "已添加活动 #125" in out
    assert "回复" in out  # 标明是 bot 自己的话，避免 agent 当成用户消息


def test_quoted_text_sent_match_truncates_long_reply():
    wechat_ilink._remember_sent("长" * 500, ts_ms=1_000_000)
    item = {"ref_msg": {"message_item": {"msg_id": "42", "create_time_ms": 1_000_500}}}
    out = wechat_ilink._quoted_text(item)
    assert len(out) < 300


def test_msg_id_cache_still_wins_over_sent_match():
    wechat_ilink._remember_msg("42", "用户原话")
    wechat_ilink._remember_sent("bot的话", ts_ms=1_000_000)
    item = {"ref_msg": {"message_item": {"msg_id": "42", "create_time_ms": 1_000_000}}}
    assert wechat_ilink._quoted_text(item) == "用户原话"


def test_sent_persist_roundtrip(tmp_path):
    f = tmp_path / "sent.json"
    wechat_ilink._remember_sent("第一条", ts_ms=111, persist_file=f)
    wechat_ilink._remember_sent("第二条", ts_ms=222, persist_file=f)
    wechat_ilink._SENT_REPLIES.clear()
    wechat_ilink._load_sent_replies(f)
    assert wechat_ilink._match_sent_by_time(222) == "第二条"
    assert wechat_ilink._match_sent_by_time(111) == "第一条"
