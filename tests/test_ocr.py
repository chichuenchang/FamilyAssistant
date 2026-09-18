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
