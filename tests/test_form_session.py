# tests/test_form_session.py — Form_Filler 会话存储。
import json

import pytest

import form_session


@pytest.fixture
def data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    return tmp_path / "data"


def _mk(member="Jim", kind="acroform", fields=None, **kw):
    return form_session.new_session(
        member, "jim/inbox/2026-07/f.pdf", kind,
        fields=fields if fields is not None else [
            {"name": "family_name", "label": "姓", "type": "text"},
            {"name": "agree", "label": "同意条款", "type": "checkbox", "options": ["/Yes"]},
            {"name": "province", "label": "省份", "type": "choice", "options": ["AB", "BC"]},
        ], **kw)


class TestCRUD:
    def test_new_session_persists_and_loads(self, data_root):
        s = _mk()
        assert (data_root / "jim" / "forms" / f"{s['id']}.json").exists()
        loaded = form_session.load("Jim", s["id"])
        assert loaded == s
        assert loaded["status"] == "collecting"
        assert loaded["kind"] == "acroform"
        assert all(f["value"] is None for f in loaded["fields"])

    def test_bad_kind_rejected(self, data_root):
        with pytest.raises(ValueError):
            _mk(kind="xfa")

    def test_load_missing_raises(self, data_root):
        with pytest.raises(FileNotFoundError):
            form_session.load("Jim", "20260709_000000_dead")

    def test_load_rejects_malformed_id(self, data_root):
        # 路径遍历防线：id 不匹配格式直接拒绝
        with pytest.raises(ValueError):
            form_session.load("Jim", "../evil")

    def test_list_sessions_newest_first(self, data_root):
        a = _mk()
        b = _mk()
        ids = [s["id"] for s in form_session.list_sessions("Jim")]
        assert set(ids) == {a["id"], b["id"]}
        assert form_session.list_sessions("Wenliang") == []


class TestFields:
    def test_field_requires_known_type(self, data_root):
        with pytest.raises(ValueError):
            _mk(fields=[{"name": "x", "label": "x", "type": "date"}])

    def test_next_and_set_and_progress(self, data_root):
        s = _mk()
        f = form_session.next_unanswered(s)
        assert f["name"] == "family_name"
        form_session.set_value(s, "family_name", "Zheng")
        assert form_session.progress(s) == (1, 3)
        # 落盘了
        again = form_session.load("Jim", s["id"])
        assert again["fields"][0]["value"] == "Zheng"
        assert form_session.next_unanswered(again)["name"] == "agree"

    def test_set_blank_counts_as_answered(self, data_root):
        s = _mk()
        form_session.set_value(s, "family_name", "")
        assert form_session.progress(s)[0] == 1

    def test_checkbox_normalization(self, data_root):
        s = _mk()
        for raw, want in [("on", "on"), ("yes", "on"), ("是", "on"), ("勾", "on"),
                          ("off", "off"), ("no", "off"), ("否", "off")]:
            form_session.set_value(s, "agree", raw)
            assert form_session.load("Jim", s["id"])["fields"][1]["value"] == want
        with pytest.raises(ValueError):
            form_session.set_value(s, "agree", "maybe")

    def test_choice_must_be_in_options(self, data_root):
        s = _mk()
        form_session.set_value(s, "province", "AB")
        with pytest.raises(ValueError):
            form_session.set_value(s, "province", "ON")

    def test_set_unknown_field_raises(self, data_root):
        s = _mk()
        with pytest.raises(ValueError):
            form_session.set_value(s, "nope", "x")


class TestFlatDefine:
    def test_define_fields_validates_anchor_and_activates(self, data_root):
        s = form_session.new_session(
            "Jim", "jim/inbox/2026-07/flat.pdf", "flat", status="defining",
            pages=[{"page": 0, "image": "jim/forms/x/page_0.png",
                    "width": 1000, "height": 1400}])
        assert form_session.next_unanswered(s) is None
        form_session.define_fields(s, [
            {"name": "name", "label": "姓名", "type": "text", "page": 0,
             "anchor": {"x": 100, "y": 200, "w": 300, "h": 24}}])
        assert s["status"] == "collecting"
        assert form_session.next_unanswered(s)["name"] == "name"

    def test_define_rejects_out_of_bounds_anchor(self, data_root):
        s = form_session.new_session(
            "Jim", "jim/inbox/2026-07/flat.pdf", "flat", status="defining",
            pages=[{"page": 0, "image": "p.png", "width": 1000, "height": 1400}])
        with pytest.raises(ValueError):
            form_session.define_fields(s, [
                {"name": "n", "label": "n", "type": "text", "page": 0,
                 "anchor": {"x": 900, "y": 200, "w": 300, "h": 24}}])
        with pytest.raises(ValueError):
            form_session.define_fields(s, [
                {"name": "n", "label": "n", "type": "text", "page": 5,
                 "anchor": {"x": 1, "y": 1, "w": 5, "h": 5}}])

    def test_define_only_when_defining(self, data_root):
        s = _mk()
        with pytest.raises(ValueError):
            form_session.define_fields(s, [])
