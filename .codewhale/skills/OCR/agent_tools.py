"""OCR 的 Agent manifest（契约见 Agent_Runtime/skill_registry.py）。

进程内直调 ocr.py（无 cli.py）。路径限 data_root 内，防任意本地文件外泄到云端 OCR。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Agent_Runtime")); import bootstrap  # noqa: E402,E702  挂全部 skill 目录

import paths as _paths
import tool_runtime as rt
from tool_runtime import fn, int_, s

ORDER = 15


def tool_ocr_image(args):
    resolved = rt.data_path_guard(args.get("path", ""))
    if resolved is None:
        return f"[错误] 只允许识别数据目录内的图片: {_paths.data_root().resolve()}"
    try:
        from ocr import ocr_extract, is_available
        if is_available():
            info = ocr_extract(str(resolved))
            return json.dumps(info, ensure_ascii=False) if info else "[未识别到文字]"
        return "[OCR 未配置]"
    except Exception as e:
        return f"[OCR 错误] {e}"


def tool_ocr_read(args):
    resolved = rt.data_path_guard(args.get("path", ""))
    if resolved is None:
        return f"[错误] 只允许识别数据目录内的文件: {_paths.data_root().resolve()}"
    try:
        from ocr import MAX_PDF_PAGES, pdf_page_count, ocr_image, is_available
        if not is_available():
            return "[OCR 未配置]"
        text = ocr_image(str(resolved))
        if text is None:
            return "[OCR 失败] 文件读不出文字（可能不是图片/PDF，或是加密扫描件）"
        text = text or "[未识别到文字]"
        total = pdf_page_count(resolved) if resolved.suffix.lower() == ".pdf" else None
        if total and total > MAX_PDF_PAGES:
            text += (f"\n[仅读了前 {MAX_PDF_PAGES} 页，共 {total} 页；"
                     f"用户要后面内容再用 ocr_read_pages]")
        return text
    except Exception as e:
        return f"[OCR 错误] {e}"


def tool_ocr_read_pages(args):
    resolved = rt.data_path_guard(args.get("path", ""))
    if resolved is None:
        return f"[错误] 只允许识别数据目录内的文件: {_paths.data_root().resolve()}"
    if resolved.suffix.lower() != ".pdf":
        return "[错误] 只支持 PDF；图片用 ocr_read"
    try:
        first = int(args.get("first_page") or 1)
        last = int(args.get("last_page") or first)
    except (TypeError, ValueError):
        return "[错误] first_page / last_page 须为整数"
    try:
        from ocr import ocr_pdf_range, is_available
        if not is_available():
            return "[OCR 未配置]"
        r = ocr_pdf_range(str(resolved), first, last)
        if r is None:
            return (f"[OCR 失败] 第 {max(1, first)} 页读不出文字"
                    "（可能是加密扫描件，或该页已超出总页数）")
        total = r["total"]
        of = f"共 {total} 页" if total else "总页数未知"
        if not r["pages"]:
            return f"[页段越界] 第 {r['first']} 页起无内容（{of}）"
        head = f"[第 {r['first']}-{r['last']} 页，{of}]"
        if r["failed"]:
            head += f" 识别失败页: {', '.join(map(str, r['failed']))}"
        if total and r["last"] < total:
            head += f"；还有第 {r['last'] + 1}-{total} 页未读"
        body = "\n".join(f"--- 第 {n} 页 ---\n{text or '（本页无文字）'}"
                         for n, text in r["pages"])
        return f"{head}\n{body}"
    except Exception as e:
        return f"[OCR 错误] {e}"


TOOLS = {"ocr_image": tool_ocr_image, "ocr_read": tool_ocr_read,
         "ocr_read_pages": tool_ocr_read_pages}

UNTRUSTED_TOOLS = set(TOOLS)

SCHEMAS = [
    fn("ocr_image", "OCR 识别票据/账单图片，逐笔提取交易明细（返回 transactions 数组，"
       "非账单总额）。拿到后逐笔调 add_transaction 记账", {
        "path": s("图片路径"),
    }, ["path"]),
    fn("ocr_read", "读任意图片/PDF 里的文字，返回原文（邮件附件、扫描件、截图、合同、"
       "说明书、通知信…）。要的是逐笔交易明细就改用 ocr_image；其余一切\"这文件里写了什么\""
       "都用本工具，拿到原文再自己判断怎么用。PDF 只读前 20 页", {
        "path": s("文件路径（如 download_attachment 返回的那行）"),
    }, ["path"]),
    fn("ocr_read_pages", "按页段读 PDF 文字（每页带页号），可读第 20 页之后。备用工具："
       "先用 ocr_read；仅当用户要的内容在前 20 页之外、或点名要某几页时才用。"
       "单次最多 20 页（每页耗一次 OCR 额度），更多就分段多次调用", {
        "path": s("PDF 路径"),
        "first_page": int_("起始页（1 起）"),
        "last_page": int_("结束页（含）；与起始页相差超 19 页时截到起始页+19"),
    }, ["path", "first_page", "last_page"]),
]
