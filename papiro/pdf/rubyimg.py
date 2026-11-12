"""Separa el furigana de la imagen de una página escaneada antes del OCR.

El furigana confunde a cualquier OCR: son letras diminutas pegadas a la línea
principal. Aquí se localizan esas tiras finas (por los huecos en blanco entre
filas, o entre columnas si el texto es vertical), se borran de la imagen
principal y se copian a una imagen aparte, que se lee por separado.
"""

from __future__ import annotations


def _bands(profile: list[int], min_ink: int) -> list[tuple[int, int]]:
    bands = []
    start = None
    for i, v in enumerate(profile):
        if v >= min_ink:
            if start is None:
                start = i
        elif start is not None:
            bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(profile)))
    merged: list[tuple[int, int]] = []
    for b in bands:  # huecos de 1-2 píxeles: es la misma tira
        if merged and b[0] - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return merged


def split_ruby(img, vertical: bool, strip: int = 220, scale: float = 1.0):
    """Devuelve (imagen sin furigana, imagen solo con furigana o None, nº de tiras)."""
    from PIL import Image, ImageFilter

    gray = img.convert("L")
    ink = gray.filter(ImageFilter.MedianFilter(3)).point(lambda v: 255 if v < 150 else 0)
    if vertical:  # se trabaja girado: las columnas pasan a ser filas, el furigana queda encima
        ink = ink.transpose(Image.ROTATE_90)
        gray_w = gray.transpose(Image.ROTATE_90)
    else:
        gray_w = gray
    w, h = ink.size
    main = gray_w.copy()
    ruby = Image.new("L", (w, h), 255)
    found = 0
    for x0 in range(0, w, strip):
        x1 = min(x0 + strip, w)
        col = ink.crop((x0, 0, x1, h)).resize((1, h), Image.BOX)
        # media 0..255 de la fila -> nº aproximado de píxeles de tinta
        prof = [round(v * (x1 - x0) / 255) for v in col.tobytes()]
        bands = _bands(prof, 2)
        for (a0, a1), (b0, b1) in zip(bands, bands[1:]):
            ha, hb, gap = a1 - a0, b1 - b0, b0 - a1
            if ha < 10 * scale or hb < 24 * scale:
                continue
            if not (0.28 * hb <= ha <= 0.66 * hb):
                continue
            if gap > 0.6 * ha + 1:
                continue
            box = (x0, max(a0 - 1, 0), x1, min(a1 + 1, h))
            ruby.paste(gray_w.crop(box), box)
            main.paste(255, box)
            found += 1
    if vertical:
        main = main.transpose(Image.ROTATE_270)
        ruby = ruby.transpose(Image.ROTATE_270)
    return main, (ruby if found else None), found
