"""合成 PDF 样本（reportlab 造），PDF_Editor 各测试共用。"""
from pathlib import Path


def _canvas(path, size=(612, 792)):
    from reportlab.pdfgen import canvas
    return canvas.Canvas(str(path), pagesize=size)


def build_digital_pdf(path, pages=2) -> Path:
    """每页两行标签 Name<i>: / DOB<i>:（Helvetica 12pt，基线 y=700 / 660）。"""
    c = _canvas(path)
    for i in range(pages):
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, f"Name{i}:")
        c.drawString(72, 660, f"DOB{i}:")
        c.showPage()
    c.save()
    return Path(path)


def build_acro_pdf(path) -> Path:
    """单页 AcroForm：text 字段 name（rect 160,690,360,710）+ checkbox married。"""
    c = _canvas(path)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Name:")
    c.acroForm.textfield(name="name", tooltip="Full name",
                         x=160, y=690, width=200, height=20)
    c.drawString(72, 660, "Married:")
    c.acroForm.checkbox(name="married", tooltip="Married", x=160, y=650, size=14)
    c.showPage()
    c.save()
    return Path(path)


def build_blank_pdf(path, pages=1) -> Path:
    """无文字层（当扫描件用）。"""
    c = _canvas(path)
    for _ in range(pages):
        c.rect(50, 50, 100, 100)
        c.showPage()
    c.save()
    return Path(path)


def build_png(path, size=(80, 30)) -> Path:
    from PIL import Image
    Image.new("RGB", size, (0, 0, 200)).save(str(path))
    return Path(path)


def rotate_pdf(src, dst, deg) -> Path:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    w.append(PdfReader(str(src)))
    for p in w.pages:
        p.rotate(deg)
    with open(dst, "wb") as fh:
        w.write(fh)
    return Path(dst)


def text_rects(pdf_path, page=0) -> list:
    """[(text, (l, b, r, t))]，pdfium 用户空间 point。"""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        tp = pdf[page].get_textpage()
        out = []
        for i in range(tp.count_rects()):
            rect = tp.get_rect(i)
            out.append((tp.get_text_bounded(*rect).strip(), rect))
        return out
    finally:
        pdf.close()


def page_texts(pdf_path) -> list:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return [pdf[i].get_textpage().get_text_bounded() for i in range(len(pdf))]
    finally:
        pdf.close()
