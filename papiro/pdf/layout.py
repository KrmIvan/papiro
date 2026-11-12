"""Análisis de la maqueta: regiones de texto y tablas en orden de lectura.

Dos motores:

- ``docling`` (modo calidad): modelos de maqueta y de tablas de docling (MIT).
- ``rapido`` (modo rápido): cortes XY por huecos en blanco + tablas con líneas
  detectadas por pdfplumber. Sin modelos, sin descargas.
"""

from __future__ import annotations

import logging
import os
import tempfile
import warnings
from dataclasses import dataclass, field
from statistics import median

from .glyphs import Glyph, PageText


@dataclass
class Cell:
    row: int
    col: int
    x0: float
    y0: float
    x1: float
    y1: float
    rowspan: int = 1
    colspan: int = 1
    header: bool = False


@dataclass
class Region:
    label: str  # text, section_header, title, list_item, table, picture, page_header, page_footer, ...
    x0: float
    y0: float
    x1: float
    y1: float
    cells: list[Cell] = field(default_factory=list)
    nrows: int = 0
    ncols: int = 0

    @property
    def area(self) -> float:
        return max(self.x1 - self.x0, 0) * max(self.y1 - self.y0, 0)

    def contains(self, x: float, y: float, pad: float = 1.0) -> bool:
        return self.x0 - pad <= x <= self.x1 + pad and self.y0 - pad <= y <= self.y1 + pad


# ---------------------------------------------------------------------------
# Modo rápido: cortes XY
# ---------------------------------------------------------------------------


def _gaps(intervals: list[tuple[float, float]], min_gap: float) -> list[tuple[float, float]]:
    intervals.sort()
    out = []
    end = intervals[0][1]
    for a, b in intervals[1:]:
        if a - end >= min_gap:
            out.append((end, a))
        end = max(end, b)
    return out


def xy_cut(glyphs: list[Glyph], depth: int = 0) -> list[list[Glyph]]:
    """Divide la página en bloques, en orden de lectura (columnas de izquierda a derecha)."""
    glyphs = [g for g in glyphs if g.text.strip()]
    if not glyphs:
        return []
    if len(glyphs) < 2 or depth > 12:
        return [glyphs]
    size = median(g.size for g in glyphs)
    height = max(g.y1 for g in glyphs) - min(g.y0 for g in glyphs)
    # Cortes verticales (columnas): hueco ancho, o estrecho pero alto
    xg = _gaps([(g.x0, g.x1) for g in glyphs], 1.1 * size)
    xg = [gp for gp in xg if (gp[1] - gp[0]) >= 2.2 * size or height >= 3.2 * size]
    yg = _gaps([(g.y0, g.y1) for g in glyphs], 1.7 * size)
    if xg:
        cuts = [(a + b) / 2 for a, b in xg]
        parts: list[list[Glyph]] = [[] for _ in range(len(cuts) + 1)]
        for g in glyphs:
            parts[sum(g.cx > c for c in cuts)].append(g)
        out = []
        for part in parts:
            out.extend(xy_cut(part, depth + 1))
        return out
    if yg:
        cuts = [(a + b) / 2 for a, b in yg]
        parts = [[] for _ in range(len(cuts) + 1)]
        for g in glyphs:
            parts[sum(g.cy > c for c in cuts)].append(g)
        out = []
        for part in parts:
            out.extend(xy_cut(part, depth + 1))
        return out
    # Sin cortes claros. Si un hueco horizontal pequeño separa una zona a varias columnas
    # (p. ej. un título pegado encima de dos columnas), se corta por ahí.
    for a, b in _gaps([(g.y0, g.y1) for g in glyphs], 0.45 * size):
        cut = (a + b) / 2
        top = [g for g in glyphs if g.cy <= cut]
        bottom = [g for g in glyphs if g.cy > cut]
        for part in (top, bottom):
            if len(part) < 8:
                continue
            ph = max(g.y1 for g in part) - min(g.y0 for g in part)
            pg = _gaps([(g.x0, g.x1) for g in part], 1.1 * size)
            if any((q[1] - q[0]) >= 2.2 * size or ph >= 3.2 * size for q in pg):
                return xy_cut(top, depth + 1) + xy_cut(bottom, depth + 1)
    return [glyphs]


def _bbox(glyphs: list[Glyph]) -> tuple[float, float, float, float]:
    return (min(g.x0 for g in glyphs), min(g.y0 for g in glyphs), max(g.x1 for g in glyphs), max(g.y1 for g in glyphs))


def ruled_tables(plumber_page) -> list[Region]:
    """Tablas dibujadas con líneas, detectadas por pdfplumber."""
    regions = []
    try:
        tables = plumber_page.find_tables()
    except Exception:
        return regions
    for t in tables:
        rows = t.rows
        ncols = max((len(r.cells) for r in rows), default=0)
        if len(rows) < 2 or ncols < 2:
            continue
        reg = Region("table", t.bbox[0], t.bbox[1], t.bbox[2], t.bbox[3], nrows=len(rows), ncols=ncols)
        for ri, row in enumerate(rows):
            for ci, c in enumerate(row.cells):
                if c is None:
                    continue
                reg.cells.append(Cell(ri, ci, c[0], c[1], c[2], c[3], header=(ri == 0)))
        regions.append(reg)
    return regions


def fast_layout(page: PageText, tables: list[Region] | None = None) -> list[Region]:
    tables = tables or []
    free = [g for g in page.glyphs if not any(t.contains(g.cx, g.cy) for t in tables)]
    regions: list[Region] = []
    for part in xy_cut(free):
        x0, y0, x1, y1 = _bbox(part)
        regions.append(Region("text", x0, y0, x1, y1))
    # Colocar cada tabla en su sitio dentro del orden de lectura
    for t in sorted(tables, key=lambda r: r.y0):
        idx = len(regions)
        for i, r in enumerate(regions):
            overlap_x = min(r.x1, t.x1) - max(r.x0, t.x0)
            if r.label != "table" and overlap_x > 0 and r.y0 >= t.y0:
                idx = i
                break
        regions.insert(idx, t)
    return regions


# ---------------------------------------------------------------------------
# Modo calidad: docling
# ---------------------------------------------------------------------------


def docling_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("docling") is not None


class DoclingLayout:
    """Envuelve docling para obtener regiones y tablas por página."""

    def __init__(self) -> None:
        self._converter = None

    def _get(self):
        if self._converter is None:
            for name in ("docling", "docling_core", "docling_ibm_models", "docling_parse", "RapidOCR", "transformers"):
                logging.getLogger(name).setLevel(logging.ERROR)
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption

                opts = PdfPipelineOptions()
                opts.do_ocr = False  # el OCR lo hace Papiro, con soporte de japonés
                opts.do_table_structure = True
                self._converter = DocumentConverter(
                    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
                )
        return self._converter

    def preload(self) -> None:
        """Descarga los modelos (solo la primera vez)."""
        self._get().initialize_pipeline(self._input_format())

    @staticmethod
    def _input_format():
        from docling.datamodel.base_models import InputFormat

        return InputFormat.PDF

    def analyze(self, pdf_path: str, first: int, last: int) -> dict[int, list[Region]]:
        """Regiones de las páginas ``first``..``last`` (empezando en 1), en orden de lectura."""
        conv = self._get()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = conv.convert(pdf_path, page_range=(first, last), raises_on_error=True)
        doc = res.document
        heights = {no: p.size.height for no, p in doc.pages.items()}
        out: dict[int, list[Region]] = {n: [] for n in range(first, last + 1)}
        for item, _level in doc.iterate_items():
            label = getattr(item.label, "value", str(item.label))
            for prov in getattr(item, "prov", None) or []:
                h = heights.get(prov.page_no)
                if h is None:
                    continue
                bb = prov.bbox.to_top_left_origin(page_height=h)
                reg = Region(label, bb.l, bb.t, bb.r, bb.b)
                if label == "table" and getattr(item, "data", None) is not None:
                    reg.nrows, reg.ncols = item.data.num_rows, item.data.num_cols
                    for c in item.data.table_cells:
                        if c.bbox is None:
                            continue
                        cb = c.bbox.to_top_left_origin(page_height=h)
                        reg.cells.append(
                            Cell(
                                c.start_row_offset_idx, c.start_col_offset_idx, cb.l, cb.t, cb.r, cb.b,
                                rowspan=max(1, c.end_row_offset_idx - c.start_row_offset_idx),
                                colspan=max(1, c.end_col_offset_idx - c.start_col_offset_idx),
                                header=bool(getattr(c, "column_header", False)),
                            )
                        )
                out.setdefault(prov.page_no, []).append(reg)
        return out


def placeholder_pdf(pages: list[tuple[PageText, object]], path: str) -> None:
    """PDF auxiliar para docling: imagen de cada página + texto invisible de relleno.

    Docling solo conserva las regiones que contienen texto. En las páginas con
    OCR se le da la imagen y una «x» invisible por cada carácter reconocido; el
    texto real lo pone después Papiro.
    """
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path)
    for page, img in pages:
        c.setPageSize((page.width, page.height))
        c.drawImage(ImageReader(img), 0, 0, width=page.width, height=page.height)
        t = c.beginText()
        t.setTextRenderMode(3)  # invisible
        for g in page.glyphs:
            if not g.text.strip():
                continue
            fs = max(min(g.y1 - g.y0, g.x1 - g.x0, 40.0) * 0.9, 2.0)
            t.setFont("Helvetica", fs)
            t.setTextOrigin(g.x0, page.height - g.y1 + 0.15 * fs)
            t.textOut("x")
        c.drawText(t)
        c.showPage()
    c.save()


def temp_pdf_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="papiro_")
    os.close(fd)
    return path
