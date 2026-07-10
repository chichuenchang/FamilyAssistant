# tests/test_form_overlay.py — 平面 PDF 渲染与盖字（缺依赖跳过）。
import pytest

pytest.importorskip("pypdfium2")
PIL = pytest.importorskip("PIL")

import form_overlay
from PIL import Image


@pytest.fixture
def flat_pdf(tmp_path):
    """一页空白 PDF（用 Pillow 生成，不依赖 pypdf）。"""
    img = Image.new("RGB", (612, 792), "white")
    p = tmp_path / "flat.pdf"
    img.save(p, "PDF")
    return str(p)


def test_render_pages(flat_pdf, tmp_path):
    pages = form_overlay.render_pages(flat_pdf, str(tmp_path / "out"))
    assert len(pages) == 1
    pg = pages[0]
    assert pg["page"] == 0
    im = Image.open(pg["image"])
    assert (im.width, im.height) == (pg["width"], pg["height"])
    assert pg["width"] > 612  # scale 2.0 放大


def test_render_pages_caps_at_max(tmp_path):
    imgs = [Image.new("RGB", (100, 100), "white") for _ in range(8)]
    p = tmp_path / "big.pdf"
    imgs[0].save(p, "PDF", save_all=True, append_images=imgs[1:])
    pages = form_overlay.render_pages(str(p), str(tmp_path / "out"))
    assert len(pages) == form_overlay.MAX_FLAT_PAGES


def _page_fixture(tmp_path, w=800, h=600):
    img = Image.new("RGB", (w, h), "white")
    p = tmp_path / "page_0.png"
    img.save(p)
    return [{"page": 0, "image": str(p), "width": w, "height": h}]


def test_stamp_draws_text_and_outputs_pdf(tmp_path):
    pages = _page_fixture(tmp_path)
    fields = [
        {"name": "n", "label": "姓名", "type": "text", "options": [], "page": 0,
         "anchor": {"x": 100, "y": 100, "w": 300, "h": 24}, "value": "Zheng", "asked": True},
        {"name": "skip", "label": "留空", "type": "text", "options": [], "page": 0,
         "anchor": {"x": 100, "y": 200, "w": 300, "h": 24}, "value": "", "asked": True},
        {"name": "cb", "label": "勾", "type": "checkbox", "options": [], "page": 0,
         "anchor": {"x": 100, "y": 300, "w": 24, "h": 24}, "value": "on", "asked": True},
    ]
    out = tmp_path / "filled.pdf"
    warnings = form_overlay.stamp(pages, fields, str(out))
    assert warnings == []
    assert out.exists() and out.stat().st_size > 0
    # 盖字后与原白页不同
    stamped = Image.open(pages[0]["image"]).convert("RGB")
    region = stamped.crop((100, 100, 400, 124))
    assert region.getextrema() != ((255, 255), (255, 255), (255, 255))


def test_stamp_truncates_overlong_value(tmp_path):
    pages = _page_fixture(tmp_path)
    fields = [{"name": "n", "label": "n", "type": "text", "options": [], "page": 0,
               "anchor": {"x": 10, "y": 10, "w": 40, "h": 12},
               "value": "a-very-long-value-that-cannot-fit", "asked": True}]
    warnings = form_overlay.stamp(pages, fields, str(tmp_path / "o.pdf"))
    assert warnings and "截断" in warnings[0]
