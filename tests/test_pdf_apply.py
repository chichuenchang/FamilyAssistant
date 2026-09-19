# tests/test_pdf_apply.py — PDF_Editor ops 应用（字段填写 / 覆盖层 / 页级操作）。
from pathlib import Path

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")
pytest.importorskip("pypdfium2")

from pypdf import PdfReader

import pdf_apply
import pdf_layout
from pdf_samples import (build_acro_pdf, build_digital_pdf, build_png, page_texts,
                         rotate_pdf, text_rects)


def _apply(src, ops, out):
    return pdf_apply.apply(str(src), ops, pdf_layout.build(src), str(out),
                           resolve_src=lambda rel: Path(rel))


def _rect_of(pdf, needle, page=0):
    return next(rc for tx, rc in text_rects(pdf, page) if needle in tx)


def test_text_lands_at_layout_coords(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    warns = _apply(src, [{"op": "text", "page": 0, "x": 320.0, "y": 164.0, "w": 400.0,
                          "h": 40.0, "text": "ZHANGSAN", "size": None}], out)
    assert warns == []
    l, b, r, t = _rect_of(out, "ZHANGSAN")
    assert abs(l - 160) <= 2 and 690 <= b and t <= 710      # 框 = 160..360 × 690..710 pt
    assert "Name0" in page_texts(out)[0]                     # 原内容还在


def test_cjk_text_is_extractable(tmp_path):
    if not pdf_apply._font()[1]:
        pytest.skip("本机无中文字体")
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    _apply(src, [{"op": "text", "page": 0, "x": 320.0, "y": 164.0, "w": None, "h": None,
                  "text": "张三", "size": 11.0}], out)
    assert "张三" in page_texts(out)[0]


def test_long_text_shrinks_then_truncates_with_warning(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    warns = _apply(src, [{"op": "text", "page": 0, "x": 320.0, "y": 164.0, "w": 60.0,
                          "h": 40.0, "text": "W" * 80, "size": None}], out)
    assert any("截断" in w for w in warns)
    l, b, r, t = _rect_of(out, "WW")
    assert r <= 160 + 30 + 2                                 # 没冲出 60px=30pt 的框


def test_reapply_from_original_does_not_stack(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    op = {"op": "text", "page": 0, "x": 320.0, "y": 164.0, "w": None, "h": None, "size": None}
    _apply(src, [{**op, "text": "FIRST"}], out)
    _apply(src, [{**op, "text": "SECOND"}], out)
    text = page_texts(out)[0]
    assert "SECOND" in text and "FIRST" not in text


def test_field_ops_set_real_values(tmp_path):
    src, out = build_acro_pdf(tmp_path / "a.pdf"), tmp_path / "o.pdf"
    warns = _apply(src, [{"op": "field", "name": "name", "value": "ZHANG"},
                         {"op": "field", "name": "married", "value": "是"}], out)
    assert warns == []
    r = PdfReader(str(out))
    f = r.get_fields()
    assert f["name"]["/V"] == "ZHANG" and f["married"]["/V"] == "/Yes"
    assert bool(r.trailer["/Root"]["/AcroForm"]["/NeedAppearances"])


def test_checkbox_off_and_bad_state(tmp_path):
    src, out = build_acro_pdf(tmp_path / "a.pdf"), tmp_path / "o.pdf"
    warns = _apply(src, [{"op": "field", "name": "married", "value": "maybe"}], out)
    assert any("married" in w for w in warns)
    _apply(src, [{"op": "field", "name": "married", "value": "off"}], out)
    assert PdfReader(str(out)).get_fields()["married"]["/V"] == "/Off"


def test_field_and_overlay_in_one_pass(tmp_path):
    src, out = build_acro_pdf(tmp_path / "a.pdf"), tmp_path / "o.pdf"
    _apply(src, [{"op": "field", "name": "name", "value": "ZHANG"},
                 {"op": "text", "page": 0, "x": 144.0, "y": 400.0, "w": None, "h": None,
                  "text": "MARGINNOTE", "size": None}], out)
    assert PdfReader(str(out)).get_fields()["name"]["/V"] == "ZHANG"
    assert "MARGINNOTE" in page_texts(out)[0]


def test_erase_warns_it_is_not_redaction(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    warns = _apply(src, [{"op": "erase", "page": 0, "x": 140.0, "y": 150.0,
                          "w": 120.0, "h": 40.0}], out)
    assert warns == [pdf_apply.ERASE_WARNING]


def test_check_image_line_draw(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    sig = build_png(tmp_path / "sig.png")
    warns = _apply(src, [
        {"op": "check", "page": 0, "x": 100.0, "y": 300.0, "size": 18.0},
        {"op": "image", "page": 0, "x": 200.0, "y": 600.0, "w": 160.0, "h": None, "src": str(sig)},
        {"op": "line", "page": 1, "x1": 100.0, "y1": 100.0, "x2": 400.0, "y2": 100.0, "width": 2.0},
    ], out)
    assert warns == []
    r = PdfReader(str(out))
    assert len(r.pages) == 2 and len(r.pages[0].images) == 1


def test_unreadable_image_is_a_warning(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    warns = _apply(src, [{"op": "image", "page": 0, "x": 1.0, "y": 1.0, "w": 50.0,
                          "h": None, "src": str(bad)}], out)
    assert any("图片" in w for w in warns) and out.exists()


def _names(out):
    return [t.split(":")[0].strip() for t in page_texts(out)]


def test_page_delete_reorder_rotate(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf", pages=3), tmp_path / "o.pdf"
    _apply(src, [{"op": "page_delete", "pages": [1]}], out)
    assert _names(out) == ["Name0", "Name2"]
    _apply(src, [{"op": "page_reorder", "order": [2, 0, 1]}], out)
    assert _names(out) == ["Name2", "Name0", "Name1"]
    _apply(src, [{"op": "page_rotate", "page": 0, "deg": 90}], out)
    r = PdfReader(str(out))
    assert [p.rotation for p in r.pages] == [90, 0, 0]


def test_page_insert_positions(tmp_path):
    src = build_digital_pdf(tmp_path / "d.pdf", pages=2)
    other = build_acro_pdf(tmp_path / "other.pdf")            # 页文字以 "Name:" 开头
    out = tmp_path / "o.pdf"
    _apply(src, [{"op": "page_insert", "src": str(other), "after": 0}], out)
    assert _names(out) == ["Name0", "Name", "Name1"]
    _apply(src, [{"op": "page_insert", "src": str(other), "after": -1}], out)
    assert _names(out) == ["Name", "Name0", "Name1"]


def test_content_ops_follow_their_page_through_reorder(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    _apply(src, [{"op": "page_reorder", "order": [1, 0]},
                 {"op": "text", "page": 0, "x": 320.0, "y": 164.0, "w": None, "h": None,
                  "text": "ONPAGEZERO", "size": None}], out)
    texts = page_texts(out)
    assert "ONPAGEZERO" in texts[1] and "Name0" in texts[1] and "ONPAGEZERO" not in texts[0]


def test_ops_on_deleted_page_are_dropped_with_warning(tmp_path):
    src, out = build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "o.pdf"
    warns = _apply(src, [{"op": "page_delete", "pages": [0]},
                         {"op": "text", "page": 0, "x": 1.0, "y": 1.0, "w": None, "h": None,
                          "text": "GONE", "size": None}], out)
    assert any("第 1 页" in w for w in warns)
    assert _names(out) == ["Name1"]


def test_deleting_every_page_raises(tmp_path):
    src = build_digital_pdf(tmp_path / "d.pdf")
    with pytest.raises(ValueError, match="所有页"):
        _apply(src, [{"op": "page_delete", "pages": [0, 1]}], tmp_path / "o.pdf")


@pytest.mark.parametrize("deg", [90, 180, 270])
def test_overlay_on_rotated_page_lands_visually(tmp_path, deg):
    src = rotate_pdf(build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "r.pdf", deg)
    out = tmp_path / "o.pdf"
    label = next(l for l in pdf_layout.build(src)["lines"]["0"] if l["text"].startswith("Name0"))
    _apply(src, [{"op": "text", "page": 0, "x": float(label["x"]),
                  "y": float(label["y"] + label["h"] + 10), "w": None, "h": None,
                  "text": "BELOW", "size": 12.0}], out)
    assert PdfReader(str(out)).pages[0].rotation == 0        # 旋转已烘进内容
    ll, lb, lr, lt = _rect_of(out, "Name0")
    nl, nb, nr, nt = _rect_of(out, "BELOW")
    assert abs(ll - nl) <= 3 and nt < lb                     # 左对齐、在标签正下方、正着读
