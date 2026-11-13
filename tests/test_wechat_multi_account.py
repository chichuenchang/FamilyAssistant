# tests/test_wechat_multi_account.py — 一个守护进程挂多个微信 ClawBot 账号：
# 凭据发现、后台推送按用户路由到对的 bot、多路轮询串行分发、出站记录按用户匹配。
import threading

import pytest

import wechat_ilink


# ── 凭据文件 ────────────────────────────────────────────────

def test_creds_path_default_and_labelled(tmp_path):
    assert wechat_ilink.creds_path(None, tmp_path) == tmp_path / "wechat_creds.json"
    assert wechat_ilink.creds_path("mom", tmp_path) == tmp_path / "wechat_creds_mom.json"


def test_creds_path_rejects_unsafe_label(tmp_path):
    with pytest.raises(ValueError):
        wechat_ilink.creds_path("../x", tmp_path)


def test_creds_files_finds_all_accounts_skips_cursor(tmp_path):
    for name in ("wechat_creds.json", "wechat_creds.json.sync",
                 "wechat_creds_mom.json", "wechat_creds_mom.json.sync", "other.json"):
        (tmp_path / name).write_text("{}")
    assert wechat_ilink.creds_files(tmp_path) == {
        "default": tmp_path / "wechat_creds.json",
        "mom": tmp_path / "wechat_creds_mom.json",
    }


# ── 推送路由 ────────────────────────────────────────────────

class FakeBot:
    def __init__(self, users=(), msgs=()):
        self._ctx_cache = {u: "ctx" for u in users}
        self._msgs = list(msgs)
        self.sent, self.dispatched, self.stopped = [], [], False

    def send_text(self, to, text):
        self.sent.append((to, text))

    def messages(self):
        yield from self._msgs

    def _dispatch(self, msg):
        self.dispatched.append((msg, threading.current_thread()))

    def stop(self):
        self.stopped = True


def test_push_goes_to_bot_that_knows_user():
    a, b = FakeBot(users=["dad"]), FakeBot(users=["mom"])
    t = wechat_ilink.WeChatTransport([a, b])
    t.send_text("mom", "提醒")
    assert b.sent == [("mom", "提醒")] and a.sent == []


def test_push_to_unseen_user_falls_back_to_first_bot():
    a, b = FakeBot(), FakeBot()
    wechat_ilink.WeChatTransport([a, b]).send_text("kid", "hi")
    assert a.sent == [("kid", "hi")] and b.sent == []


# ── 多路轮询，单线程分发 ───────────────────────────────────

def test_serve_dispatches_every_bot_on_calling_thread():
    a, b = FakeBot(msgs=["a1", "a2"]), FakeBot(msgs=["b1"])
    wechat_ilink.serve([a, b])
    me = threading.current_thread()
    assert [m for m, _ in a.dispatched] == ["a1", "a2"]
    assert [m for m, _ in b.dispatched] == ["b1"]
    assert all(th is me for _, th in a.dispatched + b.dispatched)


# ── 出站记录按用户 ──────────────────────────────────────────

def setup_function(_fn):
    wechat_ilink._SENT_REPLIES.clear()
    wechat_ilink._RECENT_MSGS.clear()


def test_sent_match_ignores_other_users_reply():
    wechat_ilink._remember_sent("给爸爸的", ts_ms=1_000_000, user="dad")
    wechat_ilink._remember_sent("给妈妈的", ts_ms=1_000_500, user="mom")
    assert wechat_ilink._match_sent_by_time(1_000_400, user="dad") == "给爸爸的"


def test_quoted_text_uses_sender_scope():
    wechat_ilink._remember_sent("给爸爸的", ts_ms=1_000_000, user="dad")
    wechat_ilink._remember_sent("给妈妈的", ts_ms=1_000_100, user="mom")
    item = {"ref_msg": {"message_item": {"msg_id": "9", "create_time_ms": 1_000_100}}}
    assert "给爸爸的" in wechat_ilink._quoted_text(item, user="dad")


def test_legacy_sent_entries_still_load(tmp_path):
    f = tmp_path / "sent.json"
    f.write_text('[[111, "旧格式"]]', encoding="utf-8")
    wechat_ilink._load_sent_replies(f)
    assert wechat_ilink._match_sent_by_time(111, user="dad") == "旧格式"


def test_quoting_a_push_resolves_pushed_text():
    t = wechat_ilink.WeChatTransport([FakeBot(users=["mom"])])
    assert t.push_text("mom", "新邮件：学校通知")
    ts = wechat_ilink._SENT_REPLIES[-1][0]
    item = {"ref_msg": {"message_item": {"msg_id": "7", "create_time_ms": ts}}}
    assert "新邮件：学校通知" in wechat_ilink._quoted_text(item, user="mom")
