"""
Family Assistant — 平面/扫描 PDF 盖字（tier 2）。

无 AcroForm 字段的 PDF：pypdfium2 逐页渲染成图 → （调用方经 OCR+LLM 定字段
锚点）→ Pillow 把答案画在锚点处 → 重组多页 PDF。输出是栅格（输入本就是扫描件）。

依赖 pypdfium2 + Pillow（可选）：缺席时 is_available()=False，cli 层给安装提示。
"""

from __future__ import annotations

from pathlib import Path

MAX_FLAT_PAGES = 6      # 平面表页数上限（渲染+OCR 逐页花时间/额度，表格通常很短）
RENDER_SCALE = 2.0      # 渲染放大倍数（OCR 与盖字共用同一坐标系）
MIN_FONT_PX = 8         # 缩字下限，再放不下就截断

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑（中文）
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def is_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def render_pages(pdf_path: str, out_dir: str) -> list:
    """逐页渲染 PNG（上限 MAX_FLAT_PAGES）→ [{"page","image","width","height"}]。"""
    import pypdfium2 as pdfium
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    pages = []
    try:
        for i in range(min(len(pdf), MAX_FLAT_PAGES)):
            pil = pdf[i].render(scale=RENDER_SCALE).to_pil().convert("RGB")
            p = out / f"page_{i}.png"
            pil.save(p)
            pages.append({"page": i, "image": str(p),
                          "width": pil.width, "height": pil.height})
    finally:
        pdf.close()
    return pages


def _load_font(px: int):
    from PIL import ImageFont
    for cand in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, px)
        except OSError:
            continue
    return ImageFont.load_default()


def stamp(pages: list, fields: list, out_pdf: str) -> list:
    """把已答字段画到页面图（内存副本，不改源图，可反复重渲染），输出多页 PDF。
    返回警告（截断等）。

    checkbox：on 画 X（✓ 在部分字体缺字形，X 全字体可靠），off 不画。
    文本：先按锚高取字号，放不下逐级缩到 MIN_FONT_PX，仍放不下则截断加 …。
    """
    from PIL import Image, ImageDraw
    warnings = []
    by_page: dict = {}
    for f in fields:
        if f.get("value") and f.get("anchor"):
            by_page.setdefault(int(f.get("page", 0)), []).append(f)
    images = []
    for pg in pages:
        img = Image.open(pg["image"]).convert("RGB")
        draw = ImageDraw.Draw(img)
        for f in by_page.get(int(pg["page"]), []):
            if f["type"] == "checkbox":
                if f["value"] != "on":
                    continue
                text = "X"
            else:
                text = str(f["value"])
            a = f["anchor"]
            px = max(int(a["h"] * 0.8), MIN_FONT_PX)
            font = _load_font(px)
            while px > MIN_FONT_PX and draw.textlength(text, font=font) > a["w"]:
                px -= 1
                font = _load_font(px)
            if draw.textlength(text, font=font) > a["w"]:
                while text and draw.textlength(text + "…", font=font) > a["w"]:
                    text = text[:-1]
                text += "…"
                warnings.append(f"{f['name']}: 值过长已截断")
            draw.text((a["x"], a["y"]), text, font=font, fill=(0, 0, 0))
        images.append(img)
    images[0].save(out_pdf, "PDF", save_all=True, append_images=images[1:],
                   resolution=72 * RENDER_SCALE)
    return warnings
