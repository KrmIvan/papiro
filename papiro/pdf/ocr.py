"""OCR local: Tesseract (por defecto) o RapidOCR (alternativa).

Devuelve caracteres con posición, igual que la capa de texto de un PDF, para
que el resto del programa trate igual una página escaneada y una digital.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

from ..textutil import is_cjk, is_kana, is_kanji
from .glyphs import Glyph, PageText

OCR_DPI = int(os.environ.get("PAPIRO_OCR_DPI", "300"))
LANG_CODES = {"ja": ["jpn", "eng"], "es": ["spa", "eng"], "en": ["eng"], "auto": ["jpn", "spa", "eng"]}
NEEDED_LANGS = ["jpn", "jpn_vert", "spa", "eng"]


class OcrUnavailable(RuntimeError):
    """No hay ningún motor de OCR instalado."""


def app_dir() -> Path:
    return Path(os.environ.get("PAPIRO_HOME", Path.home() / ".papiro"))


# ---------------------------------------------------------------------------
# Tesseract
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def find_tesseract() -> str | None:
    env = os.environ.get("PAPIRO_TESSERACT")
    if env and Path(env).exists():
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path("/opt/homebrew/bin/tesseract"),
        Path("/usr/local/bin/tesseract"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def tessdata_dir() -> str | None:
    """Carpeta propia con los idiomas (la crean los instaladores), si existe."""
    env = os.environ.get("PAPIRO_TESSDATA")
    if env and Path(env).is_dir():
        return env
    own = app_dir() / "tessdata"
    if own.is_dir() and any(own.glob("*.traineddata")):
        return str(own)
    return None


def _tess_cmd() -> list[str]:
    exe = find_tesseract()
    if not exe:
        raise OcrUnavailable("Tesseract no está instalado.")
    cmd = [exe]
    td = tessdata_dir()
    if td:
        cmd += ["--tessdata-dir", td]
    return cmd


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


@lru_cache(maxsize=1)
def tesseract_langs() -> tuple[str, ...]:
    try:
        out = subprocess.run(
            _tess_cmd() + ["--list-langs"], capture_output=True, text=True, timeout=30, **_no_window()
        )
    except (OcrUnavailable, OSError, subprocess.SubprocessError):
        return ()
    langs = [ln.strip() for ln in (out.stdout + out.stderr).splitlines()[1:] if ln.strip()]
    return tuple(l for l in langs if "/" not in l and " " not in l)


def reset_cache() -> None:
    find_tesseract.cache_clear()
    tesseract_langs.cache_clear()
    _rapid_engine.cache_clear()


def _run_tesseract(png: bytes, langs: list[str], psm: int, dpi: int = 300) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "pagina.png"
        src.write_bytes(png)
        env = dict(os.environ)
        env.setdefault("OMP_THREAD_LIMIT", "1")
        proc = subprocess.run(
            _tess_cmd() + [str(src), "stdout", "-l", "+".join(langs), "--psm", str(psm), "--dpi", str(dpi), "tsv"],
            capture_output=True, env=env, timeout=600, **_no_window(),
        )
    if proc.returncode != 0:
        raise RuntimeError("Tesseract falló: " + proc.stderr.decode("utf-8", "replace")[-400:])
    text = proc.stdout.decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))


def _char_weight(ch: str) -> float:
    if is_cjk(ch):
        return 1.0
    if ch in "ilI.,;:'|!1 ":
        return 0.3
    return 0.55


def _explode(text: str, x0: float, y0: float, x1: float, y1: float, size: float, vertical: bool,
             line: int, conf: float, seq0: int) -> list[Glyph]:
    """Reparte los caracteres de una palabra a lo largo de su caja."""
    weights = [_char_weight(c) for c in text]
    total = sum(weights) or 1.0
    out = []
    pos = 0.0
    for i, ch in enumerate(text):
        a, b = pos / total, (pos + weights[i]) / total
        pos += weights[i]
        if vertical:
            g = Glyph(ch, x0, y0 + (y1 - y0) * a, x1, y0 + (y1 - y0) * b, size)
        else:
            g = Glyph(ch, x0 + (x1 - x0) * a, y0, x0 + (x1 - x0) * b, y1, size)
        g.line, g.conf, g.seq = line, conf, seq0 + i
        out.append(g)
    return out


def _height_factor(text: str) -> float:
    """Altura de la caja de una línea en relación con el cuerpo de letra.

    Una línea sin letras altas ni bajas («amor») mide la mitad que una con
    ambas («figura»); sin corregirlo, parecerían de tamaños distintos.
    """
    if any(is_kana(c) or is_kanji(c) for c in text):
        return 1.0
    asc = any(c in "bdfhklt¿¡?!()[]/" or c.isupper() or c.isdigit() or c in "áéíóúñü" for c in text)
    desc = any(c in "gjpqyç,;()[]" for c in text)
    if asc and desc:
        return 0.93
    if asc:
        return 0.72
    if desc:
        return 0.68
    return 0.47


def _tsv_to_glyphs(rows: list[dict], scale: float, vertical: bool, boxes: dict | None = None) -> list[Glyph]:
    lines: dict[tuple, dict] = {}
    words: list[tuple[tuple, dict]] = []
    for r in rows:
        try:
            level = int(r["level"])
            key = (int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))
            box = (int(r["left"]), int(r["top"]), int(r["width"]), int(r["height"]))
        except (KeyError, ValueError, TypeError):
            continue
        if level == 4:
            lines[key] = {"box": box}
        elif level == 5:
            txt = (r.get("text") or "").strip()
            try:
                conf = float(r.get("conf", "0"))
            except ValueError:
                conf = 0.0
            if not txt:
                continue
            if conf < 30 and len(txt) <= 2 and not any(is_kana(c) or is_kanji(c) for c in txt):
                continue  # motas de polvo
            if conf < 12:
                continue
            words.append((key, {"box": box, "text": txt, "conf": conf}))
    # Líneas de basura (sellos, texto girado, manchas): confianza baja o casi vacías
    stats: dict[tuple, list[float]] = {}
    for k, w in words:
        st = stats.setdefault(k, [0.0, 0.0])
        st[0] += w["conf"] * len(w["text"])
        st[1] += len(w["text"])
    bad = {k for k, (c, n) in stats.items() if c / n < 55 or (n <= 3 and c / n < 85)}
    if not vertical:  # texto latino girado 90° en el margen: caja alta y estrecha
        for k, (c, n) in stats.items():
            box = lines.get(k, {}).get("box")
            if box and n >= 4 and box[3] > 3 * box[2]:
                txt = "".join(w["text"] for kk, w in words if kk == k)
                if sum(is_cjk(ch) for ch in txt) < len(txt) / 2:
                    bad.add(k)
    words = [(k, w) for k, w in words if k not in bad]
    # Las cajas de palabra de Tesseract en japonés a veces son demasiado anchas:
    # el inicio es fiable, así que cada palabra acaba donde empieza la siguiente.
    for (k1, w1), (k2, w2) in zip(words, words[1:]):
        if k1 != k2:
            continue
        l1, t1, wd1, ht1 = w1["box"]
        l2, t2 = w2["box"][0], w2["box"][1]
        if vertical:
            if t1 < t2 < t1 + ht1:
                w1["box"] = (l1, t1, wd1, t2 - t1)
        elif l1 < l2 < l1 + wd1:
            w1["box"] = (l1, t1, l2 - l1, ht1)
    glyphs: list[Glyph] = []
    factors: dict[tuple, float] = {}  # cuerpo de letra de cada línea, en puntos
    line_ids = {k: i for i, k in enumerate(lines)}
    if boxes is not None:
        for k, i in line_ids.items():
            l, t, wd, ht = lines[k]["box"]
            boxes[i] = (l * scale, t * scale, (l + wd) * scale, (t + ht) * scale)
    seq = 0
    for key, w in words:
        lb = lines.get(key, {"box": w["box"]})["box"]
        l, t, wd, ht = w["box"]
        if key not in factors:
            # cuerpo de letra de la línea: mediana de la altura de sus palabras, corregida por
            # las letras que tiene cada una (la caja de la línea entera engorda si el escaneo está torcido)
            hs = sorted(w2["box"][2 if vertical else 3] / _height_factor(w2["text"]) for k2, w2 in words if k2 == key)
            factors[key] = hs[len(hs) // 2] * scale
        if vertical:
            size = max(factors[key], lb[2] * scale * 0.8)
            x0, x1 = lb[0] * scale, (lb[0] + lb[2]) * scale
            y0, y1 = t * scale, (t + ht) * scale
        else:
            size = factors[key]
            x0, x1 = l * scale, (l + wd) * scale
            y0, y1 = lb[1] * scale, (lb[1] + lb[3]) * scale
        if vertical:
            y1 = min(y1, (lb[1] + lb[3]) * scale) if y0 < (lb[1] + lb[3]) * scale else y1
        else:
            x1 = min(x1, (lb[0] + lb[2]) * scale) if x0 < (lb[0] + lb[2]) * scale else x1
        gs = _explode(w["text"], x0, y0, x1, y1, max(size, 1.0), vertical, line_ids.get(key, -1), w["conf"], seq)
        seq += len(gs) + 1
        if gs:
            gs[-1].space_after = True
        glyphs.extend(gs)
    return glyphs


# ---------------------------------------------------------------------------
# ¿Texto vertical? (sin OCR, mirando los huecos entre líneas)
# ---------------------------------------------------------------------------


def looks_vertical(img) -> bool:
    """Compara los huecos en blanco entre filas y entre columnas de tinta."""
    from PIL import Image, ImageFilter

    g = img.convert("L").filter(ImageFilter.MedianFilter(3))  # quita motas de polvo
    g = g.point(lambda v: 255 if v < 140 else 0)  # tinta = blanco
    box = g.getbbox()
    if not box:
        return False
    g = g.crop(box)
    w, h = g.size
    if w < 80 or h < 80:
        return False

    def blank_fraction(strip, axis: str) -> float:
        sw, sh = strip.size
        prof = strip.resize((1, sh), Image.BOX) if axis == "rows" else strip.resize((sw, 1), Image.BOX)
        data = prof.tobytes()
        return sum(v < 4 for v in data) / max(len(data), 1)

    n = 4
    rows = sum(blank_fraction(g.crop((w * i // n, 0, w * (i + 1) // n, h)), "rows") for i in range(n)) / n
    cols = sum(blank_fraction(g.crop((0, h * i // n, w, h * (i + 1) // n)), "cols") for i in range(n)) / n
    return cols > 0.3 and cols > rows * 1.3


# ---------------------------------------------------------------------------
# RapidOCR (alternativa)
# ---------------------------------------------------------------------------


def rapidocr_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("rapidocr") is not None


@lru_cache(maxsize=1)
def _rapid_engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "error"})


def _rapid_glyphs(img, scale: float) -> list[Glyph]:
    """Lee la imagen con RapidOCR (PP-OCR) y devuelve caracteres con posición."""
    import math
    from statistics import median

    import numpy as np

    res = _rapid_engine()(np.array(img.convert("RGB")), return_word_box=True)
    glyphs: list[Glyph] = []
    if res is None or res.boxes is None or res.txts is None:
        return glyphs
    word_results = getattr(res, "word_results", None) or ()
    aligned = len(word_results) == len(res.txts)
    seq = 0
    for i, (quad, txt, score) in enumerate(zip(res.boxes, res.txts, res.scores)):
        (xa, ya), (xb, yb), (xc, yc), (xd, yd) = [(float(p[0]), float(p[1])) for p in quad]
        ang = abs(math.degrees(math.atan2(yb - ya, xb - xa)))
        if 8 < ang < 82 or 98 < ang < 172:
            continue  # texto inclinado: sello o marca de agua
        x0, x1 = min(xa, xb, xc, xd), max(xa, xb, xc, xd)
        y0, y1 = min(ya, yb, yc, yd), max(ya, yb, yc, yd)
        txt = txt.strip()
        if not txt or score < 0.45:
            continue
        cjk = sum(is_cjk(c) for c in txt)
        vertical = (y1 - y0) > 1.6 * (x1 - x0) and len(txt) > 1
        if vertical and cjk < len(txt) / 2:
            continue  # texto latino girado 90° en el margen
        words = []
        if aligned and not vertical:
            for w in word_results[i]:
                try:
                    wt, ws, wq = w[0], float(w[1]), w[2]
                    xs = [float(p[0]) for p in wq]
                    if wt and wt.strip():
                        words.append((wt.strip(), ws, min(xs), max(xs)))
                except (TypeError, ValueError, IndexError):
                    words = []
                    break
        if not words:
            size = (x1 - x0) if vertical else (y1 - y0) * 0.75
            gs = _explode(txt.replace(" ", ""), x0 * scale, y0 * scale, x1 * scale, y1 * scale,
                          max(size * scale, 1.0), vertical, i, score * 100, seq)
            seq += len(gs) + 1
            if gs:
                gs[-1].space_after = True
            glyphs.extend(gs)
            continue
        # cuerpo de letra: la altura de la caja menos el margen que añade el detector (~14 px a 300 ppp)
        h = y1 - y0
        size_px = max(h - 14 * (0.24 / scale), 0.6 * h)
        cy = (y0 + y1) / 2
        for k, (t, ws, a, b) in enumerate(words):
            gs = _explode(t, a * scale, (cy - size_px / 2) * scale, b * scale, (cy + size_px / 2) * scale,
                          max(size_px * scale, 1.0), False, i, ws * 100, seq)
            seq += len(gs) + 1
            # En líneas mixtas RapidOCR da el texto latino letra a letra: solo hay espacio si hay hueco
            if gs and k + 1 < len(words) and words[k + 1][2] - b > 0.2 * size_px:
                gs[-1].space_after = True
            glyphs.extend(gs)
        # Los espacios que marca el propio texto reconocido mandan sobre los huecos medidos
        mine = [g for g in glyphs if g.line == i]
        if "".join(g.text for g in mine) == txt.replace(" ", ""):
            j = -1
            for g in mine:
                g.space_after = False
            for ch in txt:
                if ch == " ":
                    if j >= 0:
                        mine[j].space_after = True
                else:
                    j += 1
    return glyphs


def _use_tesseract_for_latin_lines(rapid: list[Glyph], tess: list[Glyph]) -> list[Glyph]:
    """En las líneas solo latinas se prefiere Tesseract: conserva ¿ ¡ y los espacios."""

    def lines_of(gs: list[Glyph]) -> dict[int, list[Glyph]]:
        out: dict[int, list[Glyph]] = {}
        for g in gs:
            out.setdefault(g.line, []).append(g)
        return out

    def box(gs: list[Glyph]) -> tuple[float, float, float, float]:
        return (min(g.x0 for g in gs), min(g.y0 for g in gs), max(g.x1 for g in gs), max(g.y1 for g in gs))

    t_lines = []
    for gs in lines_of(tess).values():
        if any(is_cjk(g.text[0]) for g in gs) or _mean_conf(gs) < 88 or len(gs) < 3:
            continue
        t_lines.append((box(gs), gs))
    out: list[Glyph] = []
    base = max((g.seq for g in rapid), default=0) + 1000
    for lid, gs in lines_of(rapid).items():
        if any(is_cjk(g.text[0]) for g in gs) or len(gs) < 3:
            out.extend(gs)
            continue
        x0, y0, x1, y1 = box(gs)
        best = None
        for (a0, b0, a1, b1), tg in t_lines:
            cy = (b0 + b1) / 2
            inter = min(x1, a1) - max(x0, a0)
            union = max(x1, a1) - min(x0, a0)
            if y0 - 0.3 * (y1 - y0) <= cy <= y1 + 0.3 * (y1 - y0) and union > 0 and inter / union >= 0.8:
                if best is None or inter / union > best[0]:
                    best = (inter / union, tg)
        if best is None or abs(len(best[1]) - len(gs)) > 0.15 * len(gs) + 2:
            out.extend(gs)
            continue
        size = gs[0].size
        for g in sorted(best[1], key=lambda g: g.seq):
            g.line, g.seq, g.size = lid, base + g.seq, size
            g.y0, g.y1 = y0, y1
            out.append(g)
        base += 100000
    return out


# ---------------------------------------------------------------------------
# Interfaz común
# ---------------------------------------------------------------------------


def pick_engine(preferred: str = "auto") -> str | None:
    """Devuelve 'tesseract', 'rapidocr' o None si no hay OCR."""
    if preferred == "rapidocr":
        return "rapidocr" if rapidocr_installed() else None
    if find_tesseract() and tesseract_langs():
        return "tesseract"
    if preferred == "tesseract":
        return None
    return "rapidocr" if rapidocr_installed() else None


def ocr_status() -> dict:
    """Estado del OCR, para mostrarlo en la interfaz y en los avisos."""
    langs = tesseract_langs() if find_tesseract() else ()
    return {
        "motor": pick_engine(),
        "tesseract": find_tesseract(),
        "idiomas": list(langs),
        "faltan": [l for l in NEEDED_LANGS if l not in langs] if find_tesseract() else NEEDED_LANGS,
        "rapidocr": rapidocr_installed(),
    }


MISSING_OCR_MESSAGE = (
    "Esta página está escaneada (no tiene texto) y no hay ningún programa de OCR instalado. "
    "Vuelve a ejecutar el instalador y responde «s» a la pregunta del OCR, o instala Tesseract "
    "con los idiomas jpn, jpn_vert, spa y eng."
)


def ocr_image(img, lang: str = "auto", engine: str = "auto", dpi: int = 0) -> tuple[list[Glyph], bool, list[str]]:
    """OCR de una imagen PIL. Devuelve (caracteres en puntos, vertical, avisos)."""
    dpi = dpi or OCR_DPI
    scale = 72.0 / dpi
    warnings: list[str] = []
    chosen = pick_engine(engine)
    if chosen is None:
        raise OcrUnavailable(MISSING_OCR_MESSAGE)
    from .assemble import detect_ruby
    from .glyphs import is_vertical_page

    if chosen == "rapidocr":
        glyphs = _rapid_glyphs(img, scale)
        vertical = is_vertical_page(glyphs)
        return detect_ruby(glyphs, vertical, from_ocr=True), vertical, warnings

    have = tesseract_langs()
    wanted = LANG_CODES.get(lang, LANG_CODES["auto"])
    langs = [l for l in wanted if l in have]
    missing = [l for l in wanted if l not in have]
    if missing:
        warnings.append("Faltan idiomas de OCR en Tesseract: " + ", ".join(missing) + ".")
    if not langs:
        langs = [have[0]] if have else ["eng"]
    vertical = lang in ("auto", "ja") and looks_vertical(img)
    if vertical and "jpn_vert" not in have:
        warnings.append("La página parece japonés vertical, pero falta el idioma «jpn_vert» de Tesseract.")
        vertical = False
    if vertical:
        glyphs = _tess_page(img, ["jpn_vert"], dpi, True)
        if _mean_conf(glyphs) < 55:  # mala señal: probar en horizontal y quedarse con lo mejor
            alt = _tess_page(img, langs, dpi, False)
            if _mean_conf(alt) * len(alt) > _mean_conf(glyphs) * len(glyphs) * 1.15:
                return alt, False, warnings
        return glyphs, True, warnings
    if engine == "auto" and "jpn" in langs and rapidocr_installed():
        # Japonés horizontal: RapidOCR (PP-OCR) lo lee mucho mejor que Tesseract, sobre todo
        # con furigana. Tesseract se queda con el español y con el japonés vertical.
        first = _tsv_to_glyphs(_run_tesseract(_png(img), langs, 3, dpi), scale, False)
        if sum(1 for g in first if is_kana(g.text[0]) or is_kanji(g.text[0])) >= 12:
            try:
                rapid = _rapid_glyphs(img, scale)
            except Exception as exc:  # modelo sin descargar, sin memoria…
                rapid = []
                warnings.append(f"RapidOCR no pudo leer la página ({type(exc).__name__}); se usa Tesseract.")
            if len(rapid) >= 0.6 * len(first):
                rapid = _use_tesseract_for_latin_lines(rapid, first)
                return detect_ruby(rapid, False, from_ocr=True), False, warnings
    return _tess_page(img, langs, dpi, False), False, warnings


def _tess_page(img, langs: list[str], dpi: int, vertical: bool) -> list[Glyph]:
    """Lee una página con Tesseract en varios pasos:

    1. Lectura de la página entera.
    2. Si hay japonés con furigana: se separa el furigana de la imagen y se relee.
    3. Los bloques que están lado a lado (columnas, frase y traducción) se releen
       uno a uno: Tesseract los mezcla si los ve juntos.
    4. El furigana se lee aparte y se une a sus kanji.
    """
    from .layout import xy_cut
    from .rubyimg import split_ruby

    scale = 72.0 / dpi
    psm = 5 if vertical else 3
    boxes: dict[int, tuple] = {}
    glyphs = _tsv_to_glyphs(_run_tesseract(_png(img), langs, psm, dpi), scale, vertical, boxes)
    main_img, ruby_img = img, None
    japanese = sum(1 for g in glyphs if is_kana(g.text[0]) or is_kanji(g.text[0])) >= 12
    if japanese and any(l.startswith("jpn") for l in langs):
        m_img, r_img, found = split_ruby(img, vertical, strip=max(120, int(220 * dpi / 300)), scale=dpi / 300)
        if r_img is not None and found >= 2:
            b2: dict[int, tuple] = {}
            again = _tsv_to_glyphs(_run_tesseract(_png(m_img), langs, psm, dpi), scale, vertical, b2)
            if len(again) >= 0.6 * len(glyphs):
                glyphs, boxes, main_img, ruby_img = again, b2, m_img, r_img

    if not vertical and glyphs:
        leaves = xy_cut(glyphs)
        rects = [(min(g.x0 for g in lf), min(g.y0 for g in lf), max(g.x1 for g in lf), max(g.y1 for g in lf)) for lf in leaves]
        if 2 <= len(leaves) <= 60:
            for k, (leaf, (x0, y0, x1, y1)) in enumerate(zip(leaves, rects)):
                gap = None  # distancia horizontal al vecino de al lado más cercano
                for j, (a0, b0, a1, b1) in enumerate(rects):
                    if j == k:
                        continue
                    overlap = min(y1, b1) - max(y0, b0)
                    if overlap <= 0.5 * min(y1 - y0, b1 - b0) or len(leaves[j]) < 3:
                        continue
                    d = a0 - x1 if a0 >= x1 else (x0 - a1 if a1 <= x0 else None)
                    if d is not None and (gap is None or d < gap):
                        gap = d
                if gap is None or len(leaf) < 3:
                    continue
                size = sorted(g.size for g in leaf)[len(leaf) // 2]
                padx = min(0.6 * size, max(gap / 2 - 1, 0))
                pady = 0.6 * size
                box = (
                    max(int((x0 - padx) / scale), 0), max(int((y0 - pady) / scale), 0),
                    min(int((x1 + padx) / scale) + 1, main_img.size[0]), min(int((y1 + pady) / scale) + 1, main_img.size[1]),
                )
                if box[2] - box[0] < 12 or box[3] - box[1] < 12:
                    continue
                lb: dict[int, tuple] = {}
                try:
                    crop_png = _png(main_img.crop(box))
                    part = _tsv_to_glyphs(_run_tesseract(crop_png, langs, 6, dpi), scale, False, lb)
                    if "jpn" in langs and len(langs) > 1 and _mean_conf(part) < 82:
                        # bloque dudoso (p. ej. una columna de kana leída como letras): probar solo japonés
                        lb2: dict[int, tuple] = {}
                        alt = _tsv_to_glyphs(_run_tesseract(crop_png, ["jpn"], 6, dpi), scale, False, lb2)
                        if alt and _mean_conf(alt) > _mean_conf(part) + 3 and len(alt) >= 0.7 * len(part):
                            part, lb = alt, lb2
                except RuntimeError:
                    continue
                if len(part) < 0.85 * len(leaf):
                    continue
                ox, oy = box[0] * scale, box[1] * scale
                base = (k + 1) * 100000
                for g in part:
                    g.x0, g.x1, g.y0, g.y1 = g.x0 + ox, g.x1 + ox, g.y0 + oy, g.y1 + oy
                    g.line += base
                    g.seq += base
                for lid, (l, t, r, bt) in lb.items():
                    boxes[lid + base] = (l + ox, t + oy, r + ox, bt + oy)
                old = {id(g) for g in leaf}
                glyphs = [g for g in glyphs if id(g) not in old] + part

    runs: list[dict] = []
    if ruby_img is not None:
        rlangs, rpsm = (["jpn_vert"], 5) if vertical else (["jpn"], 11)
        runs = _ruby_runs(_run_tesseract(_png(ruby_img), rlangs, rpsm, dpi), scale, vertical)
    if japanese:
        glyphs, more = _small_kana_lines(glyphs, boxes, vertical)
        runs += more
    if runs:
        _attach_ruby(glyphs, boxes, runs, vertical)
    return glyphs


def _small_kana_lines(glyphs: list[Glyph], boxes: dict[int, tuple], vertical: bool) -> tuple[list[Glyph], list[dict]]:
    """Restos de furigana que quedaron como líneas pequeñas: se tratan como lecturas."""
    by_line: dict[int, list[Glyph]] = {}
    for g in glyphs:
        by_line.setdefault(g.line, []).append(g)
    runs: list[dict] = []
    drop: set[int] = set()
    for lid, gs in by_line.items():
        if lid not in boxes or len(gs) > 8 or not all(is_kana(g.text[0]) or g.text == "ー" for g in gs):
            continue
        x0, y0, x1, y1 = boxes[lid]
        thick = (x1 - x0) if vertical else (y1 - y0)
        for other, (a0, b0, a1, b1) in boxes.items():
            if other == lid or other not in by_line:
                continue
            if vertical:
                o_thick, d, overlap = a1 - a0, x0 - a1, min(y1, b1) - max(y0, b0)
            else:
                o_thick, d, overlap = b1 - b0, b0 - y1, min(x1, a1) - max(x0, a0)
            if overlap > 0 and thick <= 0.66 * o_thick and -0.3 * thick <= d <= 0.9 * thick:
                text = "".join(g.text for g in sorted(gs, key=lambda g: g.seq))
                if vertical:
                    runs.append({"text": text, "a0": y0, "a1": y1, "p0": x0, "p1": x1})
                else:
                    runs.append({"text": text, "a0": x0, "a1": x1, "p0": y0, "p1": y1})
                drop.add(lid)
                break
    if not drop:
        return glyphs, runs
    return [g for g in glyphs if g.line not in drop], runs


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.convert("L").save(buf, "PNG")
    return buf.getvalue()


def _ruby_runs(rows: list[dict], scale: float, vertical: bool) -> list[dict]:
    """Agrupa las palabras del furigana en lecturas: [{text, a0, a1, p0, p1}] (a = eje de lectura)."""
    words = []
    for r in rows:
        if r.get("level") != "5":
            continue
        txt = "".join(c for c in (r.get("text") or "") if is_kana(c) or c == "ー")
        try:
            l, t, w, h = (int(r[k]) * scale for k in ("left", "top", "width", "height"))
            conf = float(r.get("conf", 0))
        except (ValueError, TypeError, KeyError):
            continue
        if not txt or conf < 35:
            continue
        if vertical:
            words.append({"text": txt, "a0": t, "a1": t + h, "p0": l, "p1": l + w})
        else:
            words.append({"text": txt, "a0": l, "a1": l + w, "p0": t, "p1": t + h})
    words.sort(key=lambda w: ((w["p0"] + w["p1"]) / 2, w["a0"]))
    rows_: list[list[dict]] = []
    for w in words:
        pc = (w["p0"] + w["p1"]) / 2
        for row in rows_:
            if row[0]["p0"] - 1 <= pc <= row[0]["p1"] + 1:
                row.append(w)
                break
        else:
            rows_.append([w])
    runs = []
    for row in rows_:
        row.sort(key=lambda w: w["a0"])
        cur = dict(row[0])
        hs = sorted(w["p1"] - w["p0"] for w in row)
        thick = max(hs[len(hs) // 2], 1.0)
        for w in row[1:]:
            if w["a0"] - cur["a1"] <= 1.2 * thick:
                cur["text"] += w["text"]
                cur["a1"] = max(cur["a1"], w["a1"])
                cur["p0"], cur["p1"] = min(cur["p0"], w["p0"]), max(cur["p1"], w["p1"])
            else:
                runs.append(cur)
                cur = dict(w)
        runs.append(cur)
    return runs


def _attach_ruby(glyphs: list[Glyph], boxes: dict[int, tuple], runs: list[dict], vertical: bool) -> None:
    """Une cada lectura con su grupo de kanji, línea a línea y en orden."""
    by_line: dict[int, list[Glyph]] = {}
    for g in glyphs:
        by_line.setdefault(g.line, []).append(g)
    per_line: dict[int, list[dict]] = {}
    for run in runs:
        best, bd = None, None
        for lid, (x0, y0, x1, y1) in boxes.items():
            if lid not in by_line:
                continue
            if vertical:  # la lectura va a la derecha de su columna
                a0, a1, edge, thick = y0, y1, x1, x1 - x0
                d = run["p0"] - edge
            else:  # la lectura va encima de su línea
                a0, a1, edge, thick = x0, x1, y0, y1 - y0
                d = edge - run["p1"]
            if run["a1"] < a0 or run["a0"] > a1:
                continue
            if -0.4 * thick <= d <= 1.0 * thick and (bd is None or abs(d) < bd):
                best, bd = lid, abs(d)
        if best is not None:
            per_line.setdefault(best, []).append(run)
    group = 0
    for lid, rr in per_line.items():
        rr.sort(key=lambda r: r["a0"])
        gs = sorted(by_line[lid], key=lambda g: g.y0 if vertical else g.x0)
        kruns: list[list[Glyph]] = []
        prev_kanji = False
        for g in gs:
            k = is_kanji(g.text[0])
            if k and prev_kanji:
                kruns[-1].append(g)
            elif k:
                kruns.append([g])
            prev_kanji = k
        if not kruns:
            continue

        def span(kr: list[Glyph]) -> tuple[float, float]:
            return (kr[0].y0, kr[-1].y1) if vertical else (kr[0].x0, kr[-1].x1)

        pairs: list[tuple[list[Glyph], dict]] = []
        if len(kruns) == len(rr):
            pairs = list(zip(kruns, rr))
        else:
            start = 0
            for r in rr:
                rc = (r["a0"] + r["a1"]) / 2
                best_i, bd = None, None
                for i in range(start, len(kruns)):
                    a, b = span(kruns[i])
                    d = 0.0 if a <= rc <= b else min(abs(rc - a), abs(rc - b))
                    if bd is None or d < bd:
                        best_i, bd = i, d
                if best_i is not None and bd is not None and bd <= 1.5 * kruns[best_i][0].size:
                    pairs.append((kruns[best_i], r))
                    start = best_i + 1
        for kr, r in pairs:
            for g in kr:
                g.group = group
            kr[-1].ruby = r["text"]
            group += 1


def _mean_conf(glyphs: list[Glyph]) -> float:
    return sum(g.conf for g in glyphs) / len(glyphs) if glyphs else 0.0


def read_cell(img, box_pt: tuple[float, float, float, float], dpi: int, lang: str = "auto") -> tuple[str, float]:
    """Relee una celda de tabla suelta (una línea). Prueba japonés y latino y se queda con lo más fiable."""
    if pick_engine("tesseract") != "tesseract":
        return "", 0.0
    k = dpi / 72.0
    pad = 3
    box = (max(int(box_pt[0] * k) - pad, 0), max(int(box_pt[1] * k) - pad, 0),
           min(int(box_pt[2] * k) + pad, img.size[0]), min(int(box_pt[3] * k) + pad, img.size[1]))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return "", 0.0
    from PIL import ImageOps

    png = _png(ImageOps.expand(img.crop(box).convert("L"), border=12, fill=255))
    have = tesseract_langs()
    tries = []
    if lang in ("auto", "ja") and "jpn" in have:
        tries.append(["jpn"])
    if lang != "ja":
        latin = [l for l in ("spa", "eng") if l in have]
        if latin:
            tries.append(latin)
    best = ("", 0.0)
    for langs in tries:
        try:
            gs = _tsv_to_glyphs(_run_tesseract(png, langs, 7, dpi), 72.0 / dpi, False)
        except RuntimeError:
            continue
        if not gs:
            continue
        conf = _mean_conf(gs)
        if conf > best[1]:
            from .assemble import line_text

            best = (line_text(sorted(gs, key=lambda g: g.seq), False, False), conf)
    return best


def page_dpi(pdf_page) -> int:
    """Resolución para el OCR: la de la imagen escaneada, si la página es una imagen."""
    try:
        import pypdfium2.raw as c

        w, h = pdf_page.get_size()
        best = None
        for obj in pdf_page.get_objects(filter=[c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
            l, b, r, t = obj.get_bounds()
            if (r - l) * (t - b) > 0.5 * w * h:
                px = obj.get_px_size()[0]
                best = max(best or 0, px / ((r - l) / 72.0))
        if best:
            return int(min(max(best, 200), 400))
    except Exception:
        pass
    return OCR_DPI


def render_for_ocr(pdf_page):
    dpi = page_dpi(pdf_page)
    return pdf_page.render(scale=dpi / 72.0, grayscale=True).to_pil(), dpi


def ocr_page(pdf_page, number: int, lang: str = "auto", engine: str = "auto") -> PageText:
    """Renderiza una página de pypdfium2 y le pasa el OCR."""
    width, height = pdf_page.get_size()
    img, dpi = render_for_ocr(pdf_page)
    glyphs, vertical, warnings = ocr_image(img, lang, engine, dpi)
    page = PageText(number=number, width=width, height=height, glyphs=glyphs, source="ocr")
    page.vertical = vertical
    page.warnings = warnings
    return page
