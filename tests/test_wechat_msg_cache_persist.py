# tests/test_wechat_msg_cache_persist.py — 近期消息缓存持久化：bot 重启不丢引用反查数据。
import wechat_ilink


def setup_function(_fn):
    wechat_ilink._RECENT_MSGS.clear()


def test_save_and_load_roundtrip(tmp_path):
    f = tmp_path / "recent.json"
    wechat_ilink._remember_msg(111, "第一条")
    wechat_ilink._remember_msg(222, "第二条")
    wechat_ilink._save_recent_msgs(f)

    wechat_ilink._RECENT_MSGS.clear()
    wechat_ilink._load_recent_msgs(f)
    assert wechat_ilink._RECENT_MSGS == {"111": "第一条", "222": "第二条"}


def test_load_missing_file_is_noop(tmp_path):
    wechat_ilink._load_recent_msgs(tmp_path / "nope.json")
    assert len(wechat_ilink._RECENT_MSGS) == 0


def test_load_corrupt_file_is_noop(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    wechat_ilink._load_recent_msgs(f)
    assert len(wechat_ilink._RECENT_MSGS) == 0


def test_load_respects_cap(tmp_path):
    f = tmp_path / "big.json"
    import json
    f.write_text(json.dumps({str(i): f"m{i}" for i in range(wechat_ilink._RECENT_MSGS_CAP + 30)}),
                 encoding="utf-8")
    wechat_ilink._load_recent_msgs(f)
    assert len(wechat_ilink._RECENT_MSGS) == wechat_ilink._RECENT_MSGS_CAP
