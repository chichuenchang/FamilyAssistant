"""transport_base：频道共用生命周期（闸门 / 投递 / 异常兜底 / 后台节拍）。"""
import agent_core
import tool_runtime as rt
import transport_base as tb


class FakeTransport(tb.Transport):
    channel = "telegram"
    tag = "fk"

    def __init__(self, agent=None):
        super().__init__(agent)
        self.calls = []

    def send_text(self, target, text):
        self.calls.append(("text", target, text))

    def send_photo(self, target, path):
        self.calls.append(("photo", target, path))

    def send_document(self, target, path):
        self.calls.append(("doc", target, path))

    def after_text_sent(self, target, text):
        self.calls.append(("after", target, text))


class FakeAgent:
    def __init__(self, reply="ok", boom=False):
        self.reply, self.boom, self.seen = reply, boom, []

    def handle(self, text, user="", member="", said=None):
        self.said = said
        self.seen.append(("text", text, user, member))
        if self.boom:
            raise RuntimeError("llm down")
        return self.reply

    def handle_media(self, paths, text, user="", member="", said=None):
        self.said = said
        self.seen.append(("media", paths, text, user, member))
        return self.reply


def _seed(tmp_path):
    img = tmp_path / "Alex" / "charts" / "c.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\x89PNG")
    doc = tmp_path / "Family" / "documents" / "d.pdf"
    doc.parent.mkdir(parents=True)
    doc.write_bytes(b"%PDF")
    return "Alex/charts/c.png", "Family/documents/d.pdf"


def test_with_quote():
    assert tb.with_quote("hi", "prev") == f"[引用]\n{rt.fence('prev', 'quote')}\nhi"
    assert tb.with_quote("hi", None) == "hi"


def test_deliver_photo_then_doc_then_text_then_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    img, doc = _seed(tmp_path)
    t = FakeTransport(agent=FakeAgent())
    t.deliver(7, f"说明\n{agent_core.IMG_SENTINEL}{img}\n{agent_core.DOC_SENTINEL}{doc}")
    assert [c[0] for c in t.calls] == ["photo", "doc", "text", "after"]
    assert t.calls[2] == ("text", 7, "说明")


def test_deliver_gates_missing_and_escaping_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    t = FakeTransport(agent=FakeAgent())
    t.deliver(7, f"hi\n{agent_core.IMG_SENTINEL}Alex/missing.png\n{agent_core.DOC_SENTINEL}../../etc/passwd")
    assert t.calls == [("text", 7, "hi"), ("after", 7, "hi")]


def test_deliver_empty_reply_sends_nothing():
    t = FakeTransport(agent=FakeAgent())
    t.deliver(7, "")
    assert t.calls == []


def test_one_failed_attachment_does_not_block_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    img, _ = _seed(tmp_path)
    t = FakeTransport(agent=FakeAgent())
    t.send_photo = lambda target, path: (_ for _ in ()).throw(RuntimeError("net"))
    t.deliver(7, f"文字\n{agent_core.IMG_SENTINEL}{img}")
    assert ("text", 7, "文字") in t.calls


def test_on_text_ticks_quotes_and_delivers(monkeypatch):
    ticks = []
    monkeypatch.setattr(tb.REGISTRY, "message_ticks",
                        [lambda: ticks.append("cal"), lambda: ticks.append("gc")])
    agent = FakeAgent(reply="回")
    t = FakeTransport(agent=agent)
    t.on_text("tgt", 42, "Alex", "你好", quoted="上一条")
    assert ticks == ["cal", "gc"]
    assert agent.seen == [("text", tb.with_quote("你好", "上一条"), "42", "Alex")]
    assert agent.said == "你好"          # 确认闸门只认用户亲手打的字，不含引用
    assert ("text", "tgt", "回") in t.calls


def test_on_text_agent_exception_reports_not_crashes(monkeypatch):
    monkeypatch.setattr(tb.REGISTRY, "message_ticks", [])
    t = FakeTransport(agent=FakeAgent(boom=True))
    t.on_text("tgt", 1, "Alex", "x")
    assert t.calls and t.calls[0][2].startswith("处理出错")


def test_on_media_none_path_asks_resend(monkeypatch):
    monkeypatch.setattr(tb.REGISTRY, "message_ticks", [])
    agent = FakeAgent()
    t = FakeTransport(agent=agent)
    t.on_media("tgt", 1, "Alex", None)
    assert agent.seen == []
    assert "重发" in t.calls[0][2]


def test_on_media_silent_until_text_then_batched(monkeypatch, tmp_path):
    monkeypatch.setattr(tb.REGISTRY, "message_ticks", [])
    agent = FakeAgent(reply="收到")
    t = FakeTransport(agent=agent)
    t.on_media("tgt", 1, "Alex", tmp_path / "a.jpg")
    t.on_media("tgt", 1, "Alex", tmp_path / "b.pdf")
    t.on_media("tgt", 2, "Bo", tmp_path / "c.jpg")     # 别的用户的来件不串
    assert agent.seen == [] and t.calls == []
    t.on_text("tgt", 1, "Alex", "记账", quoted="上一条")
    assert agent.seen == [("media", [str(tmp_path / "a.jpg"), str(tmp_path / "b.pdf")],
                           tb.with_quote("记账", "上一条"), "1", "Alex")]
    assert agent.said == "记账"
    assert ("text", "tgt", "收到") in t.calls
    t.on_text("tgt", 1, "Alex", "再问")                # 来件已用掉 → 普通对话
    assert agent.seen[-1] == ("text", "再问", "1", "Alex")


def test_commands_bypass_pending_media(monkeypatch, tmp_path):
    monkeypatch.setattr(tb.REGISTRY, "message_ticks", [])
    agent = FakeAgent()
    t = FakeTransport(agent=agent)
    t.on_media("tgt", 1, "Alex", tmp_path / "a.jpg")
    t.on_text("tgt", 1, "Alex", "/model")               # 命令照常执行，来件继续等
    assert agent.seen == [("text", "/model", "1", "Alex")]
    t.on_text("tgt", 1, "Alex", "/clear")               # /clear 连来件一起清
    assert agent.seen[-1] == ("text", "/clear", "1", "Alex")
    t.on_text("tgt", 1, "Alex", "你好")
    assert agent.seen[-1] == ("text", "你好", "1", "Alex")


def test_gate_uses_channel_registry(monkeypatch):
    seen = {}

    def fake_resolve(channel, cid):
        seen.update(channel=channel, cid=cid)
        return "Alex" if cid == "1" else None

    monkeypatch.setattr(tb, "resolve", fake_resolve)
    t = FakeTransport(agent=FakeAgent())
    assert t.gate(1) == "Alex" and seen == {"channel": "telegram", "cid": "1"}
    assert t.gate(2) is None


def test_inbox_path_uses_member_inbox_and_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    t = FakeTransport(agent=FakeAgent())
    p = t.inbox_path("Alex", ".pdf")
    assert p.name.endswith("_telegram.pdf")
    assert p.parent.is_relative_to(tmp_path)


def test_background_tick_isolates_failures(monkeypatch):
    order = []
    monkeypatch.setattr(tb.REGISTRY, "slow_ticks", [
        lambda send, ch: (_ for _ in ()).throw(RuntimeError("r")),
        lambda send, ch: order.append(("backup", ch))])
    monkeypatch.setattr(tb.REGISTRY, "fast_ticks", [lambda send, ch: order.append(("kk", ch))])
    t = FakeTransport(agent=FakeAgent())
    t.background_tick()
    assert order == [("backup", "telegram"), ("kk", "telegram")]


def test_real_manifests_register_ticks():
    names = lambda fns: {getattr(f, "__name__", "") for f in fns}
    assert {"calendar_tick", "image_gc_tick"} <= names(tb.REGISTRY.message_ticks)
    assert {"check_and_push", "backup_tick"} <= names(tb.REGISTRY.slow_ticks)
    assert "poll_and_deliver" in names(tb.REGISTRY.fast_ticks)


def test_agent_is_lazy():
    t = FakeTransport()
    assert t._agent is None
    t.deliver(1, "只发文字")           # 投递不构造 Agent
    assert t._agent is None
