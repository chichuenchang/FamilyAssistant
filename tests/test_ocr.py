# tests/test_ocr.py — OCR module (PDF support).
import os
import sys
from pathlib import Path

import ocr


def test_ocr_image_pdf_loops_pages_with_ispdf(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-fake")
    calls = []

    def fake(payload):
        calls.append(payload)
        n = payload["PdfPageNumber"]
        if n == 1:
            return {"TextDetections": [{"DetectedText": "page1"}]}
        if n == 2:
            return {"TextDetections": [{"DetectedText": "page2"}]}
        return None  # page 3 → out of range

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    out = ocr.ocr_image(str(f))
    assert out == "page1\npage2"
    assert all(c["IsPdf"] is True for c in calls)
    assert [c["PdfPageNumber"] for c in calls] == [1, 2, 3]


def test_ocr_image_pdf_page1_failure_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: None)
    assert ocr.ocr_image(str(f)) is None


def test_ocr_image_pdf_caps_at_max_pages(monkeypatch, tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"%PDF")
    seen = []

    def fake(payload):
        seen.append(payload["PdfPageNumber"])
        return {"TextDetections": [{"DetectedText": f"p{payload['PdfPageNumber']}"}]}

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    out = ocr.ocr_image(str(f))
    assert seen == list(range(1, ocr.MAX_PDF_PAGES + 1))   # never beyond the cap
    assert out.count("\n") == ocr.MAX_PDF_PAGES - 1         # all pages joined


def test_ocr_image_pdf_known_page_count_no_probe(monkeypatch, tmp_path):
    # 页数本地可读：只调实际页数，不再探测越界页（腾讯报 OcrFailed、白耗额度）
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(tmp_path / "two.pdf", pages=2)
    seen = []

    def fake(payload):
        seen.append(payload["PdfPageNumber"])
        return {"TextDetections": [{"DetectedText": f"p{payload['PdfPageNumber']}"}]}

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    assert ocr.ocr_image(str(f)) == "p1\np2"
    assert seen == [1, 2]


def test_ocr_image_pdf_known_count_skips_failed_middle_page(monkeypatch, tmp_path):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(tmp_path / "three.pdf", pages=3)

    def fake(payload):
        n = payload["PdfPageNumber"]
        return None if n == 2 else {"TextDetections": [{"DetectedText": f"p{n}"}]}

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    assert ocr.ocr_image(str(f)) == "p1\np3"


def test_ocr_image_image_path_has_no_ispdf(monkeypatch, tmp_path):
    f = tmp_path / "x.jpg"
    f.write_bytes(b"img")
    captured = {}

    def fake(payload):
        captured.update(payload)
        return {"TextDetections": [{"DetectedText": "hi"}]}

    monkeypatch.setattr(ocr, "_call_ocr", fake)
    assert ocr.ocr_image(str(f)) == "hi"
    assert "IsPdf" not in captured


def test_ocr_image_missing_file_returns_none():
    assert ocr.ocr_image("nope.pdf") is None


def test_ocr_extract_uses_ocr_image_for_pdf(monkeypatch, tmp_path):
    f = tmp_path / "stmt.pdf"
    f.write_bytes(b"%PDF")
    monkeypatch.setattr(ocr, "ocr_image", lambda path: "txn line 1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")   # 无 LLM → raw_text 透传，确认走 ocr_image
    assert ocr.ocr_extract(str(f)) == {"raw_text": "txn line 1"}


def test_ocr_image_words_item_polygon(monkeypatch, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: {"TextDetections": [
        {"DetectedText": "姓名", "ItemPolygon": {"X": 10, "Y": 20, "Width": 60, "Height": 18}},
        {"DetectedText": "", "ItemPolygon": {"X": 0, "Y": 0, "Width": 1, "Height": 1}},
    ]})
    assert ocr.ocr_image_words(str(f)) == [
        {"text": "姓名", "x": 10, "y": 20, "w": 60, "h": 18}]


def test_ocr_image_words_polygon_fallback(monkeypatch, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: {"TextDetections": [
        {"DetectedText": "地址", "Polygon": [
            {"X": 5, "Y": 8}, {"X": 105, "Y": 8}, {"X": 105, "Y": 28}, {"X": 5, "Y": 28}]},
    ]})
    assert ocr.ocr_image_words(str(f)) == [
        {"text": "地址", "x": 5, "y": 8, "w": 100, "h": 20}]


def test_ocr_image_words_unavailable(monkeypatch, tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: None)
    assert ocr.ocr_image_words(str(f)) is None
    assert ocr.ocr_image_words(str(tmp_path / "missing.png")) is None


# ── agent 工具：通用文字识别 ────────────────────────────────

import agent_core as ac
import paths as _paths

_ocr_at = ac.REGISTRY.modules["OCR"]


def test_ocr_read_returns_plain_text(monkeypatch, tmp_path):
    f = _paths.data_root() / "scan.jpg"
    f.write_bytes(b"jpg")
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_image", lambda p: "合同第一页")
    assert _ocr_at.tool_ocr_read({"path": str(f)}) == "合同第一页"


def test_ocr_read_refuses_path_outside_data_root(tmp_path):
    out = _ocr_at.tool_ocr_read({"path": str(tmp_path / "secret.png")})
    assert out.startswith("[错误]")


def test_ocr_read_registered_and_untrusted():
    names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
    assert "ocr_read" in names and "ocr_read" in ac._TOOL_MAP
    assert "ocr_read" in ac._UNTRUSTED_TOOLS


def test_ocr_extract_goes_through_llm_client(monkeypatch, tmp_path):
    import llm_client
    f = tmp_path / "r.png"
    f.write_bytes(b"img")
    monkeypatch.setattr(ocr, "ocr_image", lambda path: "COFFEE 5.00")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    seen = {}

    def chat(messages, tools, model, effort, **opts):
        seen.update(model=model, tools=tools, opts=opts, prompt=messages[-1]["content"])
        return {"content": 'ok ```{"currency": "USD", "transactions": []}```'}

    monkeypatch.setattr(llm_client, "chat", chat)
    assert ocr.ocr_extract(str(f)) == {"currency": "USD", "transactions": []}
    assert seen["model"] == "deepseek-flash" and seen["tools"] is None
    assert seen["opts"] == {"temperature": 0, "max_tokens": 10000, "timeout": 90}
    assert "COFFEE 5.00" in seen["prompt"]
    monkeypatch.setattr(llm_client, "chat", lambda *a, **k: None)
    assert ocr.ocr_extract(str(f)) == {"raw_text": "COFFEE 5.00"}


def test_cli_extract_standalone_process_imports_llm_client(tmp_path):
    # 独立进程、干净 sys.path（无 conftest bootstrap）：--extract 须自行挂路径才 import 得到 llm_client
    import subprocess
    img = tmp_path / "r.png"
    img.write_bytes(b"img")
    code = (
        f"import sys; sys.argv = ['ocr.py', {str(img)!r}, '--extract']\n"
        f"sys.path.insert(0, {str(Path(ocr.__file__).parent)!r})\n"
        "import ocr\n"
        "ocr.SECRET_ID = ocr.SECRET_KEY = 'x'\n"
        "ocr.ocr_image = lambda p: 'COFFEE 5.00'\n"
        "sys.exit(ocr.main())\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "LLM_MODEL", "DEEPSEEK_MODEL")}
    env["DEEPSEEK_API_KEY"] = ""   # 无 key → raw_text 透传，仍须经过 import llm_client
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert '"raw_text": "COFFEE 5.00"' in r.stdout


# ── 页段 OCR（ocr_pdf_range / ocr_read_pages）─────────────────

def _echo_pages(seen):
    def fake(payload):
        seen.append(payload["PdfPageNumber"])
        return {"TextDetections": [{"DetectedText": f"p{payload['PdfPageNumber']}"}]}
    return fake


def test_ocr_pdf_range_reads_beyond_page_20(monkeypatch, tmp_path):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(tmp_path / "big.pdf", pages=30)
    seen = []
    monkeypatch.setattr(ocr, "_call_ocr", _echo_pages(seen))
    r = ocr.ocr_pdf_range(str(f), 21, 30)
    assert seen == list(range(21, 31))
    assert r == {"total": 30, "first": 21, "last": 30, "failed": [],
                 "pages": [(n, f"p{n}") for n in range(21, 31)]}


def test_ocr_pdf_range_caps_window_and_clamps_to_total(monkeypatch, tmp_path):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(tmp_path / "big.pdf", pages=50)
    seen = []
    monkeypatch.setattr(ocr, "_call_ocr", _echo_pages(seen))
    assert ocr.ocr_pdf_range(str(f), 5, 100)["last"] == 5 + ocr.MAX_PDF_PAGES - 1
    assert len(seen) == ocr.MAX_PDF_PAGES
    seen.clear()
    assert ocr.ocr_pdf_range(str(f), 45, 60)["last"] == 50
    assert seen == list(range(45, 51))


def test_ocr_pdf_range_out_of_range_no_calls(monkeypatch, tmp_path):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(tmp_path / "two.pdf", pages=2)
    seen = []
    monkeypatch.setattr(ocr, "_call_ocr", _echo_pages(seen))
    r = ocr.ocr_pdf_range(str(f), 5, 8)
    assert r["pages"] == [] and seen == []


def test_ocr_pdf_range_probe_mode_stops_at_end(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-fake")   # 页数读不出 → 探测
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: (
        {"TextDetections": [{"DetectedText": "x"}]} if payload["PdfPageNumber"] <= 23 else None))
    r = ocr.ocr_pdf_range(str(f), 21, 30)
    assert r["total"] is None and r["last"] == 23
    assert [n for n, _ in r["pages"]] == [21, 22, 23]


def test_ocr_pdf_range_probe_first_page_failure_is_none(monkeypatch, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-fake")   # 页数读不出 → 探测；段首页失败不能报成越界
    seen = []
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: seen.append(1))
    assert ocr.ocr_pdf_range(str(f), 21, 30) is None
    assert len(seen) == 1


def test_ocr_pdf_range_all_failed_is_none(monkeypatch, tmp_path):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(tmp_path / "two.pdf", pages=2)
    monkeypatch.setattr(ocr, "_call_ocr", lambda payload: None)
    assert ocr.ocr_pdf_range(str(f), 1, 2) is None
    assert ocr.ocr_pdf_range(str(tmp_path / "x.jpg"), 1, 2) is None


def test_ocr_read_pages_tool_formats_pages(monkeypatch):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(_paths.data_root() / "manual.pdf", pages=30)
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "_call_ocr", _echo_pages([]))
    out = _ocr_at.tool_ocr_read_pages({"path": str(f), "first_page": 21, "last_page": 22})
    assert out.splitlines() == ["[第 21-22 页，共 30 页]；还有第 23-30 页未读",
                                "--- 第 21 页 ---", "p21", "--- 第 22 页 ---", "p22"]
    out = _ocr_at.tool_ocr_read_pages({"path": str(f), "first_page": 40, "last_page": 45})
    assert out.startswith("[页段越界]")


def test_ocr_read_pages_tool_lists_failed_pages(monkeypatch):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(_paths.data_root() / "gaps.pdf", pages=3)
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    echo = _echo_pages([])
    monkeypatch.setattr(ocr, "_call_ocr", lambda p: None if p["PdfPageNumber"] == 2 else echo(p))
    out = _ocr_at.tool_ocr_read_pages({"path": str(f), "first_page": 1, "last_page": 3})
    assert out.splitlines()[0] == "[第 1-3 页，共 3 页] 识别失败页: 2"


def test_ocr_read_pages_rejects_non_pdf_and_outside_root(tmp_path):
    img = _paths.data_root() / "scan.jpg"
    img.write_bytes(b"jpg")
    assert _ocr_at.tool_ocr_read_pages({"path": str(img), "first_page": 1,
                                        "last_page": 2}).startswith("[错误]")
    assert _ocr_at.tool_ocr_read_pages({"path": str(tmp_path / "a.pdf"), "first_page": 1,
                                        "last_page": 2}).startswith("[错误]")


def test_ocr_read_flags_pdf_over_20_pages(monkeypatch):
    from pdf_samples import build_digital_pdf
    f = build_digital_pdf(_paths.data_root() / "long.pdf", pages=25)
    monkeypatch.setattr(ocr, "is_available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_image", lambda p: "前文")
    out = _ocr_at.tool_ocr_read({"path": str(f)})
    assert out == "前文\n[仅读了前 20 页，共 25 页；用户要后面内容再用 ocr_read_pages]"


def test_ocr_read_pages_registered_and_untrusted():
    names = {t["function"]["name"] for t in ac.TOOL_SCHEMAS}
    assert "ocr_read_pages" in names and "ocr_read_pages" in ac._TOOL_MAP
    assert "ocr_read_pages" in ac._UNTRUSTED_TOOLS
