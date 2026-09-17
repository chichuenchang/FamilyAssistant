# tests/test_mail_keeper.py — Mail Keeper skill tests.
# No real family names, addresses, or credentials in test data. Never hits Gmail:
# every provider call goes through the stubbed gmail_provider._http.
import base64
import json
import os
import time

import pytest

import agent_core as ac
import gmail_provider as gp
import mail_draft as md


PREFIX = "TESTMAIL"
# 被测对象就是 bot 实际加载的那个 manifest 模块（文件名跨 skill 重复，无法按名 import）
at = ac.REGISTRY.modules["Mail_Keeper"]


@pytest.fixture(autouse=True)
def _no_real_gmail(monkeypatch):
    """Strip inherited GMAIL_*/TESTMAIL_* and give the provider fake creds."""
    for k in list(os.environ):
        if k.startswith(("GMAIL_", f"{PREFIX}_")):
            monkeypatch.delenv(k, raising=False)
    for v in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN"):
        monkeypatch.setenv(f"{PREFIX}_{v}", "fake")
    gp._token_cache.clear()
    monkeypatch.setitem(gp._token_cache, PREFIX, ("fake-access", time.time() + 3600))


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _msg(mid="m1", *, subject="Invoice", frm="Sender <s@example.com>",
         body="hello there", reply_to="", mime="text/plain"):
    headers = [{"name": "From", "value": frm}, {"name": "Subject", "value": subject},
               {"name": "Date", "value": "Mon, 1 Jun 2026 09:00:00 +0000"},
               {"name": "To", "value": "me@example.com"},
               {"name": "Message-ID", "value": f"<{mid}@example.com>"}]
    if reply_to:
        headers.append({"name": "Reply-To", "value": reply_to})
    return {"id": mid, "threadId": f"t-{mid}", "snippet": "hello", "labelIds": ["UNREAD"],
            "payload": {"mimeType": mime, "headers": headers,
                        "body": {"data": _b64(body)}}}


class _Stub:
    """Records requests; answers Gmail endpoints from a canned message table."""

    def __init__(self, messages):
        self.messages = {m["id"]: m for m in messages}
        self.calls = []

    def __call__(self, method, url, data=None, headers=None):
        self.calls.append((method, url, json.loads(data) if data else None))
        if "/messages/send" in url:
            return 200, b'{"id": "sent-1"}'
        if "/messages?" in url or url.endswith("/messages"):
            ids = [{"id": m, "threadId": f"t-{m}"} for m in self.messages]
            return 200, json.dumps({"messages": ids}).encode()
        mid = url.split("/messages/")[1].split("?")[0]
        return 200, json.dumps(self.messages[mid]).encode()


@pytest.fixture
def stub(monkeypatch):
    s = _Stub([_msg()])
    monkeypatch.setattr(gp, "_http", s)
    return s


class TestProviderParsing:
    def test_search_returns_headers_and_unread(self, stub):
        rows = gp.search("is:unread", 5, PREFIX)
        assert rows[0]["from"] == "Sender <s@example.com>"
        assert rows[0]["subject"] == "Invoice"
        assert rows[0]["unread"] is True

    def test_get_message_extracts_plain_body(self, stub):
        assert gp.get_message("m1", PREFIX)["body"] == "hello there"

    def test_html_only_body_is_stripped_to_text(self, monkeypatch):
        s = _Stub([_msg(body="<p>hi <b>there</b></p><script>x()</script>",
                        mime="text/html")])
        monkeypatch.setattr(gp, "_http", s)
        assert gp.get_message("m1", PREFIX)["body"] == "hi there"

    def test_multipart_prefers_plain_over_html(self, monkeypatch):
        m = _msg()
        m["payload"] = {"mimeType": "multipart/alternative", "headers": m["payload"]["headers"],
                        "parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>rich</p>")}},
                                  {"mimeType": "text/plain", "body": {"data": _b64("plain")}}]}
        monkeypatch.setattr(gp, "_http", _Stub([m]))
        assert gp.get_message("m1", PREFIX)["body"] == "plain"

    def test_api_error_raises(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", lambda *a, **k: (403, b'{"error":"denied"}'))
        with pytest.raises(RuntimeError):
            gp.get_message("m1", PREFIX)


class TestReplyAddressing:
    def test_reply_to_header_wins_over_from(self):
        msg = {"from": "A <a@example.com>", "reply_to": "B <b@example.com>"}
        assert gp.reply_recipient(msg) == "b@example.com"

    def test_subject_gets_re_prefix_once(self):
        assert gp.reply_subject("Invoice") == "Re: Invoice"
        assert gp.reply_subject("Re: Invoice") == "Re: Invoice"

    def test_send_reply_threads_and_targets_original_sender(self, stub):
        msg = gp.get_message("m1", PREFIX)
        assert gp.send_reply(msg, "my answer", PREFIX) == "sent-1"
        method, url, payload = stub.calls[-1]
        assert (method, "/messages/send" in url) == ("POST", True)
        assert payload["threadId"] == "t-m1"
        raw = base64.urlsafe_b64decode(payload["raw"] + "==").decode()
        assert "To: s@example.com" in raw
        assert "Subject: Re: Invoice" in raw
        assert "In-Reply-To: <m1@example.com>" in raw
        assert "my answer" in raw


class TestDraftGate:
    """Send is gated by code, not by the LLM's good behaviour."""

    def _put(self, member="MemberA", *, age_s=0.0):
        return md.put(member, {"to": "s@example.com", "subject": "Re: Invoice",
                               "body": "ok"}, now=time.time() - age_s)

    def test_no_draft_refuses(self):
        md.drop("MemberA")
        draft, why = md.check("MemberA", turn_at=time.time(), text="确认发送")
        assert draft is None and "没有待发送" in why

    def test_same_turn_send_refused(self):
        turn_at = time.time()
        self._put()                                  # drafted during this turn
        draft, why = md.check("MemberA", turn_at=turn_at, text="确认发送")
        assert draft is None and "这一轮" in why

    def test_next_turn_with_confirmation_passes(self):
        self._put(age_s=5)
        draft, why = md.check("MemberA", turn_at=time.time(), text="确认发送")
        assert why == "" and draft["to"] == "s@example.com"

    def test_next_turn_without_confirmation_refused(self):
        self._put(age_s=5)
        draft, why = md.check("MemberA", turn_at=time.time(), text="那封账单多少钱？")
        assert draft is None and "没有明确确认" in why

    @pytest.mark.parametrize("text", [
        "不行，先别发送", "帮我看下银行那封", "不确定", "不同意", "don't send", "token",
        "ok", "好的谢谢", "确认一下内容再说"])
    def test_negations_substrings_and_bare_acks_are_not_consent(self, text):
        self._put(age_s=5)
        draft, why = md.check("MemberA", turn_at=time.time(), text=text)
        assert draft is None and "没有明确确认" in why

    @pytest.mark.parametrize("text", ["确认", "好的，发送吧", "可以发", "OK, send it!"])
    def test_whole_sentence_confirmations_pass(self, text):
        self._put(age_s=5)
        assert md.check("MemberA", turn_at=time.time(), text=text)[1] == ""

    def test_expired_draft_refused_and_dropped(self):
        self._put(age_s=md.TTL_S + 1)
        draft, why = md.check("MemberA", turn_at=time.time(), text="确认发送")
        assert draft is None and "过期" in why
        assert md.get("MemberA") is None

    def test_drafts_are_per_member(self):
        self._put("MemberA", age_s=5)
        self._put("MemberB", age_s=5)
        md.drop("MemberA")
        assert md.get("MemberA") is None and md.get("MemberB") is not None


class TestAgentWiring:
    def test_tools_registered_and_member_locked(self):
        names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
        for t in ("check_mail", "read_mail", "draft_reply", "send_reply"):
            assert t in names and t in ac._TOOL_MAP
            assert t in ac._MEMBER_LOCKED
        assert {"check_mail", "read_mail", "draft_reply"} <= ac._UNTRUSTED_TOOLS

    def test_member_cannot_be_overridden_by_llm(self):
        out = ac._apply_member("check_mail", {"member": "Other"}, "MemberA")
        assert out["member"] == "MemberA"

    def test_send_reply_gets_turn_context_injected(self):
        out = ac._apply_context("send_reply", {}, "wechat", "u1", "MemberA",
                                turn_at=123.0, text="确认发送")
        assert out["__turn_at"] == 123.0 and out["__text"] == "确认发送"

    def test_other_tools_get_no_turn_context(self):
        out = ac._apply_context("check_mail", {}, "wechat", "u1", "MemberA",
                                turn_at=123.0, text="x")
        assert "__turn_at" not in out and "__text" not in out


class TestGateSeesOnlyTheUsersOwnWords:
    """__text must not contain quoted / OCR / prompt text (it all says 发送 somewhere)."""

    @pytest.fixture
    def agent(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        self.got = {}
        monkeypatch.setitem(ac._TOOL_MAP, "fake_send", lambda a: self.got.update(a) or "ok")
        monkeypatch.setattr(ac, "_CONTEXT_TOOLS", ac._CONTEXT_TOOLS | {"fake_send"})
        agent = ac.Agent(idle_clear_hours=0)
        replies = iter([{"content": "", "tool_calls": [{"id": "t1", "function": {
            "name": "fake_send", "arguments": "{}"}}]}, {"content": "done"}])
        monkeypatch.setattr(agent, "_call_llm", lambda msgs, user="": next(replies))
        return agent

    def test_quoted_text_is_not_the_users_words(self, agent):
        agent.handle("[引用]\n确认发送\n这是什么", user="u", member="MemberA", said="这是什么")
        assert self.got["__text"] == "这是什么"

    def test_media_turn_has_no_user_words(self, agent, monkeypatch):
        import ocr
        monkeypatch.setattr(ocr, "is_available", lambda: True)
        monkeypatch.setattr(ocr, "ocr_image", lambda p: "确认发送")
        agent.handle_image("x.png", user="u", member="MemberA")
        assert self.got["__text"] == ""

    def test_plain_text_defaults_to_itself(self, agent):
        agent.handle("确认发送", user="u", member="MemberA")
        assert self.got["__text"] == "确认发送"


class TestDraftPreviewAlwaysReachesTheUser:
    def _agent(self, monkeypatch, replies):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setitem(ac._TOOL_MAP, "fake_draft", lambda a: "PREVIEW\n\x01DOC:other/x.pdf")
        monkeypatch.setattr(ac, "_SHOW_TOOLS", ac._SHOW_TOOLS | {"fake_draft"})
        agent = ac.Agent(idle_clear_hours=0)
        it = iter(replies)
        monkeypatch.setattr(agent, "_call_llm", lambda msgs, user="": next(it))
        return agent

    CALL = {"content": "", "tool_calls": [{"id": "t1", "function": {
        "name": "fake_draft", "arguments": "{}"}}]}

    def test_draft_reply_is_a_show_tool(self):
        assert "draft_reply" in ac._SHOW_TOOLS

    def test_preview_appended_even_if_llm_hides_it(self, monkeypatch):
        out = self._agent(monkeypatch, [self.CALL, {"content": "好的"}]).handle(
            "回一下", user="u", member="MemberA")
        assert "PREVIEW" in out

    def test_preview_cannot_smuggle_a_sentinel(self, monkeypatch):
        out = self._agent(monkeypatch, [self.CALL, {"content": "好的"}]).handle(
            "回一下", user="u", member="MemberA")
        assert ac.split_reply(out)[2] == []

    def test_preview_survives_llm_failure(self, monkeypatch):
        out = self._agent(monkeypatch, [self.CALL, None]).handle(
            "回一下", user="u", member="MemberA")
        assert "PREVIEW" in out


class TestToolsRefuseWithoutMailBlock:
    def test_member_without_mail_block_is_refused(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: None)
        assert at.tool_check_mail({"member": "MemberA"}).startswith("[错误]")

    def test_missing_credentials_names_the_env_vars(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: {
            "provider": "gmail", "cred_prefix": "NOPE", "enabled": True})
        out = at.tool_check_mail({"member": "MemberA"})
        assert "NOPE_REFRESH_TOKEN" in out

    def test_disabled_mail_block_is_refused(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": False})
        assert at.tool_check_mail({"member": "MemberA"}).startswith("[错误]")


class TestToolFlow:
    @pytest.fixture(autouse=True)
    def _mail_member(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": True})
        md.drop("MemberA")

    def test_draft_then_confirmed_send(self, stub):
        preview = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "my answer"})
        assert "s@example.com" in preview and "my answer" in preview
        assert not any("/messages/send" in c[1] for c in stub.calls)   # nothing sent yet
        out = at.tool_send_reply({"member": "MemberA", "__turn_at": time.time() + 1,
                                  "__text": "确认发送"})
        assert out.startswith("已发送")
        assert any("/messages/send" in c[1] for c in stub.calls)
        assert md.get("MemberA") is None                               # draft consumed

    def test_send_in_same_turn_does_not_hit_the_api(self, stub):
        turn_at = time.time()
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x"})
        out = at.tool_send_reply({"member": "MemberA", "__turn_at": turn_at,
                                  "__text": "确认发送"})
        assert out.startswith("[错误]")
        assert not any("/messages/send" in c[1] for c in stub.calls)

    def test_llm_cannot_choose_the_recipient(self, stub):
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                             "to": "attacker@evil.example"})
        assert md.get("MemberA")["to"] == "s@example.com"

    def test_failed_send_keeps_the_draft(self, monkeypatch, stub):
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x"})
        monkeypatch.setattr(gp, "send_reply",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        out = at.tool_send_reply({"member": "MemberA", "__turn_at": time.time() + 1,
                                  "__text": "确认"})
        assert out.startswith("[错误]") and md.get("MemberA") is not None

    def test_check_mail_lists_ids(self, stub):
        out = at.tool_check_mail({"member": "MemberA"})
        assert "#m1" in out and "Invoice" in out

    def test_read_mail_returns_body(self, stub):
        assert "hello there" in at.tool_read_mail({"member": "MemberA", "id": "m1"})


# ── 新邮件播报（mail_watch）────────────────────────────────

import mail_watch as mw


def _hist(msgs, history_id="200", token=None):
    """users.history.list response: one record per message added."""
    recs = [{"id": "1", "messagesAdded": [{"message": m}]} for m in msgs]
    out = {"history": recs, "historyId": history_id}
    if token:
        out["nextPageToken"] = token
    return out


def _added(mid, labels=("INBOX", "UNREAD")):
    return {"id": mid, "threadId": f"t-{mid}", "labelIds": list(labels)}


class _WatchStub:
    """Answers /profile, /history and metadata fetches; records urls."""

    def __init__(self, *, profile_id="500", pages=None, history_status=200):
        self.profile_id = profile_id
        self.pages = pages or [_hist([])]
        self.history_status = history_status
        self.urls = []

    def __call__(self, method, url, data=None, headers=None):
        self.urls.append(url)
        if "/profile" in url:
            return 200, json.dumps({"historyId": self.profile_id}).encode()
        if "/history" in url:
            if self.history_status != 200:
                return self.history_status, b'{"error": {"message": "stale"}}'
            page = 0
            if "pageToken=" in url:
                page = int(url.split("pageToken=")[1].split("&")[0].lstrip("p"))
            return 200, json.dumps(self.pages[page]).encode()
        mid = url.split("/messages/")[1].split("?")[0]
        return 200, json.dumps(_msg(mid, subject=f"Subj {mid}",
                                    frm=f"{mid} <{mid}@example.com>")).encode()


class TestProviderHistory:
    def test_history_since_returns_inbox_additions_and_new_id(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _WatchStub(
            pages=[_hist([_added("m2"), _added("m3")], history_id="777")]))
        rows, hid = gp.history_since("100", PREFIX)
        assert [r["id"] for r in rows] == ["m2", "m3"]
        assert hid == "777"

    def test_non_inbox_and_duplicate_additions_are_dropped(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _WatchStub(pages=[_hist(
            [_added("m2"), _added("m2"), _added("m9", labels=("SENT",)),
             _added("m8", labels=("INBOX", "TRASH"))])]))
        rows, _ = gp.history_since("100", PREFIX)
        assert [r["id"] for r in rows] == ["m2"]

    def test_pagination_is_followed(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _WatchStub(pages=[
            _hist([_added("m2")], token="p1"), _hist([_added("m3")], history_id="888")]))
        rows, hid = gp.history_since("100", PREFIX)
        assert [r["id"] for r in rows] == ["m2", "m3"] and hid == "888"

    def test_page_cap_resumes_from_last_record_not_mailbox_head(self, monkeypatch):
        monkeypatch.setattr(gp, "HISTORY_PAGES", 1)
        page = _hist([_added("m2")], history_id="999", token="p1")
        page["history"][0]["id"] = "150"
        monkeypatch.setattr(gp, "_http", _WatchStub(pages=[page, _hist([_added("m3")])]))
        rows, hid = gp.history_since("100", PREFIX)
        assert [r["id"] for r in rows] == ["m2"] and hid == "150"

    def test_metas_skip_deleted_messages_and_keep_order(self, monkeypatch):
        stub = _WatchStub()
        monkeypatch.setattr(gp, "_http", lambda m, url, *a, **k: (
            (404, b"{}") if "/messages/gone" in url else stub(m, url, *a, **k)))
        metas = gp.message_metas(["m2", "gone", "m3"], PREFIX)
        assert [m["id"] for m in metas] == ["m2", "m3"]

    def test_metas_other_errors_still_raise(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", lambda *a, **k: (500, b"{}"))
        with pytest.raises(RuntimeError):
            gp.message_metas(["m2"], PREFIX)

    def test_stale_start_id_reseeds_from_profile(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _WatchStub(profile_id="900", history_status=404))
        rows, hid = gp.history_since("1", PREFIX)
        assert rows is None and hid == "900"

    def test_other_http_errors_still_raise(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _WatchStub(history_status=403))
        with pytest.raises(RuntimeError):
            gp.history_since("1", PREFIX)

    def test_message_meta_has_sender_and_subject_only(self, stub):
        meta = gp.message_meta("m1", PREFIX)
        assert meta["from"] == "Sender <s@example.com>" and meta["subject"] == "Invoice"
        assert "body" not in meta


class TestMailWatch:
    MEMBERS = {"MemberA": {"wechat": ["wx-a"], "telegram": ["tg-a"],
                           "mail": {"provider": "gmail", "cred_prefix": PREFIX,
                                    "enabled": True, "watch": True}},
               "MemberB": {"wechat": ["wx-b"],
                           "mail": {"provider": "gmail", "cred_prefix": PREFIX,
                                    "enabled": True}}}

    @pytest.fixture(autouse=True)
    def _members(self, monkeypatch):
        monkeypatch.setattr(mw._members, "load_members", lambda *a, **k: self.MEMBERS)
        mw.store_path().unlink(missing_ok=True)

    @pytest.fixture
    def sent(self):
        out = []
        return out, lambda cid, text: out.append((cid, text))

    def _provider_for(self, member):
        pref = mw._members.mail_pref(member)
        return ((gp, PREFIX), "") if pref else (None, "[错误] no mail")

    def _run(self, push, stub, monkeypatch, *, channel="wechat", now=None):
        monkeypatch.setattr(gp, "_http", stub)
        return mw.check_and_push(push, channel, provider_for=self._provider_for, now=now)

    def test_first_run_seeds_without_pushing(self, monkeypatch, sent):
        out, push = sent
        n = self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        assert (n, out) == (0, [])
        assert mw._load()["wechat"]["MemberA"]["history_id"] == "500"

    def test_new_mail_is_pushed_with_sender_and_subject(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        n = self._run(push, stub, monkeypatch, now=time.time() + 100)
        assert n == 1 and len(out) == 1
        cid, text = out[0]
        assert cid == "wx-a" and "m2@example.com" in text and "Subj m2" in text
        assert mw._load()["wechat"]["MemberA"]["history_id"] == "600"

    def test_member_without_watch_flag_is_never_polled(self, monkeypatch, sent):
        out, push = sent
        stub = _WatchStub(profile_id="500")
        self._run(push, stub, monkeypatch)
        assert mw._load()["wechat"].get("MemberB") is None

    def test_only_members_bound_to_this_channel_are_polled(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch, channel="telegram")
        self._run(push, _WatchStub(pages=[_hist([_added("m2")])]), monkeypatch,
                  channel="telegram", now=time.time() + 100)
        assert out and out[0][0] == "tg-a"

    def test_each_channel_keeps_its_own_cursor(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch, channel="wechat")
        self._run(push, _WatchStub(profile_id="500"), monkeypatch, channel="telegram")
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        later = time.time() + 100
        self._run(push, stub, monkeypatch, channel="wechat", now=later)
        self._run(push, stub, monkeypatch, channel="telegram", now=later)
        assert sorted(c for c, _ in out) == ["tg-a", "wx-a"]

    def test_burst_is_capped_with_a_tail_count(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        many = [_added(f"m{i}") for i in range(2, 2 + mw.MAX_LINES + 2)]
        self._run(push, _WatchStub(pages=[_hist(many)]), monkeypatch, now=time.time() + 100)
        text = out[0][1]
        assert text.count("Subj m") == mw.MAX_LINES and "2" in text.splitlines()[-1]

    def test_stale_cursor_reseeds_and_pushes_nothing(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        n = self._run(push, _WatchStub(profile_id="900", history_status=404), monkeypatch,
                      now=time.time() + 100)
        assert (n, out) == (0, [])
        assert mw._load()["wechat"]["MemberA"]["history_id"] == "900"

    def test_push_failure_keeps_cursor_for_retry(self, monkeypatch, sent):
        def boom(cid, text):
            raise RuntimeError("channel down")

        self._run(boom, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        self._run(boom, stub, monkeypatch, now=time.time() + 100)
        assert mw._load()["wechat"]["MemberA"]["history_id"] == "500"

    def test_throttle_skips_polls_inside_the_window(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("m2")])])
        n = self._run(push, stub, monkeypatch)          # same instant → throttled
        assert (n, out) == (0, [])
        assert not any("/history" in u for u in stub.urls)

    def test_api_failure_does_not_move_the_cursor(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        n = self._run(push, _WatchStub(history_status=500), monkeypatch,
                      now=time.time() + 100)
        assert (n, out) == (0, [])
        assert mw._load()["wechat"]["MemberA"]["history_id"] == "500"

    def test_watch_tick_is_registered_on_fast_ticks(self):
        assert any(getattr(f, "__name__", "") == "_mail_watch_tick"
                   for f in ac.REGISTRY.fast_ticks)


# ── 播报过滤规则（mail_rules）──────────────────────────────

import mail_rules as mr


def _meta(frm="Shop <deals@shop.example>", subject="50% off", labels=("INBOX",)):
    return {"id": "x", "from": frm, "subject": subject, "labels": list(labels)}


class TestMailRules:
    @pytest.fixture(autouse=True)
    def _members(self, monkeypatch):
        monkeypatch.setattr(mr._members, "load_members",
                            lambda *a, **k: {"MemberA": {"dir": "membera"}})
        mr.store_path("MemberA").unlink(missing_ok=True)

    def test_added_rule_is_persisted_and_listed(self):
        mr.add("MemberA", kind="sender", value="deals@shop.example", note="广告")
        rules = mr.load("MemberA")
        assert (rules[0]["kind"], rules[0]["value"]) == ("sender", "deals@shop.example")
        assert rules[0]["note"] == "广告"

    def test_same_rule_twice_is_not_duplicated(self):
        mr.add("MemberA", kind="sender", value="deals@shop.example")
        mr.add("MemberA", kind="sender", value="DEALS@shop.example")
        assert len(mr.load("MemberA")) == 1

    def test_unknown_kind_is_refused(self):
        with pytest.raises(ValueError):
            mr.add("MemberA", kind="mood", value="x")

    def test_remove_by_index(self):
        mr.add("MemberA", kind="sender", value="a@x.example")
        mr.add("MemberA", kind="subject", value="newsletter")
        gone = mr.remove("MemberA", 1)
        assert gone["value"] == "a@x.example"
        assert [r["value"] for r in mr.load("MemberA")] == ["newsletter"]
        assert mr.remove("MemberA", 9) is None

    def test_rules_are_per_member(self):
        mr.add("MemberA", kind="sender", value="a@x.example")
        assert mr.load("MemberB") == []

    def test_sender_match_ignores_display_name_and_case(self):
        rules = [{"kind": "sender", "value": "Deals@Shop.Example"}]
        assert mr.match(_meta(), rules)
        assert mr.match(_meta(frm="other@shop.example"), rules) is None

    def test_domain_match_covers_whole_domain(self):
        rules = [{"kind": "domain", "value": "shop.example"}]
        assert mr.match(_meta(frm="anyone@shop.example"), rules)
        assert mr.match(_meta(frm="x@notshop.example"), rules) is None

    def test_subject_match_is_case_insensitive_substring(self):
        rules = [{"kind": "subject", "value": "OFF"}]
        assert mr.match(_meta(subject="Weekend 50% off!"), rules)
        assert mr.match(_meta(subject="Invoice"), rules) is None

    def test_label_match_uses_gmail_categories(self):
        rules = [{"kind": "label", "value": "category_promotions"}]
        assert mr.match(_meta(labels=("INBOX", "CATEGORY_PROMOTIONS")), rules)
        assert mr.match(_meta(labels=("INBOX",)), rules) is None

    def test_no_rules_matches_nothing(self):
        assert mr.match(_meta(), []) is None


class TestMailWatchFiltering:
    MEMBERS = {"MemberA": {"wechat": ["wx-a"], "dir": "membera",
                           "mail": {"provider": "gmail", "cred_prefix": PREFIX,
                                    "enabled": True, "watch": True}}}

    @pytest.fixture(autouse=True)
    def _members(self, monkeypatch):
        monkeypatch.setattr(mw._members, "load_members", lambda *a, **k: self.MEMBERS)
        monkeypatch.setattr(mr._members, "load_members", lambda *a, **k: self.MEMBERS)
        mw.store_path().unlink(missing_ok=True)
        mw.last_push_path().unlink(missing_ok=True)
        mr.store_path("MemberA").unlink(missing_ok=True)

    @pytest.fixture
    def sent(self):
        out = []
        return out, lambda cid, text: out.append((cid, text))

    def _provider_for(self, member):
        return ((gp, PREFIX), "")

    def _seed(self, push, monkeypatch):
        monkeypatch.setattr(gp, "_http", _WatchStub(profile_id="500"))
        mw.check_and_push(push, "wechat", provider_for=self._provider_for)

    def _poll(self, push, stub, monkeypatch):
        monkeypatch.setattr(gp, "_http", stub)
        return mw.check_and_push(push, "wechat", provider_for=self._provider_for,
                                 now=time.time() + 100)

    def test_unmuted_mail_still_pushes(self, monkeypatch, sent):
        out, push = sent
        self._seed(push, monkeypatch)
        n = self._poll(push, _WatchStub(pages=[_hist([_added("m2")])]), monkeypatch)
        assert (n, len(out)) == (1, 1)

    def test_muted_sender_is_dropped_but_cursor_advances(self, monkeypatch, sent):
        out, push = sent
        self._seed(push, monkeypatch)
        mr.add("MemberA", kind="sender", value="m2@example.com")
        n = self._poll(push, _WatchStub(pages=[_hist([_added("m2")], history_id="600")]),
                       monkeypatch)
        assert (n, out) == (0, [])
        assert mw._load()["wechat"]["MemberA"]["history_id"] == "600"

    def test_only_unmuted_mail_of_a_batch_is_pushed(self, monkeypatch, sent):
        out, push = sent
        self._seed(push, monkeypatch)
        mr.add("MemberA", kind="sender", value="m2@example.com")
        self._poll(push, _WatchStub(pages=[_hist([_added("m2"), _added("m3")])]), monkeypatch)
        text = out[0][1]
        assert "Subj m3" in text and "Subj m2" not in text
        assert "1" in text.splitlines()[0]

    def test_label_rule_drops_mail_without_fetching_it(self, monkeypatch, sent):
        out, push = sent
        self._seed(push, monkeypatch)
        mr.add("MemberA", kind="label", value="CATEGORY_PROMOTIONS")
        stub = _WatchStub(pages=[_hist([_added("m2", labels=("INBOX", "CATEGORY_PROMOTIONS"))])])
        n = self._poll(push, stub, monkeypatch)
        assert (n, out) == (0, [])
        assert not any("/messages/" in u for u in stub.urls)

    def test_pushed_items_are_recorded_for_later_feedback(self, monkeypatch, sent):
        out, push = sent
        self._seed(push, monkeypatch)
        self._poll(push, _WatchStub(pages=[_hist([_added("m2")])]), monkeypatch)
        items = mw.last_push("MemberA")
        assert [i["id"] for i in items] == ["m2"]
        assert items[0]["from"] == "m2 <m2@example.com>"


class TestMuteTools:
    @pytest.fixture(autouse=True)
    def _mail_member(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m, *a, **k: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": True, "watch": True})
        monkeypatch.setattr(mr._members, "load_members",
                            lambda *a, **k: {"MemberA": {"dir": "membera"}})
        mr.store_path("MemberA").unlink(missing_ok=True)
        mw.last_push_path().unlink(missing_ok=True)

    def test_tools_are_registered_member_locked(self):
        names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
        for t in ("mail_last_push", "mail_mute", "mail_rules"):
            assert t in names and t in ac._MEMBER_LOCKED
        assert "mail_last_push" in ac._UNTRUSTED_TOOLS

    def test_last_push_lists_what_was_broadcast(self):
        mw.record_last_push("MemberA", [{"id": "m2", "from": "Shop <d@shop.example>",
                                         "subject": "50% off", "labels": ["INBOX"]}])
        out = at.tool_mail_last_push({"member": "MemberA"})
        assert "d@shop.example" in out and "50% off" in out

    def test_last_push_empty_says_so(self):
        assert "没有" in at.tool_mail_last_push({"member": "MemberA"})

    def test_mute_by_sender_persists_rule(self):
        out = at.tool_mail_mute({"member": "MemberA", "sender": "d@shop.example",
                                 "note": "广告"})
        assert "d@shop.example" in out
        assert mr.load("MemberA")[0]["kind"] == "sender"

    def test_mute_needs_one_criterion(self):
        assert at.tool_mail_mute({"member": "MemberA"}).startswith("[错误]")

    def test_mute_by_label_normalises_promotions(self):
        at.tool_mail_mute({"member": "MemberA", "label": "promotions"})
        assert mr.load("MemberA")[0]["value"] == "CATEGORY_PROMOTIONS"

    def test_rules_tool_lists_and_removes(self):
        at.tool_mail_mute({"member": "MemberA", "sender": "d@shop.example"})
        assert "d@shop.example" in at.tool_mail_rules({"member": "MemberA"})
        out = at.tool_mail_rules({"member": "MemberA", "remove": 1})
        assert "d@shop.example" in out and mr.load("MemberA") == []

    def test_rules_tool_on_empty_list(self):
        assert "没有" in at.tool_mail_rules({"member": "MemberA"})
