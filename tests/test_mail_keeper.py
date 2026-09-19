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
import paths as _paths


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


class _AttachStub(_Stub):
    """_Stub + attachments 端点（attachment_id → 原始字节）。"""

    def __init__(self, messages, blobs):
        super().__init__(messages)
        self.blobs = blobs

    def __call__(self, method, url, data=None, headers=None):
        if "/attachments/" in url:
            self.calls.append((method, url, None))
            aid = url.split("/attachments/")[1].split("?")[0]
            body = base64.urlsafe_b64encode(self.blobs[aid]).decode().rstrip("=")
            return 200, json.dumps({"size": len(self.blobs[aid]), "data": body}).encode()
        return super().__call__(method, url, data, headers)


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
        assert gp.send_mail(gp.reply_recipient(msg), gp.reply_subject(msg["subject"]),
                            "my answer", PREFIX, in_reply_to=msg["message_id"],
                            references=msg["references"],
                            thread_id=msg["thread_id"]) == "sent-1"
        method, url, payload = stub.calls[-1]
        assert (method, "/messages/send" in url) == ("POST", True)
        assert payload["threadId"] == "t-m1"
        raw = base64.urlsafe_b64decode(payload["raw"] + "==").decode()
        assert "To: s@example.com" in raw
        assert "Subject: Re: Invoice" in raw
        assert "In-Reply-To: <m1@example.com>" in raw
        assert "my answer" in raw

    def test_new_mail_has_no_thread_id(self, stub):
        assert gp.send_mail("who@example.com", "Hi", "text", PREFIX) == "sent-1"
        payload = stub.calls[-1][2]
        assert "threadId" not in payload
        raw = base64.urlsafe_b64decode(payload["raw"] + "==").decode()
        assert "To: who@example.com" in raw and "In-Reply-To" not in raw

    def test_attachments_ride_as_mime_parts(self, stub, tmp_path):
        pdf = tmp_path / "bill.pdf"
        pdf.write_bytes(b"%PDF-1.4 xyz")
        png = tmp_path / "shot.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nzzz")
        gp.send_mail("who@example.com", "Hi", "text", PREFIX, attachments=[pdf, png])
        raw = base64.urlsafe_b64decode(stub.calls[-1][2]["raw"] + "==").decode()
        assert "multipart/mixed" in raw
        assert "application/pdf" in raw and 'filename="bill.pdf"' in raw
        assert "image/png" in raw and 'filename="shot.png"' in raw
        assert base64.b64encode(b"%PDF-1.4 xyz").decode() in raw

    def test_unknown_extension_falls_back_to_octet_stream(self, stub, tmp_path):
        f = tmp_path / "notes.whatever"
        f.write_bytes(b"data")
        gp.send_mail("who@example.com", "Hi", "text", PREFIX, attachments=[f])
        raw = base64.urlsafe_b64decode(stub.calls[-1][2]["raw"] + "==").decode()
        assert "application/octet-stream" in raw


class TestDraftGate:
    """Send is gated by code, not by the LLM's good behaviour."""

    def _put(self, member="MemberA", *, age_s=0.0, turn_id=7, user="u1"):
        return md.put(member, {"to": "s@example.com", "subject": "Re: Invoice",
                               "body": "ok"}, turn_id=turn_id, user=user,
                      now=time.time() - age_s)

    def _check(self, member="MemberA", *, turn_id=8, user="u1", text="确认发送"):
        return md.check(member, turn_id=turn_id, user=user, text=text)

    def test_no_draft_refuses(self):
        md.drop("MemberA")
        draft, why = self._check()
        assert draft is None and "没有待发送" in why

    def test_same_turn_send_refused(self):
        self._put()                                  # drafted during turn 7
        draft, why = self._check(turn_id=7)
        assert draft is None and "不是上一轮" in why

    def test_stale_draft_from_an_earlier_turn_refused(self):
        """An "确认" two turns later answers something else — not this preview."""
        self._put(age_s=5)                           # drafted during turn 7
        draft, why = self._check(turn_id=9)
        assert draft is None and "不是上一轮" in why

    def test_another_users_turn_cannot_confirm(self):
        self._put(age_s=5, user="u1")
        draft, why = self._check(user="u2")
        assert draft is None and "不是上一轮" in why

    def test_missing_turn_context_refuses(self):
        """Injection failing open would send; it must fail closed."""
        self._put(age_s=5)
        draft, why = self._check(turn_id=0, user="")
        assert draft is None and "不是上一轮" in why

    def test_next_turn_with_confirmation_passes(self):
        self._put(age_s=5)
        draft, why = self._check()
        assert why == "" and draft["to"] == "s@example.com"

    def test_next_turn_without_confirmation_refused(self):
        self._put(age_s=5)
        draft, why = self._check(text="那封账单多少钱？")
        assert draft is None and "没有明确确认" in why

    @pytest.mark.parametrize("text", [
        "不行，先别发送", "帮我看下银行那封", "不确定", "不同意", "don't send", "token",
        "ok", "好的谢谢", "确认一下内容再说"])
    def test_negations_substrings_and_bare_acks_are_not_consent(self, text):
        self._put(age_s=5)
        draft, why = self._check(text=text)
        assert draft is None and "没有明确确认" in why

    @pytest.mark.parametrize("text", ["确认", "好的，发送吧", "可以发", "OK, send it!"])
    def test_whole_sentence_confirmations_pass(self, text):
        self._put(age_s=5)
        assert self._check(text=text)[1] == ""

    def test_expired_draft_refused_and_dropped(self):
        self._put(age_s=md.TTL_S + 1)
        draft, why = self._check()
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
        for t in ("check_mail", "read_mail", "draft_reply", "compose_mail", "send_draft"):
            assert t in names and t in ac._TOOL_MAP
            assert t in ac._MEMBER_LOCKED
        assert {"check_mail", "read_mail", "draft_reply",
                "compose_mail"} <= ac._UNTRUSTED_TOOLS

    def test_member_cannot_be_overridden_by_llm(self):
        out = ac._apply_member("check_mail", {"member": "Other"}, "MemberA")
        assert out["member"] == "MemberA"

    @pytest.mark.parametrize("tool", ["draft_reply", "compose_mail", "send_draft"])
    def test_draft_tools_get_turn_context_injected(self, tool):
        out = ac._apply_context(tool, {}, "wechat", "u1", "MemberA",
                                turn_id=7, text="确认发送")
        assert out["__turn_id"] == 7 and out["__user"] == "u1"
        assert out["__text"] == "确认发送"

    def test_other_tools_get_no_turn_context(self):
        out = ac._apply_context("check_mail", {}, "wechat", "u1", "MemberA",
                                turn_id=7, text="x")
        assert "__turn_id" not in out and "__text" not in out

    def test_each_user_message_is_a_new_turn(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        agent = ac.Agent(idle_clear_hours=0)
        monkeypatch.setattr(agent, "_call_llm", lambda msgs, user="": {"content": "ok"})
        agent.handle("一", user="u", member="MemberA")
        agent.handle("二", user="u", member="MemberA")
        assert agent._turn_seq["u"] == 2 and agent._turn_seq.get("other") is None


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

    def test_media_turn_counts_only_typed_words(self, agent, monkeypatch):
        import ocr
        monkeypatch.setattr(ocr, "is_available", lambda: True)
        monkeypatch.setattr(ocr, "ocr_image", lambda p: "确认发送")
        agent.handle_media(["x.png"], "这是啥", user="u", member="MemberA")
        assert self.got["__text"] == "这是啥"

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


# agent_core._apply_context 注入的本轮身份：起草一轮，确认必须落在紧接着的下一轮
TURN = {"__turn_id": 3, "__user": "u1"}
NEXT_TURN = {"__turn_id": 4, "__user": "u1", "__text": "确认发送"}


class TestToolFlow:
    @pytest.fixture(autouse=True)
    def _mail_member(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": True})
        md.drop("MemberA")

    def test_draft_then_confirmed_send(self, stub):
        preview = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "my answer",
                                       **TURN})
        assert "s@example.com" in preview and "my answer" in preview
        assert not any("/messages/send" in c[1] for c in stub.calls)   # nothing sent yet
        out = at.tool_send_draft({"member": "MemberA", **NEXT_TURN})
        assert out.startswith("已发送")
        assert any("/messages/send" in c[1] for c in stub.calls)
        assert md.get("MemberA") is None                               # draft consumed

    def test_send_in_same_turn_does_not_hit_the_api(self, stub):
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x", **TURN})
        out = at.tool_send_draft({"member": "MemberA", **TURN, "__text": "确认发送"})
        assert out.startswith("[错误]")
        assert not any("/messages/send" in c[1] for c in stub.calls)

    def test_confirmation_two_turns_later_does_not_hit_the_api(self, stub):
        """"确认" answering some later question must not fire a stale draft."""
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x", **TURN})
        out = at.tool_send_draft({"member": "MemberA", "__turn_id": TURN["__turn_id"] + 2,
                                  "__user": "u1", "__text": "确认发送"})
        assert out.startswith("[错误]") and md.get("MemberA") is not None
        assert not any("/messages/send" in c[1] for c in stub.calls)

    def test_llm_cannot_choose_the_recipient(self, stub):
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                             "to": "attacker@evil.example", **TURN})
        assert md.get("MemberA")["to"] == "s@example.com"

    def test_failed_send_keeps_the_draft(self, monkeypatch, stub):
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x", **TURN})
        monkeypatch.setattr(gp, "send_mail",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        out = at.tool_send_draft({"member": "MemberA", **NEXT_TURN, "__text": "确认"})
        assert out.startswith("[错误]") and md.get("MemberA") is not None

    def test_check_mail_lists_ids(self, stub):
        out = at.tool_check_mail({"member": "MemberA"})
        assert "#m1" in out and "Invoice" in out

    def test_read_mail_returns_body(self, stub):
        assert "hello there" in at.tool_read_mail({"member": "MemberA", "id": "m1"})


def _sent_raw(stub):
    payload = next(c[2] for c in reversed(stub.calls) if "/messages/send" in c[1])
    return base64.urlsafe_b64decode(payload["raw"] + "=="), payload


class TestOutboundAttachments:
    """附件闸门：只能发家庭共享或本成员的现存文件，起草和发送各查一次。"""

    @pytest.fixture(autouse=True)
    def _mail_member(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": True})
        md.drop("MemberA")

    @pytest.fixture
    def files(self):
        fam = _paths.family_dir() / "documents" / "lease"
        fam.mkdir(parents=True, exist_ok=True)
        lease = fam / "lease.pdf"
        lease.write_bytes(b"%PDF-1.4 lease")
        mine = _paths.member_inbox_dir("MemberA")
        shot = mine / "shot.png"
        shot.write_bytes(b"\x89PNG shot")
        theirs = _paths.member_inbox_dir("MemberB")
        secret = theirs / "secret.png"
        secret.write_bytes(b"\x89PNG secret")
        comma = fam / "租约, 2024.pdf"          # 落盘时逗号不会被洗掉
        comma.write_bytes(b"%PDF-1.4 comma")
        return {k: _paths.to_rel(v) for k, v in
                (("lease", lease), ("shot", shot), ("secret", secret), ("comma", comma))}

    def test_reply_carries_family_and_own_files(self, stub, files):
        preview = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "see attached",
                                       "attachments": f"{files['lease']}\n{files['shot']}",
                                       **TURN})
        assert files["lease"] in preview and files["shot"] in preview
        assert md.get("MemberA")["attachments"] == [files["lease"], files["shot"]]
        out = at.tool_send_draft({"member": "MemberA", **NEXT_TURN})
        assert out.startswith("已发送") and "附件 2 个" in out
        raw, payload = _sent_raw(stub)
        assert payload["threadId"] == "t-m1"
        assert b'filename="lease.pdf"' in raw and b'filename="shot.png"' in raw
        assert base64.b64encode(b"%PDF-1.4 lease") in raw

    def test_filename_with_a_comma_is_one_attachment(self, stub, files):
        """逗号是合法文件名字符：按逗号切会把一个附件切成两条发不出去的路径。"""
        preview = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                                       "attachments": files["comma"], **TURN})
        assert md.get("MemberA")["attachments"] == [files["comma"]]
        assert files["comma"] in preview

    def test_other_members_file_is_refused_and_nothing_is_drafted(self, stub, files):
        out = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                                   "attachments": files["secret"], **TURN})
        assert out.startswith("[错误]") and md.get("MemberA") is None

    def test_missing_path_is_refused(self, stub, files):
        out = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                                   "attachments": "Family/documents/lease/nope.pdf", **TURN})
        assert out.startswith("[错误]") and md.get("MemberA") is None

    def test_too_many_attachments_refused(self, stub, files):
        many = "\n".join([files["lease"]] * (at.SEND_ATTACH_MAX_N + 1))
        out = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                                   "attachments": many, **TURN})
        assert out.startswith("[错误]") and str(at.SEND_ATTACH_MAX_N) in out

    def test_total_size_cap(self, stub, files, monkeypatch):
        monkeypatch.setattr(at, "SEND_ATTACH_MAX_BYTES", 4)
        out = at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                                   "attachments": files["lease"], **TURN})
        assert out.startswith("[错误]") and md.get("MemberA") is None

    def test_file_vanishing_after_draft_blocks_send_and_keeps_draft(self, stub, files):
        at.tool_draft_reply({"member": "MemberA", "id": "m1", "body": "x",
                             "attachments": files["shot"], **TURN})
        _paths.resolve_rel(files["shot"]).unlink()
        out = at.tool_send_draft({"member": "MemberA", **NEXT_TURN})
        assert out.startswith("[错误]") and md.get("MemberA") is not None
        assert not any("/messages/send" in c[1] for c in stub.calls)


class TestComposeNewMail:
    @pytest.fixture(autouse=True)
    def _mail_member(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": True})
        md.drop("MemberA")

    def test_compose_then_confirmed_send(self, stub):
        preview = at.tool_compose_mail({"member": "MemberA", "to": "Teacher <t@school.example>",
                                        "subject": "请假", "body": "明天请假一天", **TURN})
        assert "t@school.example" in preview and "请假" in preview
        assert not any("/messages/send" in c[1] for c in stub.calls)
        out = at.tool_send_draft({"member": "MemberA", **NEXT_TURN})
        assert out.startswith("已发送")
        raw, payload = _sent_raw(stub)
        assert "threadId" not in payload
        assert b"To: t@school.example" in raw

    def test_same_turn_send_refused(self, stub):
        at.tool_compose_mail({"member": "MemberA", "to": "t@school.example",
                              "subject": "s", "body": "b", **TURN})
        out = at.tool_send_draft({"member": "MemberA", **TURN, "__text": "确认发送"})
        assert out.startswith("[错误]")
        assert not any("/messages/send" in c[1] for c in stub.calls)

    @pytest.mark.parametrize("to", ["", "not-an-address", "a@b", "a@b.c, c@d.e", "a b@c.de"])
    def test_bad_recipient_refused(self, stub, to):
        out = at.tool_compose_mail({"member": "MemberA", "to": to,
                                    "subject": "s", "body": "b", **TURN})
        assert out.startswith("[错误]") and md.get("MemberA") is None

    def test_subject_and_body_required(self, stub):
        assert at.tool_compose_mail({"member": "MemberA", "to": "t@school.example",
                                     "subject": "", "body": "b", **TURN}).startswith("[错误]")
        assert at.tool_compose_mail({"member": "MemberA", "to": "t@school.example",
                                     "subject": "s", "body": "", **TURN}).startswith("[错误]")


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
        for ch in ("wechat", "telegram"):
            mw.store_path(ch).unlink(missing_ok=True)
        mw._polled.clear()

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
        assert mw._load("wechat")["MemberA"]["history_id"] == "500"

    def test_new_mail_is_pushed_with_sender_and_subject(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        n = self._run(push, stub, monkeypatch, now=time.time() + 100)
        assert n == 1 and len(out) == 1
        cid, text = out[0]
        assert cid == "wx-a" and "m2@example.com" in text and "Subj m2" in text
        assert mw._load("wechat")["MemberA"]["history_id"] == "600"

    def test_member_without_watch_flag_is_never_polled(self, monkeypatch, sent):
        out, push = sent
        stub = _WatchStub(profile_id="500")
        self._run(push, stub, monkeypatch)
        assert mw._load("wechat").get("MemberB") is None

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
        assert mw._load("wechat")["MemberA"]["history_id"] == "900"

    def test_push_failure_keeps_cursor_for_retry(self, monkeypatch, sent):
        def boom(cid, text):
            raise RuntimeError("channel down")

        self._run(boom, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        self._run(boom, stub, monkeypatch, now=time.time() + 100)
        assert mw._load("wechat")["MemberA"]["history_id"] == "500"

    def test_push_returning_false_keeps_cursor_for_retry(self, monkeypatch, sent):
        """Telegram swallows send errors and returns False instead of raising."""
        self._run(lambda c, t: False, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        n = self._run(lambda c, t: False, stub, monkeypatch, now=time.time() + 100)
        assert n == 0 and mw._load("wechat")["MemberA"]["history_id"] == "500"

    def test_deleted_mail_does_not_wedge_the_watch(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        stub = _WatchStub(pages=[_hist([_added("gone"), _added("m3")], history_id="600")])
        monkeypatch.setattr(gp, "_http", lambda m, url, *a, **k: (
            (404, b"{}") if "/messages/gone" in url else stub(m, url, *a, **k)))
        n = mw.check_and_push(push, "wechat", provider_for=self._provider_for,
                              now=time.time() + 100)
        assert n == 1 and "Subj m3" in out[0][1]
        assert mw._load("wechat")["MemberA"]["history_id"] == "600"

    def test_channels_do_not_share_a_state_file(self):
        assert mw.store_path("wechat") != mw.store_path("telegram")

    def test_unchanged_cursor_is_not_rewritten(self, monkeypatch, sent):
        out, push = sent
        self._run(push, _WatchStub(profile_id="500"), monkeypatch)
        saved = []
        monkeypatch.setattr(mw, "_save", lambda *a: saved.append(a))
        self._run(push, _WatchStub(pages=[_hist([], history_id="500")]), monkeypatch,
                  now=time.time() + 100)
        assert saved == []

    def test_same_mail_on_two_channels_is_recorded_once(self, monkeypatch, sent):
        out, push = sent
        mw.last_push_path().unlink(missing_ok=True)
        for ch in ("wechat", "telegram"):
            self._run(push, _WatchStub(profile_id="500"), monkeypatch, channel=ch)
        stub = _WatchStub(pages=[_hist([_added("m2")], history_id="600")])
        for ch in ("wechat", "telegram"):
            self._run(push, stub, monkeypatch, channel=ch, now=time.time() + 100)
        assert [i["id"] for i in mw.last_push("MemberA")] == ["m2"]

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
        assert mw._load("wechat")["MemberA"]["history_id"] == "500"

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

    def test_domain_match_covers_subdomains(self):
        rules = [{"kind": "domain", "value": "shop.example"}]
        assert mr.match(_meta(frm="news@mail.shop.example"), rules)
        assert mr.match(_meta(frm="x@shop.example.evil.test"), rules) is None

    @pytest.mark.parametrize("raw", ["@Shop.Example", "deals@shop.example",
                                     "https://shop.example/unsubscribe"])
    def test_domain_value_is_reduced_to_the_host(self, raw):
        assert mr.normalise("domain", raw) == ("domain", "shop.example")

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
        for ch in ("wechat", "telegram"):
            mw.store_path(ch).unlink(missing_ok=True)
        mw._polled.clear()
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
        assert mw._load("wechat")["MemberA"]["history_id"] == "600"

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


class TestAttachments:
    """附件：信里列出 → 用户要了才下载 → 只落 data/<成员>/inbox/ 内。"""

    def _msg_with_pdf(self, *, filename="bill.pdf", mime="application/pdf", size=1234):
        m = _msg()
        m["payload"] = {"mimeType": "multipart/mixed", "headers": m["payload"]["headers"],
                        "parts": [{"mimeType": "text/plain", "body": {"data": _b64("see attached")}},
                                  {"mimeType": mime, "filename": filename,
                                   "body": {"attachmentId": "a1", "size": size}}]}
        return m

    @pytest.fixture
    def pdf_stub(self, monkeypatch):
        s = _AttachStub([self._msg_with_pdf()], {"a1": b"%PDF-bytes"})
        monkeypatch.setattr(gp, "_http", s)
        monkeypatch.setattr(at, "_provider", lambda member: ((gp, PREFIX), ""))
        return s

    def test_get_message_lists_attachments(self, pdf_stub):
        att = gp.get_message("m1", PREFIX)["attachments"]
        assert att == [{"filename": "bill.pdf", "mime": "application/pdf",
                        "size": 1234, "attachment_id": "a1"}]

    def test_body_ignores_attachment_parts(self, pdf_stub):
        assert gp.get_message("m1", PREFIX)["body"] == "see attached"

    def test_get_attachment_decodes_base64url(self, pdf_stub):
        assert gp.get_attachment("m1", "a1", PREFIX) == b"%PDF-bytes"

    def test_read_mail_shows_attachment_list(self, pdf_stub):
        out = at.tool_read_mail({"member": "MemberA", "id": "m1"})
        assert "bill.pdf" in out and "附件" in out

    def test_download_writes_into_member_inbox(self, pdf_stub):
        out = at.tool_download_attachment({"member": "MemberA", "id": "m1",
                                           "filename": "bill.pdf"})
        rel = out.splitlines()[0].strip()
        p = _paths.data_root() / rel
        assert p.read_bytes() == b"%PDF-bytes"
        assert p.parent == _paths.member_inbox_dir("MemberA")

    def test_download_defaults_to_the_only_attachment(self, pdf_stub):
        out = at.tool_download_attachment({"member": "MemberA", "id": "m1"})
        assert (_paths.data_root() / out.splitlines()[0].strip()).exists()

    def test_second_download_does_not_overwrite(self, pdf_stub):
        first = at.tool_download_attachment({"member": "MemberA", "id": "m1"}).splitlines()[0]
        second = at.tool_download_attachment({"member": "MemberA", "id": "m1"}).splitlines()[0]
        assert first != second
        assert (_paths.data_root() / second.strip()).exists()

    def test_traversal_filename_stays_in_inbox(self, monkeypatch):
        s = _AttachStub([self._msg_with_pdf(filename="../../evil.pdf")], {"a1": b"%PDF"})
        monkeypatch.setattr(gp, "_http", s)
        monkeypatch.setattr(at, "_provider", lambda member: ((gp, PREFIX), ""))
        out = at.tool_download_attachment({"member": "MemberA", "id": "m1"})
        p = _paths.data_root() / out.splitlines()[0].strip()
        assert p.parent == _paths.member_inbox_dir("MemberA")

    def test_refuses_non_image_pdf(self, monkeypatch):
        s = _AttachStub([self._msg_with_pdf(filename="setup.exe",
                                            mime="application/octet-stream")],
                        {"a1": b"MZ"})
        monkeypatch.setattr(gp, "_http", s)
        monkeypatch.setattr(at, "_provider", lambda member: ((gp, PREFIX), ""))
        out = at.tool_download_attachment({"member": "MemberA", "id": "m1",
                                          "filename": "setup.exe"})
        assert out.startswith("[错误]") and "setup.exe" in out
        assert not any("/attachments/" in c[1] for c in s.calls)

    def test_refuses_oversize(self, monkeypatch):
        s = _AttachStub([self._msg_with_pdf(size=at.ATTACH_MAX_BYTES + 1)], {"a1": b"%PDF"})
        monkeypatch.setattr(gp, "_http", s)
        monkeypatch.setattr(at, "_provider", lambda member: ((gp, PREFIX), ""))
        out = at.tool_download_attachment({"member": "MemberA", "id": "m1"})
        assert out.startswith("[错误]")
        assert not any("/attachments/" in c[1] for c in s.calls)

    def test_no_attachment_says_so(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _AttachStub([_msg()], {}))
        monkeypatch.setattr(at, "_provider", lambda member: ((gp, PREFIX), ""))
        out = at.tool_download_attachment({"member": "MemberA", "id": "m1"})
        assert out.startswith("[错误]") and "附件" in out

    def test_tool_registered_member_locked_and_untrusted(self):
        names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
        assert "download_attachment" in names
        assert "download_attachment" in ac._TOOL_MAP
        assert "download_attachment" in ac._MEMBER_LOCKED


class _FilterStub:
    """Gmail labels / filters / messages.list / batchModify endpoints."""

    def __init__(self, labels=(), filters=(), matches=(), scope_ok=True):
        self.labels = [{"id": f"L{i}", "name": n, "type": "user"} for i, n in enumerate(labels)]
        self.filters = list(filters)
        self.matches = list(matches)
        self.scope_ok = scope_ok
        self.calls = []

    def __call__(self, method, url, data=None, headers=None):
        payload = json.loads(data) if data else None
        self.calls.append((method, url, payload))
        if not self.scope_ok and method != "GET":
            return 403, b'{"error":{"message":"Request had insufficient authentication scopes."}}'
        path = url.split("/users/me")[1].split("?")[0]
        if path == "/labels" and method == "GET":
            return 200, json.dumps({"labels": self.labels}).encode()
        if path == "/labels":
            lb = {"id": f"L{len(self.labels)}", "name": payload["name"], "type": "user"}
            self.labels.append(lb)
            return 200, json.dumps(lb).encode()
        if path == "/settings/filters" and method == "GET":
            return 200, json.dumps({"filter": self.filters}).encode()
        if path == "/settings/filters":
            f = {"id": f"F{len(self.filters)}", **payload}
            self.filters.append(f)
            return 200, json.dumps(f).encode()
        if path.startswith("/settings/filters/") and method == "DELETE":
            fid = path.rsplit("/", 1)[1]
            self.filters = [f for f in self.filters if f["id"] != fid]
            return 204, b""
        if path == "/messages":
            return 200, json.dumps({"messages": [{"id": m} for m in self.matches],
                                    "resultSizeEstimate": len(self.matches)}).encode()
        if path == "/messages/batchModify":
            return 204, b""
        raise AssertionError(f"unexpected {method} {url}")

    def hit(self, method, path):
        return [c for c in self.calls if c[0] == method and c[1].split("?")[0].endswith(path)]


class TestFilterProvider:
    def test_ensure_label_reuses_existing_case_insensitively(self, monkeypatch):
        s = _FilterStub(labels=["School"])
        monkeypatch.setattr(gp, "_http", s)
        assert gp.ensure_label("school", PREFIX) == "L0"
        assert not s.hit("POST", "/labels")

    def test_nested_label_creates_parent_first(self, monkeypatch):
        s = _FilterStub()
        monkeypatch.setattr(gp, "_http", s)
        gp.ensure_label(" Family / Bills ", PREFIX)
        assert [c[2]["name"] for c in s.hit("POST", "/labels")] == ["Family", "Family/Bills"]

    def test_create_filter_labels_and_skips_inbox(self, monkeypatch):
        s = _FilterStub()
        monkeypatch.setattr(gp, "_http", s)
        gp.create_filter({"from": "school.example", "to": "", "bogus": "x"}, "L9", True, PREFIX)
        body = s.hit("POST", "/settings/filters")[0][2]
        assert body == {"criteria": {"from": "school.example"},
                        "action": {"addLabelIds": ["L9"], "removeLabelIds": ["INBOX"]}}

    def test_keep_in_inbox_omits_remove(self, monkeypatch):
        s = _FilterStub()
        monkeypatch.setattr(gp, "_http", s)
        gp.create_filter({"subject": "bill"}, "L9", False, PREFIX)
        assert "removeLabelIds" not in s.hit("POST", "/settings/filters")[0][2]["action"]

    def test_criteria_query_quotes_spaces(self):
        q = gp.criteria_query({"from": "a@b.com", "subject": "report card",
                               "query": "has:attachment"})
        assert q == 'from:a@b.com subject:"report card" (has:attachment)'

    def test_move_matching_batch_modifies(self, monkeypatch):
        s = _FilterStub(matches=["m1", "m2"])
        monkeypatch.setattr(gp, "_http", s)
        assert gp.move_matching("from:x", "L1", True, PREFIX) == 2
        body = s.hit("POST", "/messages/batchModify")[0][2]
        assert body == {"ids": ["m1", "m2"], "addLabelIds": ["L1"], "removeLabelIds": ["INBOX"]}

    def test_move_matching_nothing_skips_modify(self, monkeypatch):
        s = _FilterStub()
        monkeypatch.setattr(gp, "_http", s)
        assert gp.move_matching("from:x", "L1", True, PREFIX) == 0
        assert not s.hit("POST", "/messages/batchModify")

    def test_old_token_raises_scope_error(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _FilterStub(scope_ok=False))
        with pytest.raises(gp.ScopeError, match="--auth"):
            gp.ensure_label("School", PREFIX)


class TestFilterTools:
    @pytest.fixture(autouse=True)
    def _mail_member(self, monkeypatch):
        monkeypatch.setattr(at._members, "mail_pref", lambda m, *a, **k: {
            "provider": "gmail", "cred_prefix": PREFIX, "enabled": True})
        md.drop("MemberA", at.FILTER_SLOT)
        md.drop("MemberA")

    @pytest.fixture
    def fstub(self, monkeypatch):
        s = _FilterStub(matches=["m1", "m2", "m3"])
        monkeypatch.setattr(gp, "_http", s)
        return s

    def _draft(self, **kw):
        return at.tool_draft_mail_filter({"member": "MemberA", "from": "school.example",
                                          "label": "学校", **TURN, **kw})

    def _apply(self, turn=NEXT_TURN):
        return at.tool_apply_mail_filter({"member": "MemberA", **turn, "__text": "确认"})

    def test_registered_gated_and_shown(self):
        names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
        for t in ("draft_mail_filter", "apply_mail_filter", "mail_filters"):
            assert t in names and t in ac._MEMBER_LOCKED
        assert {"draft_mail_filter", "apply_mail_filter"} <= ac._CONTEXT_TOOLS
        assert "draft_mail_filter" in ac._SHOW_TOOLS

    def test_draft_previews_without_touching_gmail_settings(self, fstub):
        out = self._draft()
        assert "from:school.example" in out and "学校" in out and "约 3 封" in out
        assert not fstub.hit("POST", "/settings/filters") and not fstub.hit("POST", "/labels")

    def test_draft_needs_criterion_and_label(self, fstub):
        assert self._draft(**{"from": ""}).startswith("[错误]")
        assert self._draft(label=" / ").startswith("[错误]")
        assert self._draft(label="inbox").startswith("[错误]")

    def test_confirmed_next_turn_creates_label_and_filter(self, fstub):
        self._draft()
        assert self._apply().startswith("已建过滤器")
        assert fstub.hit("POST", "/labels")[0][2]["name"] == "学校"
        assert fstub.hit("POST", "/settings/filters")
        assert not fstub.hit("POST", "/messages/batchModify")          # 默认不动旧信
        assert md.get("MemberA", at.FILTER_SLOT) is None

    def test_apply_existing_moves_old_mail(self, fstub):
        self._draft(apply_existing="true")
        assert "旧信已移 3 封" in self._apply()

    def test_same_turn_apply_refused(self, fstub):
        self._draft()
        assert self._apply(TURN).startswith("[错误]")
        assert not fstub.hit("POST", "/settings/filters")

    def test_no_filter_draft_refused(self, fstub):
        out = self._apply()
        assert out.startswith("[错误]") and "draft_mail_filter" in out

    def test_filter_draft_and_mail_draft_do_not_collide(self, fstub):
        md.put("MemberA", {"to": "s@example.com", "subject": "x", "body": "y"},
               turn_id=3, user="u1")
        self._draft()
        assert md.get("MemberA")["to"] == "s@example.com"
        assert md.get("MemberA", at.FILTER_SLOT)["label"] == "学校"

    def test_scope_error_keeps_draft(self, monkeypatch):
        monkeypatch.setattr(gp, "_http", _FilterStub(matches=["m1"], scope_ok=False))
        self._draft()
        out = self._apply()
        assert out.startswith("[错误]") and "--auth" in out
        assert md.get("MemberA", at.FILTER_SLOT) is not None

    def test_list_and_remove(self, monkeypatch):
        s = _FilterStub(labels=["学校"], filters=[{
            "id": "F0", "criteria": {"from": "school.example"},
            "action": {"addLabelIds": ["L0"], "removeLabelIds": ["INBOX"]}}])
        monkeypatch.setattr(gp, "_http", s)
        out = at.tool_mail_filters({"member": "MemberA"})
        assert "1. 条件 from:school.example → 学校，不进收件箱" in out
        assert at.tool_mail_filters({"member": "MemberA", "remove": 2}).startswith("[错误]")
        assert at.tool_mail_filters({"member": "MemberA", "remove": 1}).startswith("已删")
        assert s.filters == []
