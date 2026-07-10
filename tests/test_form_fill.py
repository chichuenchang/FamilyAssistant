# tests/test_form_fill.py — AcroForm 扫描与填写（pypdf 缺席则跳过）。
import pytest

pypdf = pytest.importorskip("pypdf")

import form_fill
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DictionaryObject, NameObject,
                           NumberObject, TextStringObject)


def _text_field(name, x, y):
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/Rect"): ArrayObject(
            [NumberObject(x), NumberObject(y), NumberObject(x + 200), NumberObject(y + 20)]),
    })


def _checkbox(name, x, y):
    off = DictionaryObject()
    on = DictionaryObject()
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/V"): NameObject("/Off"),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): DictionaryObject({
            NameObject("/Yes"): on, NameObject("/Off"): off})}),
        NameObject("/Rect"): ArrayObject(
            [NumberObject(x), NumberObject(y), NumberObject(x + 15), NumberObject(y + 15)]),
    })


def build_acro_pdf(path):
    """最小 AcroForm：一个文本域 + 一个勾选框。"""
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    w.add_annotation(0, _text_field("family_name", 100, 700))
    w.add_annotation(0, _checkbox("agree", 100, 650))
    # add_annotation 不建 /AcroForm —— 手动挂 AcroForm 指向两个 widget
    page = w.pages[0]
    fields = ArrayObject(page["/Annots"])
    acro = DictionaryObject({NameObject("/Fields"): fields})
    w._root_object[NameObject("/AcroForm")] = acro
    with open(path, "wb") as fh:
        w.write(fh)
    return str(path)


@pytest.fixture
def acro_pdf(tmp_path):
    return build_acro_pdf(tmp_path / "form.pdf")


def test_scan_fields(acro_pdf):
    fields = form_fill.scan_fields(acro_pdf)
    by_name = {f["name"]: f for f in fields}
    assert by_name["family_name"]["type"] == "text"
    cb = by_name["agree"]
    assert cb["type"] == "checkbox"
    assert cb["options"] == ["/Yes"]


def test_scan_fields_flat_pdf_returns_empty(tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    p = tmp_path / "flat.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    assert form_fill.scan_fields(str(p)) == []


def test_render_fills_values(acro_pdf, tmp_path):
    out = tmp_path / "filled.pdf"
    form_fill.render(acro_pdf, {"family_name": "Zheng", "agree": "on"}, str(out))
    got = PdfReader(str(out)).get_fields()
    assert got["family_name"]["/V"] == "Zheng"
    assert got["agree"]["/V"] == "/Yes"


def test_render_skips_blank(acro_pdf, tmp_path):
    out = tmp_path / "filled.pdf"
    form_fill.render(acro_pdf, {"family_name": "", "agree": "off"}, str(out))
    got = PdfReader(str(out)).get_fields()
    assert not got["family_name"].get("/V")
    assert got["agree"]["/V"] == "/Off"


def test_is_available_true_here():
    assert form_fill.is_available()
