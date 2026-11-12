"""Extracción de caracteres con posición desde la capa de texto de un PDF."""

from __future__ import annotations

import ctypes
import math
from dataclasses import dataclass, field

from ..textutil import broken_text_score, is_cjk, is_kana, is_kanji, normalize_text


@dataclass
class Glyph:
    """Un carácter (o grupo corto) con su caja en puntos, origen arriba-izquierda."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool = False
    angle: float = 0.0  # grados; 0 = derecho
    light: bool = False  # color muy claro (marca de agua)
    line: int = -1  # id de línea del OCR, si viene del OCR
    conf: float = 100.0
    ruby: str = ""  # lectura furigana que va detrás de este carácter
    group: int = -1  # id del grupo base de un furigana
    seq: int = 0  # orden original
    space_after: bool = False  # el PDF indica un espacio detrás

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class PageText:
    number: int  # empieza en 1
    width: float
    height: float
    glyphs: list[Glyph] = field(default_factory=list)
    source: str = "texto"  # "texto" u "ocr"
    vertical: bool = False
    warnings: list[str] = field(default_factory=list)
    image: object = None  # imagen de la página (solo páginas con OCR)
    dpi: int = 0


def _is_bold(pdfium_c, textpage, i: int, buf, cache: dict) -> bool:
    try:
        obj = pdfium_c.FPDFText_GetTextObject(textpage, i)
        if not obj:
            return False
        font = pdfium_c.FPDFTextObj_GetFont(obj)
        key = ctypes.addressof(font.contents) if font else 0
        if key in cache:
            return cache[key]
        bold = False
        if font:
            if pdfium_c.FPDFFont_GetWeight(font) >= 600:
                bold = True
            n = pdfium_c.FPDFFont_GetBaseFontName(font, buf, 256)
            name = buf.raw[: max(n - 1, 0)].decode("utf-8", "ignore").lower()
            if any(k in name for k in ("bold", "black", "heavy", "semibold", "demi")):
                bold = True
            if pdfium_c.FPDFFont_GetFlags(font) & (1 << 18):
                bold = True
        cache[key] = bold
        return bold
    except Exception:
        return False


def extract_page(page, number: int) -> PageText:
    """Lee los caracteres de la capa de texto de una página de pypdfium2."""
    import pypdfium2.raw as pdfium_c

    width, height = page.get_size()
    out = PageText(number=number, width=width, height=height)
    textpage = page.get_textpage()
    try:
        n = pdfium_c.FPDFText_CountChars(textpage)
        rect = pdfium_c.FS_RECTF()
        le, ri, bo, to = (ctypes.c_double() for _ in range(4))
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        buf = ctypes.create_string_buffer(256)
        cache: dict = {}
        mat = pdfium_c.FS_MATRIX()
        for i in range(n):
            code = pdfium_c.FPDFText_GetUnicode(textpage, i)
            if code in (0, 0xFFFE, 0xFFFF):
                continue
            if 0xD800 <= code <= 0xDFFF:
                continue
            ch = chr(code)
            if ch in "\r\n\t":
                continue
            if pdfium_c.FPDFText_IsGenerated(textpage, i) == 1:
                if ch.isspace() and out.glyphs:
                    out.glyphs[-1].space_after = True
                continue
            if not pdfium_c.FPDFText_GetLooseCharBox(textpage, i, rect):
                continue
            x0, x1 = min(rect.left, rect.right), max(rect.left, rect.right)
            yb, yt = min(rect.bottom, rect.top), max(rect.bottom, rect.top)
            if ch.isspace():
                if out.glyphs:
                    out.glyphs[-1].space_after = True
                continue
            size = pdfium_c.FPDFText_GetFontSize(textpage, i)
            if pdfium_c.FPDFText_GetMatrix(textpage, i, ctypes.byref(mat)):
                scale = math.sqrt(abs(mat.a * mat.d - mat.b * mat.c))
                if scale > 0.001:
                    size *= scale
            ang = math.degrees(pdfium_c.FPDFText_GetCharAngle(textpage, i)) % 360
            if ang > 180:
                ang -= 360
            if abs(ang) > 0.5 or size <= 0.01:
                # caja ajustada para texto girado (la caja "holgada" no es fiable)
                if pdfium_c.FPDFText_GetCharBox(textpage, i, le, ri, bo, to):
                    x0, x1 = min(le.value, ri.value), max(le.value, ri.value)
                    yb, yt = min(bo.value, to.value), max(bo.value, to.value)
            if size <= 0.01:
                size = max(yt - yb, 1.0)
            light = False
            if pdfium_c.FPDFText_GetFillColor(textpage, i, r, g, b, a):
                lum = (r.value + g.value + b.value) / 3
                light = lum > 185 or a.value < 90
            bold = _is_bold(pdfium_c, textpage, i, buf, cache)
            out.glyphs.append(
                Glyph(ch, x0, height - yt, x1, height - yb, size, bold=bold, angle=ang, light=light, seq=i)
            )
    finally:
        textpage.close()
    return out


def raw_text(page: PageText) -> str:
    return "".join(g.text for g in page.glyphs)


def text_layer_quality(page: PageText) -> tuple[str, float]:
    """Clasifica la capa de texto: 'ok', 'vacia' o 'rota' (mojibake)."""
    txt = raw_text(page)
    visible = [c for c in txt if not c.isspace()]
    if len(visible) < 8:
        return "vacia", 0.0
    score = broken_text_score(txt)
    if score >= 0.25:
        return "rota", score
    return "ok", score


def normalize_glyphs(page: PageText) -> None:
    """Normaliza Unicode carácter a carácter y une marcas sueltas (dakuten, tildes)."""
    out: list[Glyph] = []
    for g in page.glyphs:
        t = normalize_text(g.text)
        if not t:
            continue
        # dakuten/handakuten o acento combinante suelto: se une al carácter anterior
        if out and t in ("゙", "゚", "゛", "゜") and is_kana(out[-1].text[-1:]):
            mark = "゙" if t in ("゙", "゛") else "゚"
            out[-1].text = normalize_text(out[-1].text + mark)
            continue
        g.text = t
        out.append(g)
    page.glyphs = out


def is_vertical_page(glyphs: list[Glyph]) -> bool:
    """¿El texto japonés de estos caracteres va en columnas verticales?"""
    body = [g for g in glyphs if g.text.strip() and (is_kana(g.text[0]) or is_kanji(g.text[0]))]
    if len(body) < 6:
        return False
    sizes = sorted(g.size for g in body)
    main = sizes[len(sizes) // 2]
    body = [g for g in body if g.size >= main * 0.8]
    # Para cada carácter, ¿su vecino más cercano está debajo o a la derecha?
    v = h = 0
    by_seq = sorted(body, key=lambda g: g.seq)
    for a, b in zip(by_seq, by_seq[1:]):
        dx, dy = b.cx - a.cx, b.cy - a.cy
        s = max(a.size, b.size)
        if abs(dx) < 0.45 * s and 0 < dy < 2.2 * s:
            v += 1
        elif abs(dy) < 0.45 * s and 0 < dx < 2.2 * s:
            h += 1
    if v + h < 5:
        return _vertical_by_geometry(body)
    return v > 1.5 * h


def _vertical_by_geometry(body: list[Glyph]) -> bool:
    v = h = 0
    for a in body:
        best = None
        for b in body:
            if b is a:
                continue
            d = abs(b.cx - a.cx) + abs(b.cy - a.cy)
            if best is None or d < best[0]:
                best = (d, b)
        if best is None:
            continue
        b = best[1]
        if abs(b.cx - a.cx) < abs(b.cy - a.cy):
            v += 1
        else:
            h += 1
    return v > 1.5 * h


def drop_decorations(page: PageText) -> None:
    """Quita sellos, marcas de agua y texto girado del margen.

    El texto japonés vertical (tategaki) no está girado: sus caracteres van
    derechos y apilados, así que no se ve afectado.
    """
    glyphs = [g for g in page.glyphs if g.text.strip()]
    if not glyphs:
        return
    rotated90 = [g for g in glyphs if 80 <= abs(g.angle) <= 100]
    if len(rotated90) > 0.6 * len(glyphs):
        _rotate_page(page, 90 if sum(g.angle for g in rotated90) > 0 else -90)
        glyphs = [g for g in page.glyphs if g.text.strip()]
    sizes = sorted(g.size for g in glyphs if abs(g.angle) < 3)
    body = sizes[len(sizes) // 2] if sizes else 10.0
    keep = []
    for g in page.glyphs:
        ang = abs(g.angle)
        if ang > 3 and not (page.vertical and 80 <= ang <= 100):
            continue  # texto girado: sello, marca de agua o nota del margen
        if g.light and g.size > 2.5 * body:
            continue  # marca de agua horizontal, grande y clara
        keep.append(g)
    page.glyphs = keep


def _rotate_page(page: PageText, deg: int) -> None:
    w, h = page.width, page.height
    for g in page.glyphs:
        if deg == 90:  # texto que sube: girar la página en sentido horario
            g.x0, g.y0, g.x1, g.y1 = h - g.y1, g.x0, h - g.y0, g.x1
        else:
            g.x0, g.y0, g.x1, g.y1 = g.y0, w - g.x1, g.y1, w - g.x0
        g.angle = g.angle - deg if 80 <= abs(g.angle) <= 100 else g.angle + 45
        if abs(g.angle) < 12:
            g.angle = 0.0
    page.width, page.height = h, w


def has_cjk(glyphs: list[Glyph]) -> bool:
    return any(is_cjk(g.text[0]) for g in glyphs if g.text.strip())
