"""De caracteres sueltos a líneas, párrafos y bloques. Incluye furigana y tategaki."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from ..textutil import detect_language, is_cjk, is_kana, is_kanji, join_lines
from .glyphs import Glyph

# ---------------------------------------------------------------------------
# Furigana (ruby)
# ---------------------------------------------------------------------------


def _ruby_char(ch: str) -> bool:
    return is_kana(ch) or ch in "ー・"


def detect_ruby(glyphs: list[Glyph], vertical: bool, from_ocr: bool = False) -> list[Glyph]:
    """Separa el furigana del texto principal.

    Devuelve solo los caracteres principales. El último kanji de cada grupo
    lleva la lectura en ``.ruby`` y todos comparten ``.group``.
    """
    if not glyphs:
        return glyphs
    big = [g for g in glyphs if is_cjk(g.text[0])]
    if not big:
        return glyphs
    max_size = max(g.size for g in big)
    cell = max(max_size * 2, 8.0)
    grid: dict[tuple[int, int], list[Glyph]] = {}
    for g in big:
        grid.setdefault((int(g.cx // cell), int(g.cy // cell)), []).append(g)

    def near(g: Glyph):
        cx, cy = int(g.cx // cell), int(g.cy // cell)
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                yield from grid.get((i, j), ())

    # En OCR el tamaño de letra es una estimación: se admite algo más de margen
    top_ratio = 0.8 if from_ocr else 0.7

    def is_over(g: Glyph, b: Glyph, slack: float = 0.75) -> bool:
        """¿g es letra pequeña pegada encima (o a la derecha, en vertical) de b?"""
        if b is g or not (0.28 <= g.size / b.size <= top_ratio):
            return False
        if vertical:
            return (
                g.cx > b.x1 - 0.2 * b.size
                and g.x0 - b.x1 < 0.9 * g.size
                and b.x1 - g.x0 < 0.6 * g.size
                and b.y0 - slack * b.size <= g.cy <= b.y1 + slack * b.size
            )
        return (
            g.cy < b.y0 + 0.2 * b.size
            and b.y0 - g.y1 < 0.9 * g.size
            and g.y1 - b.y0 < 0.6 * g.size
            and b.x0 - slack * b.size <= g.cx <= b.x1 + slack * b.size
        )

    cand = []
    for g in glyphs:
        if g.size > top_ratio * max_size or not _ruby_char(g.text[0]):
            continue
        if any(is_over(g, b) for b in near(g)):
            cand.append(g)
    if not cand:
        return glyphs

    # Agrupar los candidatos en tiras (una lectura)
    def along(g: Glyph) -> tuple[float, float]:
        return (g.y0, g.y1) if vertical else (g.x0, g.x1)

    def perp(g: Glyph) -> float:
        return g.cx if vertical else g.cy

    cand.sort(key=lambda g: (round(perp(g) / max(g.size, 1) * 1.5), along(g)[0]))
    runs: list[list[Glyph]] = []
    for g in cand:
        if runs:
            last = runs[-1][-1]
            if abs(perp(last) - perp(g)) < 0.6 * g.size and along(g)[0] - along(last)[1] <= 0.75 * g.size and along(g)[0] >= along(last)[0]:
                runs[-1].append(g)
                continue
        runs.append([g])

    # Asignar a cada tira sus kanji base
    claims: dict[int, tuple[float, int]] = {}  # id(kanji) -> (distancia, índice de tira)
    kanji_by_id: dict[int, Glyph] = {}
    for ri, run in enumerate(runs):
        s, e = along(run[0])[0], along(run[-1])[1]
        mid = (s + e) / 2
        seen: set[int] = set()
        for g in run:
            for b in near(g):
                if id(b) in seen or not is_kanji(b.text[0]):
                    continue
                if not is_over(g, b, slack=1.5):
                    continue
                bc = b.cy if vertical else b.cx
                if not (s - 0.4 * b.size <= bc <= e + 0.4 * b.size):
                    continue
                seen.add(id(b))
                kanji_by_id[id(b)] = b
                d = abs(bc - mid)
                if id(b) not in claims or d < claims[id(b)][0]:
                    claims[id(b)] = (d, ri)
    bases: dict[int, list[Glyph]] = {}
    for kid, (_, ri) in claims.items():
        bases.setdefault(ri, []).append(kanji_by_id[kid])

    ruby_ids: set[int] = set()
    group = max((g.group for g in glyphs), default=-1) + 1
    for ri, run in enumerate(runs):
        bs = bases.get(ri)
        if not bs:
            if from_ocr and all(g.size < 0.6 * max_size for g in run) and len(run) <= 12:
                # lectura suelta sin kanji reconocido debajo: mejor quitarla que mezclarla
                ruby_ids.update(id(g) for g in run)
            continue
        bs.sort(key=lambda b: b.cy if vertical else b.cx)
        reading = "".join(g.text for g in run).strip()
        for b in bs:
            b.group = group
        if reading:
            bs[-1].ruby = (bs[-1].ruby + reading) if bs[-1].ruby else reading
        group += 1
        ruby_ids.update(id(g) for g in run)
    return [g for g in glyphs if id(g) not in ruby_ids]


# ---------------------------------------------------------------------------
# Líneas
# ---------------------------------------------------------------------------


@dataclass
class Line:
    glyphs: list[Glyph]
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    text: str = ""
    bold: bool = False


_VERT_BAR = "|｜︱丨"
_JA_OPEN_CLOSE = "。、「」『』（）：；！？・…〜"


def _glyph_out(g: Glyph, furigana: bool) -> str:
    if g.ruby and furigana:
        return f"{g.text}({g.ruby})"
    return g.text


def line_text(glyphs: list[Glyph], vertical: bool, furigana: bool) -> str:
    out: list[str] = []
    prev: Glyph | None = None
    # separación normal entre letras de la línea (texto espaciado: «C E R T I F I C A D O»)
    lat = [
        (b.x0 - a.x1) / max(a.size, b.size)
        for a, b in zip(glyphs, glyphs[1:])
        if not vertical and not is_cjk(a.text[-1]) and not is_cjk(b.text[0])
    ]
    track = max(0.0, sorted(lat)[len(lat) // 2]) if len(lat) >= 4 else 0.0
    for g in glyphs:
        if prev is not None:
            a, b = prev.text[-1], g.text[0]
            s = max(prev.size, g.size)
            gap = (g.y0 - prev.y1) if vertical else (g.x0 - prev.x1)
            space = False
            ocr = prev.line >= 0 and g.line >= 0
            if prev.group >= 0 and prev.group == g.group:
                space = False
            elif ocr and is_cjk(a) and is_cjk(b):
                space = gap > 1.2 * s  # el OCR parte el japonés en «palabras»: no son espacios
            elif ocr and (is_cjk(a) or is_cjk(b)):
                letter = (b if is_cjk(a) else a).isalpha()
                punct = a in _JA_OPEN_CLOSE or b in _JA_OPEN_CLOSE
                space = letter and not punct  # «la partícula を marca»: japonés suelto entre texto latino
            elif ocr:
                space = prev.space_after
            elif is_cjk(a) and is_cjk(b):
                space = gap > (1.0 if vertical else 0.55) * s
            elif is_cjk(a) or is_cjk(b):
                space = (prev.space_after and gap > 0.12 * s) or gap > 0.3 * s
                if vertical:
                    space = False
            else:
                flagged = prev.space_after and g.seq > prev.seq
                space = gap > (track + (0.1 if flagged else 0.28)) * s
            if space:
                out.append(" ")
        t = _glyph_out(g, furigana)
        if vertical and t in _VERT_BAR and prev is not None and is_kana(prev.text[-1]):
            t = "ー"
        out.append(t)
        prev = g
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _mk_line(gs: list[Glyph], vertical: bool, furigana: bool) -> Line:
    if all(g.line >= 0 for g in gs):
        gs.sort(key=lambda g: g.seq)  # OCR: se respeta el orden en que leyó el motor
    else:
        gs.sort(key=(lambda g: (g.y0, g.x0)) if vertical else (lambda g: (g.x0, g.y0)))
    sizes = sorted(g.size for g in gs)
    ln = Line(
        gs,
        min(g.x0 for g in gs), min(g.y0 for g in gs), max(g.x1 for g in gs), max(g.y1 for g in gs),
        sizes[len(sizes) // 2],
    )
    ln.text = line_text(gs, vertical, furigana)
    ln.bold = all(g.bold for g in gs)
    return ln


def build_lines(glyphs: list[Glyph], vertical: bool, furigana: bool = True) -> list[Line]:
    """Agrupa caracteres en líneas (o en columnas, si el texto es vertical)."""
    glyphs = [g for g in glyphs if g.text.strip()]
    if not glyphs:
        return []
    groups: list[list[Glyph]] = []
    if all(g.line >= 0 for g in glyphs):  # OCR: las líneas ya vienen hechas
        by_line: dict[int, list[Glyph]] = {}
        for g in glyphs:
            by_line.setdefault(g.line, []).append(g)
        lines = [_mk_line(grp, vertical, furigana) for grp in by_line.values()]
        lines.sort(key=(lambda l: -(l.x0 + l.x1) / 2) if vertical else (lambda l: (l.y0, l.x0)))
        return lines
    if vertical:
        # columnas: de derecha a izquierda
        for g in sorted(glyphs, key=lambda g: -g.cx):
            for grp in groups:
                gx0, gx1 = grp[0].x0, grp[0].x1  # la caja de la columna la marca su primer carácter
                core0, core1 = min(x.x0 for x in grp[-6:]), max(x.x1 for x in grp[-6:])
                if core0 <= g.cx <= core1 or (g.x0 <= (gx0 + gx1) / 2 <= g.x1):
                    grp.append(g)
                    break
            else:
                groups.append([g])
        lines = [_mk_line(grp, True, furigana) for grp in groups]
        lines.sort(key=lambda l: -(l.x0 + l.x1) / 2)
        return lines
    for g in sorted(glyphs, key=lambda g: (g.cy, g.x0)):
        placed = False
        for grp in reversed(groups[-4:]):
            y0 = min(x.y0 for x in grp[-8:])
            y1 = max(x.y1 for x in grp[-8:])
            mid = (y0 + y1) / 2
            if y0 <= g.cy <= y1 and g.y0 <= mid <= g.y1:
                grp.append(g)
                placed = True
                break
        if not placed:
            groups.append([g])
    lines = [_mk_line(grp, False, furigana) for grp in groups]
    lines.sort(key=lambda l: (l.y0, l.x0))
    return lines


# ---------------------------------------------------------------------------
# Párrafos
# ---------------------------------------------------------------------------

_BULLET = re.compile(r"^\s*(?:[•●○▪■◦‣·\-–—*]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|[①-⑳])\s+")
_TERMINAL = "。．.!?！？…」』）)»”:："


def _script(text: str) -> str:
    lang = detect_language(text)
    if lang == "ja":
        return "ja"
    if lang in ("es", "en"):
        return "lat"
    return "mix"


def _first_word_width(ln: Line, vertical: bool) -> float:
    gs = ln.glyphs
    if not gs:
        return 0.0
    first = gs[0]
    if is_cjk(first.text[0]):
        return first.h if vertical else first.w
    end = first
    for a, b in zip(gs, gs[1:]):
        if a.space_after or is_cjk(b.text[0]) or (b.x0 - a.x1) > 0.3 * a.size:
            break
        end = b
    return end.x1 - first.x0


def split_paragraphs(lines: list[Line], vertical: bool) -> list[list[Line]]:
    """Parte las líneas de un bloque en párrafos."""
    if not lines:
        return []
    if vertical:
        top = min(l.y0 for l in lines)
        bottom = max(l.y1 for l in lines)
        paras = [[lines[0]]]
        for p, n in zip(lines, lines[1:]):
            s = max(p.size, n.size)
            new = (
                abs(p.size - n.size) > 0.12 * s
                or n.y0 > top + 0.7 * n.size and n.y0 - top < 3 * n.size
                or p.y1 < bottom - 1.6 * p.size
            )
            if new:
                paras.append([n])
            else:
                paras[-1].append(n)
        return paras

    left = min(l.x0 for l in lines)
    right = max(l.x1 for l in lines)
    gaps = [n.y0 - p.y1 for p, n in zip(lines, lines[1:])]
    # separación normal entre líneas: el cuartil bajo (la mediana se dispara si hay muchos párrafos cortos)
    med_gap = sorted(gaps)[len(gaps) // 4] if len(gaps) >= 3 else None
    flush = sum(1 for l in lines if l.x0 - left < 0.3 * l.size) / len(lines)
    ocr = all(g.line >= 0 for g in lines[0].glyphs)
    paras = [[lines[0]]]
    for p, n in zip(lines, lines[1:]):
        s = max(p.size, n.size)
        gap = n.y0 - p.y1
        new = False
        if n.y0 < p.y0 + 0.5 * p.size and n.x0 >= p.x1 - 0.5 * s:
            paras[-1].append(n)  # misma fila, partida en dos por el OCR
            continue
        tol = 0.3 if ocr else 0.12
        wrapped = (
            p.text[-1:] not in _TERMINAL and p.x1 >= right - 1.5 * s and abs(p.size - n.size) <= tol * s
            and {_script(p.text), _script(n.text)} != {"ja", "lat"} and not _BULLET.match(n.text)
            and n.x0 - left < 0.5 * s and gap < 1.6 * s
        )
        if wrapped:
            new = False  # la línea llega al borde y la frase sigue: es el mismo párrafo
        elif abs(p.size - n.size) > tol * s:
            new = True
        elif {_script(p.text), _script(n.text)} == {"ja", "lat"}:
            new = True  # frase japonesa y su traducción
        elif med_gap is not None and gap > med_gap + 0.42 * s:
            new = True
        elif med_gap is None and gap > 0.9 * s:
            new = True
        elif _BULLET.match(n.text):
            new = True
        elif flush >= 0.6 and 0.8 * s < n.x0 - left < 4 * s and p.x0 - left < 0.3 * s:
            new = True  # sangría de primera línea
        elif p.x1 < right - _first_word_width(n, False) - 0.6 * s and (
            not ocr or p.text[-1:] in _TERMINAL or p.x1 < left + 0.6 * (right - left)
        ):
            new = True  # la línea anterior acabó antes de tiempo
        if new:
            paras.append([n])
        else:
            paras[-1].append(n)
    return paras


# ---------------------------------------------------------------------------
# Bloques
# ---------------------------------------------------------------------------


@dataclass
class Block:
    kind: str  # "p", "h", "li", "table", "aviso"
    text: str = ""
    page: int = 0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    size: float = 0.0
    bold: bool = False
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 1
    vertical: bool = False
    last_full: bool = False  # la última línea llega al borde derecho
    last_edge: float = 0.0  # borde derecho (o inferior) de la última línea
    column: int = 0
    script: str = "mix"
    label: str = "text"
    nlines: int = 1
    auto: bool = False  # el título se decide por tamaño (modo rápido, tategaki)
    ocr: bool = False


def paragraph_blocks(glyphs: list[Glyph], page: int, vertical: bool, furigana: bool, label: str = "text") -> list[Block]:
    """Convierte los caracteres de una región en uno o varios bloques de párrafo."""
    lines = build_lines(glyphs, vertical, furigana)
    if not lines:
        return []
    if vertical:
        full_edge = max(l.y1 for l in lines)
    else:
        full_edge = max(l.x1 for l in lines)
    blocks = []
    for para in split_paragraphs(lines, vertical):
        text = join_lines([l.text for l in para])
        if not text:
            continue
        last = para[-1]
        b = Block(
            "p", text, page,
            min(l.x0 for l in para), min(l.y0 for l in para), max(l.x1 for l in para), max(l.y1 for l in para),
            size=median(l.size for l in para), bold=all(l.bold for l in para), vertical=vertical, label=label,
        )
        edge = last.y1 if vertical else last.x1
        b.last_edge = edge
        b.last_full = len(lines) > 1 and edge >= full_edge - 1.5 * last.size
        b.script = _script(text)
        b.nlines = len(para)
        if label == "list_item" or _BULLET.match(text):
            b.kind = "li"
        blocks.append(b)
    return blocks


def cell_text(glyphs: list[Glyph], furigana: bool) -> str:
    if not glyphs:
        return ""
    from .glyphs import is_vertical_page

    vertical = len(glyphs) > 6 and is_vertical_page(glyphs)
    lines = build_lines(glyphs, vertical, furigana)
    return join_lines([l.text for l in lines])
