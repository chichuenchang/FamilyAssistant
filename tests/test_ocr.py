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
