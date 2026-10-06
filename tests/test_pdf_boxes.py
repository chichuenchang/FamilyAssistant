# tests/test_pdf_boxes.py — PDF_Editor 表格空格 / 勾选框定位。
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("reportlab")
pytest.importorskip("pypdfium2")

import pdf_boxes
import pdf_layout
from pdf_samples import build_table_form_pdf


def _near(a, b, tol=6):
    return abs(a - b) <= tol


def test_components_find_enclosed_white_box():
    gray = np.full((60, 80), 255, dtype=np.uint8)
    gray[10, 10:50] = gray[40, 10:50] = 0          # 框 x 10–49 × y 10–40
    gray[10:41, 10] = gray[10:41, 49] = 0
    assert pdf_boxes.rects(gray) == [{"x": 11, "y": 11, "w": 38, "h": 29}]


def test_group_lines_merges_chars_and_pulls_out_check_glyphs():
    # 用户空间 (字, l, b, r, t)：逐字 + 一个词间距 + 远处另一栏 + 方框字形
    chars = [("⬜", 10, 100, 20, 110)]
    x = 24
    for ch in "NO":
        chars.append((ch, x, 100, x + 6, 109))
        x += 6
    x += 4                                           # 词间距
    for ch in "way":
        chars.append((ch, x, 100, x + 6, 109))
        x += 6
    chars.append(("Z", 200, 100, 206, 109))          # 远处另起一行
    lines, checks = pdf_layout.group_lines(chars)
    assert [l[0] for l in lines] == ["NO way", "Z"]
    assert checks == [(10, 100, 20, 110)]


def test_table_form_targets(tmp_path):
    layout = pdf_layout.build(build_table_form_pdf(tmp_path / "t.pdf"))
    assert layout["kind"] == "digital"
    texts = [l["text"] for l in layout["lines"]["0"]]
    assert "GIVEN NAME" in texts and "Phone #" in texts      # 逐字合并成行
    ts = layout["targets"]["0"]
    blanks = [t for t in ts if t["kind"] == "blank"]
    assert [t["near"]["above"] for t in blanks] == ["GIVEN NAME", "SURNAME"]
    assert _near(blanks[0]["x"], 100) and _near(blanks[0]["y"], 264) and _near(blanks[0]["h"], 60)
    assert not any(t.get("label") in ("GIVEN NAME", "SURNAME") for t in ts)   # 表头不当目标
    cell = next(t for t in ts if t["kind"] == "cell")
    assert cell["label"] == "Phone #" and cell["free"]["x"] > 112 + 80
    check = next(t for t in ts if t["kind"] == "check")
    assert check["near"]["right"] == "NO" and _near(check["x"], 120) and _near(check["y"], 444)
    assert [t["id"] for t in ts] == [f"p0-{i}" for i in range(1, len(ts) + 1)]


def test_describe_lists_targets_only_with_coords(tmp_path):
    layout = pdf_layout.build(build_table_form_pdf(tmp_path / "t.pdf"))
    full = pdf_layout.describe(layout)
    assert "空格 [x=" in full and "上:GIVEN NAME" in full and "勾选框" in full and "右:NO" in full
    assert "目标" not in pdf_layout.describe(layout, coords=False)


def test_describe_budget_is_per_page():
    page = {"width": 100, "height": 100, "scale": 2.0, "box": [0, 0, 50, 50], "rotate": 0}
    many = [{"text": f"l{i}", "x": 0, "y": i, "w": 1, "h": 1}
            for i in range(pdf_layout.MAX_PAGE_LINES + 5)]
    layout = {"kind": "digital", "fields": [], "notes": [], "targets": {},
              "pages": [{"page": 0, **page}, {"page": 1, **page}],
              "lines": {"0": many, "1": [{"text": "second", "x": 0, "y": 0, "w": 1, "h": 1}]}}
    text = pdf_layout.describe(layout)
    assert "（本页其余文字行已截断）" in text and "second" in text
