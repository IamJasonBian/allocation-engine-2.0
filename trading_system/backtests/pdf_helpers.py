"""
Shared PDF utilities for backtest report generation.

Provides a styled FPDF wrapper and formatting helpers used by both
the pairwise DCA report and the strategy comparison report.
"""

from pathlib import Path
from typing import List


class PaperPDF:
    """Thin wrapper around FPDF with header/footer."""

    def __init__(self, title: str = "Backtest Analysis"):
        from fpdf import FPDF

        _title = title

        class _PDF(FPDF):
            def header(self_inner):
                if self_inner.page_no() > 1:
                    self_inner.set_font("Helvetica", "I", 8)
                    self_inner.set_text_color(128, 128, 128)
                    self_inner.cell(0, 8, _title,
                                    new_x="LMARGIN", new_y="NEXT", align="C")
                    self_inner.set_text_color(0, 0, 0)
                    self_inner.ln(2)

            def footer(self_inner):
                self_inner.set_y(-15)
                self_inner.set_font("Helvetica", "I", 8)
                self_inner.set_text_color(128, 128, 128)
                self_inner.cell(0, 10, f"Page {self_inner.page_no()}/{{nb}}",
                                align="C")
                self_inner.set_text_color(0, 0, 0)

        self._pdf = _PDF()
        self._pdf.alias_nb_pages()
        self._pdf.set_margins(20, 15, 20)

    def __getattr__(self, name):
        return getattr(self._pdf, name)


def safe_text(text: str) -> str:
    """Replace Unicode characters unsupported by core PDF fonts."""
    return (text
            .replace("\u2014", "--")   # em-dash
            .replace("\u2013", "-")    # en-dash
            .replace("\u2018", "'")    # left single quote
            .replace("\u2019", "'")    # right single quote
            .replace("\u201c", '"')    # left double quote
            .replace("\u201d", '"')    # right double quote
            .replace("\u2026", "...")  # ellipsis
            .replace("\u2264", "<=")   # less-than-or-equal
            .replace("\u2265", ">="))  # greater-than-or-equal


def pdf_section(pdf, title: str):
    """Render a section heading."""
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, safe_text(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(41, 128, 185)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(3)


def pdf_subsection(pdf, title: str):
    """Render a subsection heading."""
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, safe_text(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def pdf_body(pdf, text: str):
    """Render a body paragraph with word wrapping."""
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, safe_text(text))
    pdf.ln(2)


def pdf_bullet(pdf, text: str):
    """Render a bullet-point line."""
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(5)  # indent
    pdf.multi_cell(0, 5, safe_text(f"  {text}"))
    pdf.ln(1)


def pdf_table_header(pdf, headers: List[str], widths: List[float]):
    """Render a table header row."""
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(41, 128, 185)
    pdf.set_text_color(255, 255, 255)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7, safe_text(h), border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_text_color(0, 0, 0)


def pdf_table_row(pdf, cells: List[str], widths: List[float]):
    """Render a table data row."""
    pdf.set_font("Helvetica", "", 9)
    for c, w in zip(cells, widths):
        pdf.cell(w, 6, safe_text(c), border=1, align="C")
    pdf.ln()


def pdf_embed_chart(pdf, chart_path: Path, max_width: float = 170):
    """Embed a PNG chart, adding a page break if needed."""
    if pdf.get_y() > 200:
        pdf.add_page()
    pdf.image(str(chart_path), x=20, w=max_width)
    pdf.ln(5)
