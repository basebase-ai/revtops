"""
PDF generation service for artifacts.

Converts markdown content to PDF using WeasyPrint.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import markdown
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration

logger = logging.getLogger(__name__)

# Default CSS for PDF documents
DEFAULT_PDF_CSS: str = """
@page {
    size: A4;
    margin: 2cm;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
}

h1 {
    font-size: 24pt;
    font-weight: 600;
    margin-top: 0;
    margin-bottom: 16pt;
    color: #111;
    border-bottom: 1px solid #e5e5e5;
    padding-bottom: 8pt;
}

h2 {
    font-size: 18pt;
    font-weight: 600;
    margin-top: 24pt;
    margin-bottom: 12pt;
    color: #222;
}

h3 {
    font-size: 14pt;
    font-weight: 600;
    margin-top: 20pt;
    margin-bottom: 8pt;
    color: #333;
}

h4, h5, h6 {
    font-size: 12pt;
    font-weight: 600;
    margin-top: 16pt;
    margin-bottom: 8pt;
}

p {
    margin-top: 0;
    margin-bottom: 12pt;
}

ul, ol {
    margin-top: 0;
    margin-bottom: 12pt;
    padding-left: 24pt;
}

li {
    margin-bottom: 4pt;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 12pt;
    margin-bottom: 16pt;
    font-size: 10pt;
}

th, td {
    padding: 8pt 12pt;
    text-align: left;
    border: 1px solid #d0d0d0;
}

th {
    background-color: #f5f5f5;
    font-weight: 600;
}

tr:nth-child(even) td {
    background-color: #fafafa;
}

code {
    font-family: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", monospace;
    font-size: 9pt;
    background-color: #f5f5f5;
    padding: 2pt 4pt;
    border-radius: 3pt;
}

pre {
    font-family: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", monospace;
    font-size: 9pt;
    background-color: #f5f5f5;
    padding: 12pt;
    border-radius: 4pt;
    overflow-x: auto;
    margin-top: 12pt;
    margin-bottom: 16pt;
}

pre code {
    background-color: transparent;
    padding: 0;
}

blockquote {
    margin: 12pt 0;
    padding: 8pt 16pt;
    border-left: 4px solid #e0e0e0;
    background-color: #fafafa;
    color: #555;
}

hr {
    border: none;
    border-top: 1px solid #e5e5e5;
    margin: 24pt 0;
}

a {
    color: #0066cc;
    text-decoration: none;
}

strong {
    font-weight: 600;
}

em {
    font-style: italic;
}
"""


def markdown_to_html(markdown_content: str) -> str:
    """
    Convert markdown content to HTML.
    
    Args:
        markdown_content: Markdown-formatted text
        
    Returns:
        HTML string
    """
    # Configure markdown extensions for tables, fenced code, etc.
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "codehilite",
            "toc",
            "nl2br",
            "sane_lists",
        ],
        extension_configs={
            "codehilite": {
                "css_class": "highlight",
                "guess_lang": False,
            },
        },
    )
    
    html_content: str = md.convert(markdown_content)
    
    # Wrap in basic HTML structure
    full_html: str = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
{html_content}
</body>
</html>"""
    
    return full_html


def generate_pdf(
    markdown_content: str,
    custom_css: Optional[str] = None,
) -> bytes:
    """
    Generate a PDF from markdown content.
    
    Args:
        markdown_content: Markdown-formatted text to convert
        custom_css: Optional additional CSS to apply
        
    Returns:
        PDF content as bytes
    """
    logger.info("[PDFGenerator] Starting PDF generation")
    
    # Convert markdown to HTML
    html_content: str = markdown_to_html(markdown_content)
    
    # Combine default CSS with any custom CSS
    css_content: str = DEFAULT_PDF_CSS
    if custom_css:
        css_content += "\n" + custom_css
    
    # Configure fonts
    font_config = FontConfiguration()
    
    # Create HTML and CSS objects
    html_doc = HTML(string=html_content)
    css_doc = CSS(string=css_content, font_config=font_config)
    
    # Generate PDF
    pdf_buffer = io.BytesIO()
    html_doc.write_pdf(
        pdf_buffer,
        stylesheets=[css_doc],
        font_config=font_config,
    )
    
    pdf_bytes: bytes = pdf_buffer.getvalue()
    logger.info("[PDFGenerator] Generated PDF: %d bytes", len(pdf_bytes))
    
    return pdf_bytes


def generate_pdf_from_html(
    html_content: str,
    custom_css: Optional[str] = None,
) -> bytes:
    """
    Generate a PDF from raw HTML content.
    
    Args:
        html_content: HTML to convert
        custom_css: Optional additional CSS to apply
        
    Returns:
        PDF content as bytes
    """
    logger.info("[PDFGenerator] Generating PDF from HTML")
    
    # Combine default CSS with any custom CSS
    css_content: str = DEFAULT_PDF_CSS
    if custom_css:
        css_content += "\n" + custom_css
    
    # Configure fonts
    font_config = FontConfiguration()
    
    # Create HTML and CSS objects
    html_doc = HTML(string=html_content)
    css_doc = CSS(string=css_content, font_config=font_config)
    
    # Generate PDF
    pdf_buffer = io.BytesIO()
    html_doc.write_pdf(
        pdf_buffer,
        stylesheets=[css_doc],
        font_config=font_config,
    )
    
    pdf_bytes: bytes = pdf_buffer.getvalue()
    logger.info("[PDFGenerator] Generated PDF from HTML: %d bytes", len(pdf_bytes))
    
    return pdf_bytes
