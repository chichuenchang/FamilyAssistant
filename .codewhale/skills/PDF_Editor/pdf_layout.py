"""
Family Assistant — PDF 版面提取（PDF_Editor）。

版面空间：视觉方向（已计 /Rotate）、左上原点、像素 = point × SCALE。
坐标来源三种：AcroForm 字段 /Rect（pypdf）、文字层行框（pypdfium2）、
无文字层的页渲染后 OCR（OCR skill）。后两者缺依赖 → notes，不崩。

pdfium 的文字框在**未旋转**的用户空间里，旋转页须经 to_visual 换到视觉空间；
渲染/OCR 出来的坐标天然是视觉空间。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

SCALE = 2.0
VERSION = 2                # 版面缓存格式；改了 build 输出就 +1，旧会话缓存自动重取
MAX_LAYOUT_PAGES = 12     # 取文字/OCR 的页数上限（页级操作不受限）
MAX_LINES = 1600          # 全书进 LLM 的版面行上限，各页均分（为何均分：SKILL.md 踩过的坑）
MAX_PAGE_LINES = 400      # 均分后单页仍不超此数
MIN_TEXT_CHARS = 5        # 一页文字层少于此 → 当扫描页
WORD_GAP = 0.25           # 字间距 > 行高 × 此值 → 补空格
LINE_BREAK = 1.2          # 字间距 > 行高 × 此值 → 另起一行（跨栏/跨格）
CHECK_GLYPHS = "☐□❑❏⬜▢◻"
ENCRYPTED = "PDF 已加密，无法读取"


def has_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


def has_pdfium() -> bool:
    try:
        import pypdfium2  # noqa: F401
        return True
    except ImportError:
        return False


def has_numpy() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def open_reader(pdf_path):
    """空口令可解的加密 PDF 放行；其余加密 → ValueError(ENCRYPTED)。"""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise ValueError(ENCRYPTED)
    return reader


def page_geometry(reader) -> list:
    out = []
    for i, p in enumerate(reader.pages):
        l, b, r, t = (float(v) for v in p.cropbox)
        rot = int(p.rotation or 0) % 360
        w, h = (r - l, t - b) if rot in (0, 180) else (t - b, r - l)
        out.append({"page": i, "width": round(w * SCALE), "height": round(h * SCALE),
                    "scale": SCALE, "box": [l, b, r, t], "rotate": rot})
    return out


def to_visual(rect, box, rotate) -> dict:
    """用户空间 rect (l0,b0,r0,t0) → 视觉空间左上原点像素框。"""
    l0, r0 = sorted((rect[0], rect[2]))
    b0, t0 = sorted((rect[1], rect[3]))
    l, b, r, t = box
    if rotate == 90:
        x, y, w, h = b0 - b, l0 - l, t0 - b0, r0 - l0
    elif rotate == 180:
        x, y, w, h = r - r0, b0 - b, r0 - l0, t0 - b0
    elif rotate == 270:
        x, y, w, h = t - t0, r - r0, t0 - b0, r0 - l0
    else:
        x, y, w, h = l0 - l, t - t0, r0 - l0, t0 - b0
    return {"x": round(x * SCALE), "y": round(y * SCALE),
            "w": round(w * SCALE), "h": round(h * SCALE)}


def _obj(x):
    return x.get_object() if hasattr(x, "get_object") else x


def _qualified(annot) -> str:
    parts, node = [], annot
    while node is not None:
        t = node.get("/T")
        if t is not None:
            parts.append(str(t))
        node = _obj(node.get("/Parent"))
    return ".".join(reversed(parts))


def _widget_state(annot) -> str | None:
    ap = _obj(annot.get("/AP"))
    normal = _obj(ap.get("/N")) if ap is not None else None
    if normal is None or not hasattr(normal, "keys"):
        return None
    states = [str(k) for k in normal.keys() if str(k) != "/Off"]
    return states[0] if states else None


def acro_fields(reader, geometry) -> list:
    """AcroForm 字段 + 各 widget 的视觉像素框。签名域 / 按钮跳过；/Ch 无选项当 text。"""
    fields = {}
    for name, f in (reader.get_fields() or {}).items():
        ft = str(f.get("/FT") or "")
        entry = {"name": str(name), "label": str(f.get("/TU") or name),
                 "options": [], "widgets": []}
        if ft == "/Tx":
            entry["type"] = "text"
        elif ft == "/Btn":
            if int(f.get("/Ff") or 0) & (1 << 16):        # pushbutton
                continue
            states = [str(s) for s in (f.get("/_States_") or []) if str(s) != "/Off"]
            entry["type"], entry["options"] = "checkbox", states or ["/Yes"]
        elif ft == "/Ch":
            opts = [str(o[1]) if isinstance(o, list) and len(o) == 2 else str(o)
                    for o in (f.get("/Opt") or [])]
            entry["type"], entry["options"] = ("choice", opts) if opts else ("text", [])
        else:
            continue
        fields[entry["name"]] = entry
    for g in geometry:
        annots = _obj(reader.pages[g["page"]].get("/Annots")) or []
        for ref in annots:
            a = _obj(ref)
            if str(a.get("/Subtype")) != "/Widget" or a.get("/Rect") is None:
                continue
            f = fields.get(_qualified(a))
            if f is None:
                continue
            w = {"page": g["page"],
                 **to_visual([float(v) for v in a["/Rect"]], g["box"], g["rotate"])}
            if f["type"] == "checkbox":
                state = _widget_state(a)
                if state:
                    w["state"] = state
            f["widgets"].append(w)
    return list(fields.values())


def _chars(tp) -> list:
    """[(字, l, b, r, t)]，用户空间；空白与零面积字形跳过。"""
    out = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch or ch.isspace():
            continue
        l, b, r, t = tp.get_charbox(i)
        if r > l and t > b:
            out.append((ch, l, b, r, t))
    return out


def group_lines(chars) -> tuple:
    """逐字框 → (行 [[文字, l, b, r, t]], 勾选框字形 [(l, b, r, t)])，用户空间。
    不用 pdfium get_rect（为何见 SKILL.md 踩过的坑）。
    勾选框字形单独拿出、不进行：它右边的字才成独立一行，好当它的标签。"""
    lines, checks = [], []
    for ch, l, b, r, t in sorted(chars, key=lambda c: c[1]):
        if ch in CHECK_GLYPHS:
            checks.append((l, b, r, t))
            continue
        best, best_gap = None, None
        for g in lines:
            h = max(g[4] - g[2], t - b)
            overlap = min(g[4], t) - max(g[2], b)
            gap = l - g[3]
            if overlap >= 0.5 * min(g[4] - g[2], t - b) and -0.3 * h <= gap <= LINE_BREAK * h:
                if best is None or gap < best_gap:
                    best, best_gap = g, gap
        if best is None:
            lines.append([ch, l, b, r, t])
            continue
        if best_gap > WORD_GAP * max(best[4] - best[2], t - b):
            best[0] += " "
        best[0] += ch
        best[1:] = [min(best[1], l), min(best[2], b), max(best[3], r), max(best[4], t)]
    return lines, checks


def _pdfium_lines(pdf_path, geometry) -> tuple:
    """文字层 → ({页: 行}, 没文字的页号, {页: 勾选框字形框})，均视觉像素。"""
    import pypdfium2 as pdfium
    lines, textless, checks = {}, [], {}
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for g in geometry[:MAX_LAYOUT_PAGES]:
            chars = _chars(pdf[g["page"]].get_textpage())
            if len(chars) < MIN_TEXT_CHARS:
                textless.append(g["page"])
                continue
            grouped, boxes = group_lines(chars)
            out = [{"text": text, **to_visual(rect, g["box"], g["rotate"])}
                   for text, *rect in grouped]
            out.sort(key=lambda l: (l["y"], l["x"]))
            lines[str(g["page"])] = out
            if boxes:
                checks[str(g["page"])] = [to_visual(b, g["box"], g["rotate"]) for b in boxes]
    finally:
        pdf.close()
    return lines, textless, checks


def _ocr_lines(pdf_path, pages) -> tuple:
    """扫描页渲染 PNG（临时目录，用完即删）→ OCR 行框。返回 ({页: 行}, 失败页号)。"""
    import ocr
    import pypdfium2 as pdfium
    got, failed = {}, []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for i in pages:
                png = Path(tmp) / f"page_{i}.png"
                pdf[i].render(scale=SCALE).to_pil().convert("RGB").save(png)
                words = ocr.ocr_image_words(str(png))
                if words is None:
                    failed.append(i)
                else:
                    got[str(i)] = words
    finally:
        pdf.close()
    return got, failed


def _page_list(pages) -> str:
    return "、".join(str(p + 1) for p in pages)


def build(pdf_path) -> dict:
    reader = open_reader(pdf_path)
    geometry = page_geometry(reader)
    fields = acro_fields(reader, geometry)
    lines, textless, checks, notes = {}, [], {}, []
    if has_pdfium():
        lines, textless, checks = _pdfium_lines(pdf_path, geometry)
    else:
        notes.append("缺 pypdfium2：取不到版面文字坐标（表单字段与页级操作仍可用）。"
                     "pip install pypdfium2")
    if textless:                          # 扫描表单带几个字段也要 OCR：字段只给自身框
        import ocr
        if ocr.is_available():
            got, failed = _ocr_lines(pdf_path, textless)
            lines.update(got)
            if failed:
                notes.append(f"第 {_page_list(failed)} 页 OCR 失败（额度/网络），该页无版面坐标")
        else:
            notes.append(f"第 {_page_list(textless)} 页是扫描页，需腾讯云 OCR 定位"
                         "（配置 TENCENT_SECRET_ID/TENCENT_SECRET_KEY），该页暂无版面坐标")
    if len(geometry) > MAX_LAYOUT_PAGES:
        notes.append(f"只取了前 {MAX_LAYOUT_PAGES} 页的版面文字（共 {len(geometry)} 页）")
    targets = {}
    if has_pdfium() and not fields:                 # 有 AcroForm 就走 field，不找格子
        import pdf_boxes
        if has_numpy():
            targets = pdf_boxes.page_targets(pdf_path, geometry[:MAX_LAYOUT_PAGES],
                                             lines, checks)
        else:
            notes.append("缺 numpy：找不到表格空格/勾选框，只能按文字坐标估位置。pip install numpy")
    kind = "acroform" if fields else ("scanned" if textless else "digital")
    return {"version": VERSION, "kind": kind, "pages": geometry, "fields": fields,
            "lines": lines, "targets": targets, "notes": notes}


def _box(b: dict) -> str:
    return f"[x={b['x']},y={b['y']},w={b['w']},h={b['h']}]"


def _field_line(f: dict) -> str:
    # name 带引号单列：实测排版模型会把标签当字段名填进 field op
    kind = f["type"] + (f"[{' / '.join(f['options'])}]" if f["options"] else "")
    return f'字段 name="{f["name"]}" | 标签: {f["label"]} | {kind}'


def describe(layout: dict, coords: bool = True) -> str:
    """版面 → 文本。coords=True 给排版 LLM；False 给 Agent 看这份 PDF 要填什么。"""
    out = [f"类型: {layout['kind']} | 页数: {len(layout['pages'])}"]
    out += [f"注意: {n}" for n in layout["notes"]]
    placed: dict = {}
    for f in layout["fields"]:
        if not f["widgets"]:
            out.append(_field_line(f))
        for w in f["widgets"]:
            placed.setdefault(w["page"], []).append((f, w))
    room = min(MAX_PAGE_LINES, MAX_LINES // max(len(layout["lines"]), 1))
    for g in layout["pages"]:
        fl = placed.get(g["page"], [])
        ll = layout["lines"].get(str(g["page"]), [])
        out.append(f"--- page={g['page']}（第 {g['page'] + 1} 页）{g['width']}x{g['height']} ---")
        for f, w in fl:
            state = f" state={w['state']}" if len(f["options"]) > 1 and "state" in w else ""
            out.append(_field_line(f) + state + (f" {_box(w)}" if coords else ""))
        for l in ll[:room]:
            out.append((f"{_box(l)} " if coords else "") + l["text"])
        if len(ll) > room:
            out.append("（本页其余文字行已截断）")
        if coords:
            out += [_target_line(t) for t in layout.get("targets", {}).get(str(g["page"]), [])]
    return "\n".join(out)


_KIND = {"blank": "空格", "check": "勾选框", "cell": "标签格"}
_SIDE = {"above": "上", "below": "下", "left": "左", "right": "右"}


def _target_line(t: dict) -> str:
    near = " ".join(f"{_SIDE[k]}:{v}" for k, v in (t.get("near") or {}).items())
    label = f" 格内:{t['label']}" if t.get("label") else ""
    return f"目标 {t['id']} {_KIND[t['kind']]} {_box(t)}{label} {near}".rstrip()
