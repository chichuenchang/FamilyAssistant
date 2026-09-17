"""
Family Assistant — 把 ops 应用到原始 PDF（PDF_Editor）。

顺序：页序列重建（reorder → insert → delete，全用原始页号）→ 表单字段原生填写
（pypdf）→ 逐页 reportlab 覆盖层 merge_page → 旋转。原页字节不动，输出保持矢量。

旋转页先 transfer_rotation_to_content()：之后用户空间 = 视觉空间，覆盖层直接按
版面坐标画（否则写上去的字是躺着的）。
"""

from __future__ import annotations

import io
from pathlib import Path

import pdf_layout

MIN_FONT_PT = 6.0
DEFAULT_FONT_PT = 10.0
AUTO_FONT_PT = (9.0, 12.0)   # 自动字号夹在此区间：行框是贴字形的（偏矮），多行区是高框（偏高）
FONT_NAME = "PDFEditFont"
ERASE_WARNING = "白底覆盖不是脱敏：原内容仍留在文件里，可被复制或恢复"

# (路径, 能否显示中文)
_FONT_CANDIDATES = (
    (r"C:\Windows\Fonts\msyh.ttc", True),
    (r"C:\Windows\Fonts\simhei.ttf", True),
    (r"C:\Windows\Fonts\simsun.ttc", True),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", True),
    ("/System/Library/Fonts/STHeiti Medium.ttc", True),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", False),
)

_CHECK_ON = {"on", "yes", "true", "1", "是", "勾", "勾选", "选", "√", "✓", "对"}
_CHECK_OFF = {"off", "no", "false", "0", "否", "不勾", "不选", "不", "×", ""}

_font_cache = None


def has_reportlab() -> bool:
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


def _font() -> tuple:
    """(字体名, 能否显示中文)。候选全缺 → Helvetica（仅 latin-1）。"""
    global _font_cache
    if _font_cache is None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        _font_cache = ("Helvetica", False)
        for cand, cjk in _FONT_CANDIDATES:
            if not Path(cand).exists():
                continue
            try:
                kw = {"subfontIndex": 0} if cand.lower().endswith(".ttc") else {}
                pdfmetrics.registerFont(TTFont(FONT_NAME, cand, **kw))
                _font_cache = (FONT_NAME, cjk)
                break
            except Exception:
                continue
    return _font_cache


# ── 页序列 ───────────────────────────────────────────────────

def _sequence(n: int, ops: list) -> tuple:
    """→ (seq, rotations)。seq 元素 ("o", 原始页号) / ("i", src)。"""
    seq = [("o", i) for i in range(n)]
    for op in ops:
        if op["op"] == "page_reorder":
            seq = [("o", i) for i in op["order"]]
    for op in ops:
        if op["op"] != "page_insert":
            continue
        anchor = ("o", op["after"])
        if op["after"] < 0:
            pos = 0
        elif anchor in seq:
            pos = seq.index(anchor) + 1
        else:
            pos = len(seq)
        while pos < len(seq) and seq[pos][0] == "i":      # 同锚点多次插入保持先后
            pos += 1
        seq.insert(pos, ("i", op["src"]))
    deleted = {p for op in ops if op["op"] == "page_delete" for p in op["pages"]}
    seq = [e for e in seq if not (e[0] == "o" and e[1] in deleted)]
    rotations: dict = {}
    for op in ops:
        if op["op"] == "page_rotate":
            rotations[op["page"]] = (rotations.get(op["page"], 0) + op["deg"]) % 360
    return seq, rotations


def _build_writer(reader, seq: list, n: int, resolve_src) -> tuple:
    """→ (writer, {原始页号: 新页号})。序列未变走整本 append（表单原样保留）。"""
    from pypdf import PdfWriter
    writer = PdfWriter()
    if seq == [("o", i) for i in range(n)]:
        writer.append(reader)
        return writer, {i: i for i in range(n)}
    index, run = {}, []

    def flush():
        if run:
            writer.append(reader, pages=list(run))
            run.clear()

    for kind, ref in seq:
        if kind == "o":
            index.setdefault(ref, len(writer.pages) + len(run))
            run.append(ref)
        else:
            flush()
            writer.append(pdf_layout.open_reader(resolve_src(ref)))
    flush()
    return writer, index


# ── 表单字段 ─────────────────────────────────────────────────

def _btn_state(field: dict, value: str, warnings: list):
    low = value.strip().lower()
    if low in _CHECK_ON:
        return field["options"][0]
    if low in _CHECK_OFF:
        return "/Off"
    state = value if value.startswith("/") else "/" + value
    if state in field["options"]:
        return state
    warnings.append(f"{field['name']}: 勾选框只接受 on/off 或 {' / '.join(field['options'])}，"
                    f"收到 {value}，已跳过")
    return None


def _fill_fields(writer, ops: list, layout: dict, warnings: list) -> None:
    fields = {f["name"]: f for f in layout["fields"]}
    fill = {}
    for op in ops:
        f = fields.get(op["name"])
        if f is None:
            continue
        if f["type"] == "checkbox":
            state = _btn_state(f, op["value"], warnings)
            if state is not None:
                fill[op["name"]] = state
            continue
        if f["type"] == "choice" and op["value"] and op["value"] not in f["options"]:
            warnings.append(f"{f['name']}: {op['value']} 不在选项 {' / '.join(f['options'])} 里")
        fill[op["name"]] = op["value"]
    if not fill:
        return
    for page in writer.pages:
        if "/Annots" in page:
            writer.update_page_form_field_values(page, fill, auto_regenerate=False)
    writer.set_need_appearances_writer(True)      # 让阅读器重算外观，值才显示


# ── 覆盖层 ───────────────────────────────────────────────────

def _fit(text: str, font: str, size: float, max_w: float) -> tuple:
    """→ (text, size, 是否截断)。先缩字到 MIN_FONT_PT，仍放不下再截断加 …。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    while size > MIN_FONT_PT and stringWidth(text, font, size) > max_w:
        size = max(size - 0.5, MIN_FONT_PT)
    if stringWidth(text, font, size) <= max_w:
        return text, size, False
    while text and stringWidth(text + "…", font, size) > max_w:
        text = text[:-1]
    return text + "…", size, True


def _draw_text(c, op, left, top, s, resolve_src, warnings):
    font, cjk = _font()
    lines = op["text"].split("\n")
    if not cjk:
        if any(ord(ch) > 255 for ch in op["text"]):
            warnings.append("无中文字体：非拉丁字符无法显示")
        if font == "Helvetica":
            lines = [ln.encode("latin-1", "replace").decode("latin-1") for ln in lines]
    box_w = op["w"] / s if op.get("w") else None
    box_h = op["h"] / s if op.get("h") else None
    if op.get("size"):
        size = float(op["size"])
    elif box_h:
        size = min(max(box_h * 0.9 / len(lines), AUTO_FONT_PT[0]), AUTO_FONT_PT[1])
    else:
        size = DEFAULT_FONT_PT
    if box_w:
        fitted = [_fit(ln, font, size, box_w) for ln in lines]
        size = min(f[1] for f in fitted)
        fitted = [_fit(ln, font, size, box_w) for ln in lines]
        lines = [f[0] for f in fitted]
        if any(f[2] for f in fitted):
            warnings.append(f"文字过长已截断: {op['text'][:12]}…")
    x, y_top = left + op["x"] / s, top - op["y"] / s
    if box_h and len(lines) == 1:
        baseline = y_top - box_h / 2 - size * 0.35          # 框内垂直居中
    else:
        baseline = y_top - size * 0.85
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font, size)
    for i, ln in enumerate(lines):
        c.drawString(x, baseline - i * size * 1.2, ln)


def _draw_check(c, op, left, top, s, resolve_src, warnings):
    side = op["size"] / s
    x0, y1 = left + op["x"] / s, top - op["y"] / s
    pad = side * 0.15
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(max(side / 9, 0.8))
    c.line(x0 + pad, y1 - pad, x0 + side - pad, y1 - side + pad)
    c.line(x0 + pad, y1 - side + pad, x0 + side - pad, y1 - pad)


def _draw_erase(c, op, left, top, s, resolve_src, warnings):
    c.setFillColorRGB(1, 1, 1)
    c.rect(left + op["x"] / s, top - (op["y"] + op["h"]) / s, op["w"] / s, op["h"] / s,
           stroke=0, fill=1)


def _draw_image(c, op, left, top, s, resolve_src, warnings):
    from reportlab.lib.utils import ImageReader
    try:
        img = ImageReader(str(resolve_src(op["src"])))
        iw, ih = img.getSize()
    except Exception:
        warnings.append(f"图片读不出来，已跳过: {op['src']}")
        return
    w = op["w"] / s
    h = op["h"] / s if op.get("h") else w * ih / iw
    c.drawImage(img, left + op["x"] / s, top - op["y"] / s - h, w, h, mask="auto")


def _draw_line(c, op, left, top, s, resolve_src, warnings):
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(op["width"] / s)
    c.line(left + op["x1"] / s, top - op["y1"] / s, left + op["x2"] / s, top - op["y2"] / s)


_DRAW = {"text": _draw_text, "check": _draw_check, "erase": _draw_erase,
         "image": _draw_image, "line": _draw_line}


def _overlay(page, page_ops: list, scale: float, resolve_src, warnings: list) -> None:
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas
    if int(page.rotation or 0) % 360:
        if "/Annots" in page:
            warnings.append("旋转页上带表单/批注：转正后批注位置可能偏移")
        page.transfer_rotation_to_content()
    left, top = float(page.cropbox.left), float(page.cropbox.top)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(float(page.mediabox.right), float(page.mediabox.top)))
    for op in sorted(page_ops, key=lambda o: o["op"] != "erase"):    # 白底先画，字在上
        _DRAW[op["op"]](c, op, left, top, scale, resolve_src, warnings)
    c.showPage()          # 什么都没画成（如图片全坏）时 save() 不出页，merge 会越界
    c.save()
    buf.seek(0)
    page.merge_page(PdfReader(buf).pages[0])


# ── 入口 ─────────────────────────────────────────────────────

def apply(src_pdf: str, ops: list, layout: dict, out_pdf: str, resolve_src) -> list:
    """ops 应用到 src_pdf → out_pdf，返回警告。resolve_src: data 相对路径 → 绝对 Path。"""
    warnings: list = []
    reader = pdf_layout.open_reader(src_pdf)
    n = len(reader.pages)
    seq, rotations = _sequence(n, ops)
    if not seq:
        raise ValueError("不能把所有页都删掉")
    writer, index = _build_writer(reader, seq, n, resolve_src)

    _fill_fields(writer, [o for o in ops if o["op"] == "field"], layout, warnings)

    by_page: dict = {}
    for op in ops:
        if op["op"] in _DRAW:
            by_page.setdefault(op["page"], []).append(op)
    for orig, page_ops in by_page.items():
        if orig not in index:
            warnings.append(f"第 {orig + 1} 页已被删除，其上的 {len(page_ops)} 处编辑已丢弃")
            continue
        _overlay(writer.pages[index[orig]], page_ops,
                 layout["pages"][orig]["scale"], resolve_src, warnings)
    if any(op["op"] == "erase" for ops_ in by_page.values() for op in ops_):
        warnings.append(ERASE_WARNING)

    for orig, deg in rotations.items():
        if orig in index and deg:
            writer.pages[index[orig]].rotate(deg)

    Path(out_pdf).parent.mkdir(parents=True, exist_ok=True)
    with open(out_pdf, "wb") as fh:
        writer.write(fh)
    return warnings
