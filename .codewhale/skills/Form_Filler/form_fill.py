"""
Family Assistant — AcroForm（可填写 PDF）扫描与填写（pypdf）。

依赖 pypdf（可选依赖）：缺席时 is_available()=False，调用方（cli.py）
给 pip install 提示，绝不崩溃。输出保持矢量（tier 1）。
"""

from __future__ import annotations


def _import_pypdf():
    try:
        from pypdf import PdfReader, PdfWriter
        return PdfReader, PdfWriter
    except ImportError:
        return None


def is_available() -> bool:
    return _import_pypdf() is not None


def _open(pdf_path: str):
    PdfReader, _ = _import_pypdf()
    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("PDF 已加密，无法读取")
        if reader.is_encrypted:
            raise ValueError("PDF 已加密，无法读取")
    return reader


def _on_state(field) -> str:
    states = [str(s) for s in (field.get("/_States_") or []) if str(s) != "/Off"]
    return states[0] if states else "/Yes"


def scan_fields(pdf_path: str) -> list:
    """读 AcroForm 字段 → [{"name","label","type","options"}]。

    [] = 无表单字段（平面表候选）。签名域（/Sig）跳过。
    /Ch 没有 /Opt 选项时按 text 处理（没得选就当自由填写）。
    加密不可解 → ValueError("PDF 已加密，无法读取")。
    """
    reader = _open(pdf_path)
    raw = reader.get_fields() or {}
    fields = []
    for name, f in raw.items():
        ft = str(f.get("/FT") or "")
        if ft == "/Tx":
            fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                           "type": "text", "options": []})
        elif ft == "/Btn":
            fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                           "type": "checkbox", "options": [_on_state(f)]})
        elif ft == "/Ch":
            opts = [str(o) for o in (f.get("/Opt") or [])]
            if opts:
                fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                               "type": "choice", "options": opts})
            else:
                fields.append({"name": str(name), "label": str(f.get("/TU") or name),
                               "type": "text", "options": []})
    return fields


def render(pdf_path: str, values: dict, out_path: str) -> None:
    """填值输出。values: 字段名→字符串；checkbox 用 "on"/"off"；空值跳过。

    NeedAppearances=True 让阅读器重算外观，填的值才会显示。
    """
    _, PdfWriter = _import_pypdf()
    reader = _open(pdf_path)
    raw = reader.get_fields() or {}
    fill = {}
    for name, v in values.items():
        f = raw.get(name)
        if f is None or v in (None, ""):
            continue
        if str(f.get("/FT") or "") == "/Btn":
            fill[name] = _on_state(f) if v == "on" else "/Off"
        else:
            fill[name] = str(v)
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        if "/Annots" in page:
            writer.update_page_form_field_values(page, fill, auto_regenerate=False)
    try:
        writer.set_need_appearances_writer(True)
    except AttributeError:
        from pypdf.generic import BooleanObject, NameObject
        writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
