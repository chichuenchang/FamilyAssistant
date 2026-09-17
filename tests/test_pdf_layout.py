# tests/test_pdf_layout.py — PDF_Editor 版面提取。
import pytest

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")
pytest.importorskip("pypdfium2")

import pdf_layout
from pdf_samples import build_acro_pdf, build_blank_pdf, build_digital_pdf, rotate_pdf


def test_geometry_is_visual_px(tmp_path):
    p = build_digital_pdf(tmp_path / "d.pdf")
    g = pdf_layout.page_geometry(pdf_layout.open_reader(p))
    assert [x["page"] for x in g] == [0, 1]
    assert (g[0]["width"], g[0]["height"], g[0]["scale"]) == (1224, 1584, 2.0)
    assert g[0]["box"] == [0.0, 0.0, 612.0, 792.0] and g[0]["rotate"] == 0


def test_geometry_swaps_dims_when_rotated(tmp_path):
    p = rotate_pdf(build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "r.pdf", 90)
    g = pdf_layout.page_geometry(pdf_layout.open_reader(p))
    assert (g[0]["width"], g[0]["height"], g[0]["rotate"]) == (1584, 1224, 90)


def test_digital_lines_have_topleft_px_boxes(tmp_path):
    layout = pdf_layout.build(build_digital_pdf(tmp_path / "d.pdf"))
    assert layout["kind"] == "digital" and layout["fields"] == []
    line = next(l for l in layout["lines"]["0"] if l["text"].startswith("Name0"))
    assert abs(line["x"] - 144) <= 4          # 72pt
    assert 150 <= line["y"] <= 176            # 基线 700pt、12pt 字 → 顶 ≈ 82pt
    assert [l["text"][:4] for l in layout["lines"]["0"]] == ["Name", "DOB0"]   # 自上而下


@pytest.mark.parametrize("deg,expect", [
    (90, {"x": 1396, "y": 144}),      # vx = Y-b, vy = X-l
    (180, {"x": 996, "y": 1396}),     # vx = r-r0, vy = b0-b
    (270, {"x": 164, "y": 996}),      # vx = t-t0, vy = r-r0
])
def test_rotated_lines_map_to_visual_space(tmp_path, deg, expect):
    p = rotate_pdf(build_digital_pdf(tmp_path / "d.pdf"), tmp_path / "r.pdf", deg)
    line = next(l for l in pdf_layout.build(p)["lines"]["0"] if l["text"].startswith("Name0"))
    assert abs(line["x"] - expect["x"]) <= 16 and abs(line["y"] - expect["y"]) <= 16


def test_acro_fields_carry_type_label_and_widget_box(tmp_path):
    layout = pdf_layout.build(build_acro_pdf(tmp_path / "a.pdf"))
    assert layout["kind"] == "acroform"
    f = {x["name"]: x for x in layout["fields"]}
    assert f["name"]["type"] == "text" and f["name"]["label"] == "Full name"
    w = f["name"]["widgets"][0]
    assert (w["page"], w["x"], w["y"], w["w"], w["h"]) == (0, 320, 164, 400, 40)
    assert f["married"]["type"] == "checkbox" and f["married"]["options"] == ["/Yes"]
    assert layout["lines"]["0"][0]["text"].startswith("Name")     # 表单也带文字行


def test_textless_page_goes_through_ocr(tmp_path, monkeypatch):
    import ocr
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_image_words",
                        lambda p: [{"text": "姓名", "x": 10, "y": 20, "w": 60, "h": 24}])
    layout = pdf_layout.build(build_blank_pdf(tmp_path / "s.pdf"))
    assert layout["kind"] == "scanned"
    assert layout["lines"]["0"] == [{"text": "姓名", "x": 10, "y": 20, "w": 60, "h": 24}]


def test_scanned_without_ocr_is_a_note_not_a_crash(tmp_path, monkeypatch):
    import ocr
    monkeypatch.setattr(ocr, "is_available", lambda: False)
    layout = pdf_layout.build(build_blank_pdf(tmp_path / "s.pdf"))
    assert layout["kind"] == "scanned" and layout["lines"] == {}
    assert any("TENCENT_SECRET_ID" in n for n in layout["notes"])


def test_missing_pdfium_is_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_layout, "has_pdfium", lambda: False)
    layout = pdf_layout.build(build_acro_pdf(tmp_path / "a.pdf"))
    assert layout["kind"] == "acroform" and layout["lines"] == {}
    assert any("pypdfium2" in n for n in layout["notes"])


def test_encrypted_raises(tmp_path):
    from pypdf import PdfReader, PdfWriter
    src = build_digital_pdf(tmp_path / "d.pdf")
    w = PdfWriter()
    w.append(PdfReader(str(src)))
    w.encrypt("secret")
    enc = tmp_path / "e.pdf"
    with open(enc, "wb") as fh:
        w.write(fh)
    with pytest.raises(ValueError, match="已加密"):
        pdf_layout.build(enc)


def test_describe_with_and_without_coords(tmp_path):
    layout = pdf_layout.build(build_acro_pdf(tmp_path / "a.pdf"))
    full = pdf_layout.describe(layout)
    assert "类型: acroform | 页数: 1" in full
    assert "--- page=0（第 1 页）1224x1584 ---" in full
    assert "字段 name | Full name | text [x=320,y=164,w=400,h=40]" in full
    assert "字段 married | Married | checkbox[/Yes]" in full
    plain = pdf_layout.describe(layout, coords=False)
    assert "字段 name | Full name | text" in plain and "x=" not in plain
