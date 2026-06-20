# tests/test_ocr.py — OCR module (PDF support).
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
