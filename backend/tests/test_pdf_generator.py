from services import pdf_generator


def test_generate_pdf_raises_helpful_error_when_weasyprint_missing(monkeypatch):
    monkeypatch.setattr(pdf_generator, "WEASYPRINT_IMPORT_ERROR", OSError("missing libgobject"))

    try:
        pdf_generator.generate_pdf("# hello")
        raise AssertionError("Expected RuntimeError when WeasyPrint is unavailable")
    except RuntimeError as exc:
        assert "WeasyPrint is unavailable" in str(exc)
        assert "installation" in str(exc)


def test_generate_pdf_from_html_raises_helpful_error_when_weasyprint_missing(monkeypatch):
    monkeypatch.setattr(pdf_generator, "WEASYPRINT_IMPORT_ERROR", OSError("missing libpango"))

    try:
        pdf_generator.generate_pdf_from_html("<h1>Hello</h1>")
        raise AssertionError("Expected RuntimeError when WeasyPrint is unavailable")
    except RuntimeError as exc:
        assert "WeasyPrint is unavailable" in str(exc)
        assert "troubleshooting" in str(exc)
