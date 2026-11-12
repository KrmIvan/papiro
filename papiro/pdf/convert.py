"""Conversión de PDF a Markdown."""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from statistics import median
from typing import Callable

from ..textutil import clean_markdown, detect_language, is_cjk, join_lines
from . import ocr as ocr_mod
from .assemble import Block, _BULLET, _TERMINAL, cell_text, detect_ruby, paragraph_blocks
from .glyphs import (
    Glyph,
    PageText,
    drop_decorations,
    extract_page,
    is_vertical_page,
    normalize_glyphs,
    text_layer_quality,
)
from .layout import DoclingLayout, Region, docling_installed, fast_layout, placeholder_pdf, ruled_tables, temp_pdf_path


class ConversionCancelled(Exception):
    """El usuario canceló la conversión."""


@dataclass
class PdfOptions:
    mode: str = "auto"  # auto | calidad | rapido
    ocr: str = "auto"  # auto | siempre | nunca
    lang: str = "auto"  # auto | ja | es | en
    furigana: bool = True
    ocr_engine: str = "auto"  # auto | tesseract | rapidocr
    page_marks: bool = False
    progress: Callable[[int, int, str], None] | None = None
    cancel: Callable[[], bool] | None = None


@dataclass
class PdfResult:
    markdown: str
    warnings: list[str] = field(default_factory=list)
    pages: int = 0
    ocr_pages: list[int] = field(default_factory=list)
    mode: str = ""
    languages: dict[int, str] = field(default_factory=dict)


_DOCLING: DoclingLayout | None = None


def get_docling() -> DoclingLayout:
    global _DOCLING
    if _DOCLING is None:
        _DOCLING = DoclingLayout()
    return _DOCLING


def resolve_mode(mode: str) -> str:
    if mode == "auto":
        return "calidad" if docling_installed() else "rapido"
    return mode


# ---------------------------------------------------------------------------
# Paso 1: caracteres de cada página (capa de texto u OCR)
# ---------------------------------------------------------------------------


def _ink_fraction(pdf_page) -> float:
    img = pdf_page.render(scale=0.25, grayscale=True).to_pil()
    data = img.tobytes()
    return sum(v < 200 for v in data) / max(len(data), 1)


def _big_image(pdf_page) -> bool:
    import pypdfium2.raw as c

    w, h = pdf_page.get_size()
    try:
        for obj in pdf_page.get_objects(filter=[c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
            l, b, r, t = obj.get_bounds()
            if (r - l) * (t - b) > 0.5 * w * h:
                return True
    except Exception:
        pass
    return False


def _range_runs(numbers: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for n in sorted(numbers):
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    return runs


def _dist_to_rect(x: float, y: float, r) -> float:
    dx = max(r.x0 - x, 0, x - r.x1)
    dy = max(r.y0 - y, 0, y - r.y1)
    return math.hypot(dx, dy)


# ---------------------------------------------------------------------------
# Paso 2: de regiones + caracteres a bloques
# ---------------------------------------------------------------------------


def _table_block(reg: Region, glyphs: list[Glyph], page: PageText, furigana: bool) -> list[Block]:
    if not reg.cells or reg.nrows < 1 or reg.ncols < 1:
        return paragraph_blocks(glyphs, page.number, False, furigana)
    buckets: dict[int, list[Glyph]] = {}
    for g in glyphs:
        best, bd = 0, None
        for i, c in enumerate(reg.cells):
            d = _dist_to_rect(g.cx, g.cy, c)
            if bd is None or d < bd:
                best, bd = i, d
                if d == 0:
                    break
        buckets.setdefault(best, []).append(g)
    grid = [["" for _ in range(reg.ncols)] for _ in range(reg.nrows)]
    header_rows = 0
    for i, c in enumerate(reg.cells):
        if c.row >= reg.nrows or c.col >= reg.ncols:
            continue
        gs = buckets.get(i, [])
        txt = cell_text(gs, furigana)
        grid[c.row][c.col] = (grid[c.row][c.col] + " " + txt).strip()
        if c.header:
            header_rows = max(header_rows, c.row + 1)
    grid = [row for row in grid if any(cell for cell in row)]
    if grid:
        keep = [j for j in range(len(grid[0])) if any(row[j] for row in grid)]
        grid = [[row[j] for j in keep] for row in grid]
    if len(grid) < 2 or len(grid[0]) < 2:
        return paragraph_blocks(glyphs, page.number, False, furigana)
    b = Block("table", "", page.number, reg.x0, reg.y0, reg.x1, reg.y1, rows=grid, header_rows=max(header_rows, 1))
    return [b]


def page_blocks(page: PageText, regions: list[Region], furigana: bool, auto_headings: bool) -> list[Block]:
    glyphs = [g for g in page.glyphs if g.text.strip()]
    if not glyphs:
        return []
    is_ocr = page.source == "ocr"
    if page.vertical:
        blocks = paragraph_blocks(glyphs, page.number, True, furigana)
        for b in blocks:
            b.auto, b.ocr = True, is_ocr
        return blocks

    tables = [i for i, r in enumerate(regions) if r.label == "table"]
    texts = [i for i, r in enumerate(regions) if r.label not in ("table", "picture", "chart")]
    pictures = [r for r in regions if r.label in ("picture", "chart")]
    assigned: dict[int, list[Glyph]] = {i: [] for i in range(len(regions))}
    orphans: list[Glyph] = []
    page_area = page.width * page.height
    for g in glyphs:
        target = None
        inside = [i for i in tables if regions[i].contains(g.cx, g.cy)]
        if inside:
            target = min(inside, key=lambda i: regions[i].area)
        else:
            inside = [i for i in texts if regions[i].contains(g.cx, g.cy)]
            if inside:
                target = min(inside, key=lambda i: regions[i].area)
            elif texts:
                near = min(texts, key=lambda i: _dist_to_rect(g.cx, g.cy, regions[i]))
                if _dist_to_rect(g.cx, g.cy, regions[near]) <= 0.8 * g.size:
                    target = near
        if target is None:
            if is_ocr and any(p.contains(g.cx, g.cy) and p.area < 0.35 * page_area for p in pictures):
                continue  # texto dentro de un sello, logotipo o foto
            orphans.append(g)
        else:
            assigned[target].append(g)

    # Texto que la maqueta no cubrió: se agrupa aparte y se coloca en su sitio
    order: list[tuple[Region, list[Glyph]]] = [(regions[i], assigned[i]) for i in range(len(regions)) if assigned[i]]
    if orphans:
        from .layout import xy_cut

        for part in xy_cut(orphans):
            x0, y0 = min(g.x0 for g in part), min(g.y0 for g in part)
            x1, y1 = max(g.x1 for g in part), max(g.y1 for g in part)
            reg = Region("text", x0, y0, x1, y1)
            idx = None
            for j, (r, _) in enumerate(order):
                if min(r.x1, x1) - max(r.x0, x0) > 0 and r.y0 <= y0:
                    idx = j + 1
            if idx is None:
                idx = next((j for j, (r, _) in enumerate(order) if r.y0 > y0), len(order))
            order.insert(idx, (reg, part))

    # La maqueta a veces devuelve una región desordenada: si una queda claramente
    # encima de la anterior y en su misma columna, se sube a su sitio.
    def real_box(gs: list[Glyph]) -> tuple[float, float, float, float]:
        return (min(g.x0 for g in gs), min(g.y0 for g in gs), max(g.x1 for g in gs), max(g.y1 for g in gs))

    boxes = [real_box(gs) for _, gs in order]
    for i in range(1, len(order)):
        j = i
        while j > 0:
            cx0, cy0, cx1, cy1 = boxes[j]
            px0, py0, px1, py1 = boxes[j - 1]
            overlap = min(cx1, px1) - max(cx0, px0)
            if cy1 <= py0 + 1 and overlap >= 0.5 * min(cx1 - cx0, px1 - px0):
                order[j - 1], order[j] = order[j], order[j - 1]
                boxes[j - 1], boxes[j] = boxes[j], boxes[j - 1]
                j -= 1
            else:
                break

    blocks: list[Block] = []
    for reg, gs in order:
        if reg.label == "table":
            new = _table_block(reg, gs, page, furigana)
        else:
            vertical = len(gs) >= 8 and is_vertical_page(gs)
            new = paragraph_blocks(gs, page.number, vertical, furigana, reg.label)
            if reg.label in ("section_header", "title") and new:
                text = join_lines([b.text for b in new])
                if len(text) <= 200:
                    first = new[0]
                    first.kind, first.text = "h", text
                    first.y1, first.x1 = max(b.y1 for b in new), max(b.x1 for b in new)
                    new = [first]
        for b in new:
            b.ocr = is_ocr
            b.auto = auto_headings and reg.label == "text"
        blocks.extend(new)
    return blocks


# ---------------------------------------------------------------------------
# Paso 3: limpieza del documento entero
# ---------------------------------------------------------------------------

_PAGE_NUM = re.compile(
    r"^[\s\-–—·•|()\[\]]*(?:p[áa]g(?:ina)?\.?\s*)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:/|de)\s*\d{1,4})?[\s\-–—·•|()\[\]]*$",
    re.IGNORECASE,
)


def drop_running_headers(blocks: list[Block], sizes: dict[int, tuple[float, float]]) -> list[Block]:
    """Quita cabeceras, pies y números de página repetidos."""
    npages = len(sizes)

    def band(b: Block) -> str | None:
        h = sizes[b.page][1]
        if b.kind == "table":
            return None
        if b.y1 <= 0.1 * h:
            return "top"
        if b.y0 >= 0.9 * h:
            return "bottom"
        if b.vertical and b.x1 - b.x0 < 4 * b.size and b.y0 >= 0.88 * h:
            return "bottom"
        return None

    def key(b: Block) -> str:
        return re.sub(r"\d+", "#", re.sub(r"\s+", " ", b.text.strip().lower()))

    counts: dict[tuple[str, str], set[int]] = {}
    for b in blocks:
        z = band(b)
        if z:
            counts.setdefault((z, key(b)), set()).add(b.page)
    need = 2 if npages < 6 else 3
    out = []
    for b in blocks:
        z = band(b)
        if z:
            if _PAGE_NUM.match(b.text.strip()) and len(b.text.strip()) <= 14:
                continue
            if npages >= 2 and len(counts[(z, key(b))]) >= need:
                continue
            if b.label in ("page_header", "page_footer") and npages >= 2 and len(b.text) < 80 and b.nlines <= 1:
                # etiqueta de docling + margen: casi seguro cabecera o pie
                if len(counts[(z, key(b))]) >= 2:
                    continue
        out.append(b)
    return out


def merge_continuations(blocks: list[Block]) -> list[Block]:
    """Une párrafos partidos entre regiones, columnas o páginas."""
    # ¿La última línea de cada bloque llega al borde de su columna?
    by_page: dict[int, list[Block]] = {}
    for b in blocks:
        if b.kind in ("p", "li"):
            by_page.setdefault(b.page, []).append(b)
    for group in by_page.values():
        for a in group:
            if a.vertical or a.last_edge <= 0:
                continue
            col = [b.x1 for b in group if not b.vertical and min(a.x1, b.x1) - max(a.x0, b.x0) > 0.5 * min(a.x1 - a.x0, b.x1 - b.x0)]
            if len(col) >= 2:
                a.last_full = a.last_edge >= max(col) - 1.5 * a.size
    out: list[Block] = []
    for b in blocks:
        a = out[-1] if out else None
        if a is not None and a.kind == "p" and b.kind == "p" and a.text and b.text and a.vertical == b.vertical:
            s = max(a.size, b.size)
            same_size = abs(a.size - b.size) <= (0.3 if a.ocr else 0.08) * s
            scripts_ok = {a.script, b.script} != {"ja", "lat"}
            open_end = a.text[-1] not in _TERMINAL
            merge = False
            if same_size and scripts_ok and open_end and not _BULLET.match(b.text):
                if a.vertical:
                    below = a.page == b.page and 0 <= a.x0 - b.x1 < 1.6 * s
                    merge = below and a.last_full
                    if b.page > a.page and a.last_full:
                        merge = True
                else:
                    below = a.page == b.page and -1 <= b.y0 - a.y1 < 2.0 * s and abs(b.x0 - a.x0) < 1.0 * s
                    if below and a.last_full:
                        merge = True
                    elif (b.page > a.page or (b.page == a.page and b.y0 < a.y0 and b.x0 > a.x0)) and len(a.text) > 40:
                        merge = b.text[0].islower() or (is_cjk(a.text[-1]) and is_cjk(b.text[0]) and a.last_full)
            if merge:
                a.text = join_lines([a.text, b.text])
                a.last_full = b.last_full
                if b.page == a.page:
                    a.y1, a.x1 = max(a.y1, b.y1), max(a.x1, b.x1)
                a.nlines += b.nlines
                continue
        out.append(b)
    return out


def pair_bilingual(blocks: list[Block]) -> list[Block]:
    """Coloca cada traducción justo después de su frase japonesa cuando van lado a lado."""
    out = list(blocks)
    i = 0
    while i < len(out):
        b = out[i]
        if b.kind == "p" and b.script == "lat" and not b.vertical:
            best = None
            for j, a in enumerate(out):
                if j == i or a.page != b.page or a.kind != "p" or a.script == "lat" or a.vertical:
                    continue
                if not any(is_cjk(ch) for ch in a.text):
                    continue
                if a.x1 > b.x0 + 2:
                    continue
                if abs(a.y0 - b.y0) < 1.3 * max(a.size, b.size):
                    if best is None or abs(a.y0 - b.y0) < abs(out[best].y0 - b.y0):
                        best = j
            if best is not None and best != i - 1:
                out.pop(i)
                if best > i:
                    best -= 1
                pos = best + 1
                out.insert(pos, b)
                if pos > i:
                    continue  # el hueco lo ocupa ahora el siguiente bloque
        i += 1
    return out


def assign_headings(blocks: list[Block]) -> None:
    """Decide qué bloques son títulos y de qué nivel."""
    weights: list[tuple[float, int]] = [(b.size, len(b.text)) for b in blocks if b.kind == "p" and b.size > 0]
    if weights:
        weights.sort()
        half = sum(w for _, w in weights) / 2
        acc = 0
        body = weights[-1][0]
        for s, w in weights:
            acc += w
            if acc >= half:
                body = s
                break
    else:
        body = 0.0
    for i, b in enumerate(blocks):
        if b.kind not in ("p", "li") or not b.auto or not body:
            continue
        text = b.text.strip()
        if len(text) > 150 or b.nlines > 2:
            continue
        factor = 1.4 if b.ocr else 1.15
        sentence = text[-1] in "。.!?！？" and (b.size < 1.5 * body or b.ocr)
        if b.ocr and (b.nlines > 1 or "。" in text):
            continue
        if b.size >= factor * body and not sentence:
            b.kind = "h"
        elif (
            b.bold and b.nlines == 1 and len(text) <= 80 and text[-1] not in ".,;:。、" and not b.ocr
            and i + 1 < len(blocks) and not blocks[i + 1].bold
        ):
            b.kind = "h"
    heads = [b for b in blocks if b.kind == "h"]
    if not heads:
        return
    levels: list[float] = []
    for s in sorted({round(b.size, 1) for b in heads}, reverse=True):
        tol = 0.15 if any(b.ocr for b in heads) else 0.07
        if not levels or s < levels[-1] * (1 - tol):
            levels.append(s)
    tol = 0.15 if any(b.ocr for b in heads) else 0.07
    for b in heads:
        lvl = len(levels)
        for k, s in enumerate(levels):
            if b.size >= s * (1 - tol / 2):
                lvl = k + 1
                break
        b.level = min(lvl, 4)


# ---------------------------------------------------------------------------
# Paso 4: Markdown
# ---------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>").strip()


def render_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(_cell(c) for c in rows[0]) + " |", "|" + " --- |" * width]
    for r in rows[1:]:
        lines.append("| " + " | ".join(_cell(c) for c in r) + " |")
    return "\n".join(lines)


def render_blocks(blocks: list[Block], page_marks: bool = False) -> str:
    parts: list[str] = []
    page = None
    prev_kind = None
    for b in blocks:
        if page_marks and b.page != page:
            parts.append(f"<!-- página {b.page} -->")
            prev_kind = None
        page = b.page
        if b.kind == "h":
            parts.append("#" * max(b.level, 1) + " " + b.text.strip())
        elif b.kind == "table":
            parts.append(render_table(b.rows))
        elif b.kind == "li":
            text = b.text.strip()
            m = re.match(r"^\s*[•●○▪■◦‣·\-–—*]\s+", text)
            item = "- " + text[m.end():] if m else (text if re.match(r"^\(?\d{1,3}[.)]\s", text) else "- " + text)
            if prev_kind == "li" and parts:
                parts[-1] += "\n" + item
            else:
                parts.append(item)
        elif b.kind == "aviso":
            parts.append("> **Aviso:** " + b.text)
        else:
            text = b.text.strip()
            if re.match(r"^(#{1,6}\s|>)", text):
                text = "\\" + text
            parts.append(text)
        prev_kind = b.kind
    return clean_markdown("\n\n".join(parts))


# ---------------------------------------------------------------------------
# Conversión
# ---------------------------------------------------------------------------


def convert_pdf(path: str, opts: PdfOptions | None = None, password: str | None = None) -> PdfResult:
    import pypdfium2 as pdfium

    opts = opts or PdfOptions()
    mode = resolve_mode(opts.mode)
    warnings: list[str] = []
    if mode == "calidad" and not docling_installed():
        warnings.append("El modo calidad necesita docling, que no está instalado. Se usa el modo rápido.")
        mode = "rapido"

    try:
        doc = pdfium.PdfDocument(path, password=password)
    except Exception as exc:  # contraseña, archivo dañado…
        msg = str(exc)
        if "assword" in msg:
            raise ValueError("El PDF está protegido con contraseña.") from exc
        raise ValueError(f"No se pudo abrir el PDF: {msg}") from exc

    total = len(doc)
    result = PdfResult("", warnings, pages=total, mode=mode)
    plumber = None
    blocks: list[Block] = []
    sizes: dict[int, tuple[float, float]] = {}
    chunk = 6 if mode == "calidad" else 12
    workers = max(1, min(4, os.cpu_count() or 1))
    no_ocr_pages: list[int] = []
    ocr_error: str | None = None
    docling_failed = False

    def tick(done: int, msg: str) -> None:
        if opts.cancel and opts.cancel():
            raise ConversionCancelled()
        if opts.progress:
            opts.progress(done, total, msg)

    try:
        for start in range(0, total, chunk):
            numbers = list(range(start + 1, min(start + chunk, total) + 1))
            tick(start, f"Leyendo páginas {numbers[0]}–{numbers[-1]} de {total}…")
            pages: dict[int, PageText] = {}
            need_ocr: dict[int, str] = {}
            for n in numbers:
                pdf_page = doc[n - 1]
                pt = extract_page(pdf_page, n)
                quality, _ = text_layer_quality(pt)
                reason = ""
                if opts.ocr == "siempre":
                    reason = "forzado"
                elif quality == "rota":
                    reason = "fuente mal codificada"
                elif quality == "vacia":
                    if _big_image(pdf_page) or _ink_fraction(pdf_page) > 0.002:
                        reason = "escaneada"
                elif sum(1 for g in pt.glyphs if g.text.strip()) < 60 and _big_image(pdf_page):
                    reason = "escaneada"
                if reason:
                    need_ocr[n] = reason
                pages[n] = pt
                sizes[n] = (pt.width, pt.height)

            # OCR (las imágenes se generan en este hilo; el reconocimiento va en paralelo)
            images: dict[int, object] = {}
            if need_ocr:
                if opts.ocr == "nunca" or ocr_mod.pick_engine(opts.ocr_engine) is None:
                    for n, reason in need_ocr.items():
                        no_ocr_pages.append(n)
                        if reason != "forzado":
                            pages[n].glyphs = []
                            pages[n].source = "sin-ocr"
                else:
                    tick(start, f"OCR de {len(need_ocr)} página(s) ({numbers[0]}–{numbers[-1]})…")
                    dpis: dict[int, int] = {}
                    for n in need_ocr:
                        images[n], dpis[n] = ocr_mod.render_for_ocr(doc[n - 1])

                    def run(n: int):
                        return n, ocr_mod.ocr_image(images[n], opts.lang, opts.ocr_engine, dpis[n])

                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        for fut in [pool.submit(run, n) for n in need_ocr]:
                            try:
                                n, (glyphs, vertical, warns) = fut.result()
                            except ocr_mod.OcrUnavailable:
                                raise
                            except Exception as exc:
                                ocr_error = str(exc)
                                continue
                            pt = pages[n]
                            pt.glyphs, pt.source, pt.vertical = glyphs, "ocr", vertical
                            pt.image, pt.dpi = images[n], dpis[n]
                            for w in warns:
                                if w not in warnings:
                                    warnings.append(w)
                            result.ocr_pages.append(n)

            # Limpieza por página y furigana
            for n in numbers:
                pt = pages[n]
                normalize_glyphs(pt)
                if pt.source != "ocr":
                    pt.vertical = is_vertical_page(pt.glyphs)
                    drop_decorations(pt)
                if pt.source != "ocr":  # en las páginas con OCR el furigana ya viene unido
                    pt.glyphs = detect_ruby(pt.glyphs, pt.vertical)
                result.languages[n] = detect_language("".join(g.text for g in pt.glyphs))

            # Maqueta
            regions: dict[int, list[Region]] = {}
            flat = [n for n in numbers if pages[n].glyphs and not pages[n].vertical]
            if mode == "calidad" and flat and not docling_failed:
                tick(start, f"Analizando la maqueta de las páginas {numbers[0]}–{numbers[-1]}…")
                try:
                    dl = get_docling()
                    text_pages = [n for n in flat if pages[n].source != "ocr"]
                    for a, b in _range_runs(text_pages):
                        regions.update(dl.analyze(path, a, b))
                    ocr_pages = [n for n in flat if pages[n].source == "ocr"]
                    if ocr_pages:
                        tmp = temp_pdf_path()
                        try:
                            placeholder_pdf([(pages[n], images[n]) for n in ocr_pages], tmp)
                            got = dl.analyze(tmp, 1, len(ocr_pages))
                            for k, n in enumerate(ocr_pages, start=1):
                                regions[n] = got.get(k, [])
                        finally:
                            try:
                                os.unlink(tmp)
                            except OSError:
                                pass
                except ConversionCancelled:
                    raise
                except Exception as exc:
                    docling_failed = True
                    warnings.append(f"El análisis de maqueta de docling falló ({type(exc).__name__}: {exc}). Se sigue en modo rápido.")
            for n in numbers:
                pt = pages[n]
                auto = False
                if n not in regions and pt.glyphs and not pt.vertical:
                    tabs: list[Region] = []
                    if pt.source == "texto":
                        try:
                            if plumber is None:
                                import logging

                                import pdfplumber

                                logging.getLogger("pdfminer").setLevel(logging.ERROR)

                                plumber = pdfplumber.open(path, password=password)
                            tabs = ruled_tables(plumber.pages[n - 1])
                        except Exception:
                            tabs = []
                    regions[n] = fast_layout(pt, tabs)
                    auto = True
                page_bl = page_blocks(pt, regions.get(n, []), opts.furigana, auto)
                blocks.extend(page_bl)
                pt.image = None
            images.clear()
        tick(total, "Montando el Markdown…")
    finally:
        if plumber is not None:
            plumber.close()
        doc.close()

    blocks = drop_running_headers(blocks, sizes)
    blocks = merge_continuations(blocks)
    blocks = pair_bilingual(blocks)
    assign_headings(blocks)

    if no_ocr_pages:
        if opts.ocr == "nunca":
            msg = "Páginas escaneadas o con texto ilegible, sin convertir porque el OCR está desactivado: "
        else:
            msg = ocr_mod.MISSING_OCR_MESSAGE + " Páginas afectadas: "
        msg += _page_list(no_ocr_pages) + "."
        warnings.append(msg)
        blocks.insert(0, Block("aviso", msg, page=0))
    if ocr_error:
        warnings.append("El OCR falló en alguna página: " + ocr_error)
    if result.ocr_pages:
        result.ocr_pages.sort()
        warnings.append(f"Se aplicó OCR a {len(result.ocr_pages)} página(s): {_page_list(result.ocr_pages)}. Revisa el resultado.")

    md = render_blocks(blocks, opts.page_marks)
    if not md.strip():
        msg = "No se encontró texto en el documento."
        warnings.append(msg)
        md = f"> **Aviso:** {msg}\n"
    result.markdown = md
    return result


def _page_list(numbers: list[int]) -> str:
    parts = []
    for a, b in _range_runs(numbers):
        parts.append(str(a) if a == b else f"{a}–{b}")
    return ", ".join(parts)
