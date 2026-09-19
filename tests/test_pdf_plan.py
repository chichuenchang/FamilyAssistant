# tests/test_pdf_plan.py — PDF_Editor 会话存储 + instruction→ops。
import json
from datetime import datetime

import pytest

import pdf_plan

PAGE = {"width": 1224, "height": 1584, "scale": 2.0, "box": [0, 0, 612, 792], "rotate": 0}
LAYOUT = {"kind": "acroform",
          "pages": [{"page": 0, **PAGE}, {"page": 1, **PAGE}, {"page": 2, **PAGE}],
          "fields": [{"name": "name", "label": "Full name", "type": "text",
                      "options": [], "widgets": []}],
          "lines": {}, "notes": []}
FENCE = "`" * 3


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    return tmp_path / "data"


def _ok(path):
    return path


def test_new_session_persists_plan_and_layout(env):
    plan = pdf_plan.new_session("jim", "jim/inbox/f.pdf", LAYOUT)
    d = env / "jim" / "pdf_edits" / plan["id"]
    assert json.loads((d / "plan.json").read_text(encoding="utf-8"))["source_pdf"] == "jim/inbox/f.pdf"
    again = pdf_plan.load("jim", plan["id"])
    assert again["kind"] == "acroform" and again["ops"] == [] and again["history"] == []
    assert pdf_plan.load_layout(again) == LAYOUT


def test_load_rejects_bad_or_missing_id(env):
    with pytest.raises(ValueError):
        pdf_plan.load("jim", "../x")
    with pytest.raises(FileNotFoundError):
        pdf_plan.load("jim", "20260101_000000_abcd")


def test_latest_for_source_picks_newest_of_same_source(env, monkeypatch):
    monkeypatch.setattr(pdf_plan, "_now", lambda: datetime(2026, 9, 1, 10, 0, 0))
    old = pdf_plan.new_session("jim", "jim/a.pdf", LAYOUT)
    monkeypatch.setattr(pdf_plan, "_now", lambda: datetime(2026, 9, 1, 10, 0, 5))
    new = pdf_plan.new_session("jim", "jim/a.pdf", LAYOUT)
    pdf_plan.new_session("jim", "jim/b.pdf", LAYOUT)
    assert pdf_plan.latest_for_source("jim", "jim/a.pdf")["id"] == new["id"] != old["id"]
    assert pdf_plan.latest_for_source("jim", "jim/zzz.pdf") is None
    assert len(pdf_plan.list_sessions("jim")) == 3 and pdf_plan.list_sessions("amy") == []


def test_parse_reply_accepts_array_object_and_fenced():
    ops = [{"op": "field", "name": "name", "value": "张"}]
    assert pdf_plan.parse_reply(json.dumps(ops)) == (ops, [])
    assert pdf_plan.parse_reply(json.dumps({"ops": ops, "notes": ["缺生日"]})) == (ops, ["缺生日"])
    fenced = f"好的：\n{FENCE}json\n{json.dumps(ops)}\n{FENCE}"
    assert pdf_plan.parse_reply(fenced) == (ops, [])


@pytest.mark.parametrize("bad", ["", "没有 json", "[1, 2]", '{"ops": "x"}'])
def test_parse_reply_rejects_garbage(bad):
    with pytest.raises(ValueError):
        pdf_plan.parse_reply(bad)


def test_compile_carries_layout_prior_ops_and_instruction():
    seen = {}

    def chat(messages):
        seen["m"] = messages
        return '{"ops": [{"op": "field", "name": "name", "value": "张三"}], "notes": []}'

    prior = [{"op": "erase", "page": 0, "x": 1, "y": 2, "w": 3, "h": 4}]
    ops, notes = pdf_plan.compile_ops("LAYOUT-TEXT", prior, "名字填张三", chat=chat)
    assert ops == [{"op": "field", "name": "name", "value": "张三"}] and notes == []
    assert seen["m"][0]["role"] == "system"
    user = seen["m"][1]["content"]
    assert "LAYOUT-TEXT" in user and "名字填张三" in user and '"erase"' in user


def test_compile_retries_once_then_raises():
    calls = []

    def flaky(messages):
        calls.append(len(messages))
        return "抱歉" if len(calls) == 1 else "[]"

    assert pdf_plan.compile_ops("L", [], "i", chat=flaky) == ([], [])
    assert calls == [2, 4]                      # 重试时带上坏回复 + 纠正要求

    with pytest.raises(pdf_plan.PlanError, match="排版模型没给出可用编辑计划"):
        pdf_plan.compile_ops("L", [], "i", chat=lambda m: "")


def test_validate_passes_known_ops_and_normalizes():
    ops = [
        {"op": "field", "name": "name", "value": 5},
        {"op": "text", "page": 0, "x": "10", "y": 20, "text": "hi", "w": 100, "h": 30},
        {"op": "check", "page": 1, "x": 5, "y": 5},
        {"op": "erase", "page": 0, "x": 1, "y": 1, "w": 10, "h": 10},
        {"op": "image", "page": 0, "x": 1, "y": 1, "w": 50, "src": "jim/sig.png"},
        {"op": "line", "page": 0, "x1": 0, "y1": 0, "x2": 10, "y2": 0},
        {"op": "page_delete", "pages": [2]},
        {"op": "page_rotate", "page": 1, "deg": -90},
        {"op": "page_reorder", "order": [1, 0]},
        {"op": "page_insert", "src": "jim/x.pdf"},
    ]
    clean, warns = pdf_plan.validate_ops(ops, LAYOUT, _ok)
    assert warns == [] and len(clean) == len(ops)
    assert clean[0]["value"] == "5"
    assert clean[1] == {"op": "text", "page": 0, "x": 10.0, "y": 20.0, "text": "hi",
                        "w": 100.0, "h": 30.0, "size": None}
    assert clean[2]["size"] == 18.0 and clean[5]["width"] == 2.0
    assert clean[4]["h"] is None
    assert clean[7]["deg"] == 270 and clean[9]["after"] == 2


def test_validate_skips_bad_ops_with_warnings():
    ops = [
        {"op": "highlight", "page": 0},
        {"op": "field", "name": "nope", "value": "x"},
        {"op": "text", "page": 9, "x": 1, "y": 1, "text": "t"},
        {"op": "text", "page": 0, "x": 99999, "y": 1, "text": "t"},
        {"op": "text", "page": 0, "x": 1, "y": 1, "text": ""},
        {"op": "erase", "page": 0, "x": 1, "y": 1, "w": 0, "h": 5},
        {"op": "page_rotate", "page": 0, "deg": 45},
        {"op": "page_reorder", "order": [0, 0]},
        "not-a-dict",
    ]
    clean, warns = pdf_plan.validate_ops(ops, LAYOUT, _ok)
    assert clean == [] and len(warns) == len(ops)
    assert warns[0] == "不支持的操作 highlight"
    assert "nope" in warns[1]


def test_validate_resolves_field_by_unique_label():
    # 实测：排版模型会拿标签当字段名
    layout = {**LAYOUT, "fields": LAYOUT["fields"] + [
        {"name": "a1", "label": "Date", "type": "text", "options": [], "widgets": []},
        {"name": "a2", "label": "Date", "type": "text", "options": [], "widgets": []}]}
    clean, warns = pdf_plan.validate_ops(
        [{"op": "field", "name": "Full name", "value": "张三"},
         {"op": "field", "name": "Date", "value": "x"}], layout, _ok)
    assert clean == [{"op": "field", "name": "name", "value": "张三"}]
    assert len(warns) == 1 and "Date" in warns[0]


def test_validate_gates_src_paths():
    ops = [{"op": "image", "page": 0, "x": 1, "y": 1, "w": 50, "src": "amy/sig.png"},
           {"op": "page_insert", "src": "../../etc/x.pdf", "after": 0}]
    clean, warns = pdf_plan.validate_ops(ops, LAYOUT, lambda p: None)
    assert clean == [] and all("路径不允许或文件不存在" in w for w in warns)
