"""Utilidades de texto: detección de idioma, japonés, Unicode y unión de líneas."""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Clases de caracteres
# ---------------------------------------------------------------------------

_JA_PUNCT = "。、「」『』・…‥〜ー（）［］【】〈〉《》！？：；，．　〃々〆〇"


def is_hiragana(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ"


def is_katakana(ch: str) -> bool:
    return "゠" <= ch <= "ヿ" or "ㇰ" <= ch <= "ㇿ" or "ｦ" <= ch <= "ﾟ"


def is_kana(ch: str) -> bool:
    return is_hiragana(ch) or is_katakana(ch)


def is_kanji(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or 0x20000 <= o <= 0x2FA1F
        or ch in "々〆〇ヶ"
    )


def is_cjk(ch: str) -> bool:
    """Carácter japonés/CJK: kana, kanji, puntuación japonesa o de ancho completo."""
    if not ch:
        return False
    o = ord(ch)
    return (
        is_kana(ch)
        or is_kanji(ch)
        or 0x3000 <= o <= 0x303F
        or 0xFF00 <= o <= 0xFFEF
        or ch in _JA_PUNCT
    )


def is_latin_letter(ch: str) -> bool:
    return ch.isalpha() and not is_cjk(ch) and ord(ch) < 0x250


# ---------------------------------------------------------------------------
# Detección de idioma
# ---------------------------------------------------------------------------

_ES_WORDS = set(
    "el la los las de del que y en un una es por con para no se su al lo como más pero "
    "sus le ya o este sí porque esta entre cuando muy sin sobre también me hasta hay donde "
    "quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos e esto "
    "mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada "
    "muchos cual poco ella estar estas algunas algo nosotros mi mis tú te ti tu tus".split()
)
_EN_WORDS = set(
    "the of and to in is that it for was on are as with his they at be this from have or "
    "one had by but not what all were we when your can said there use an each which she do "
    "how their if will up other about out many then them these so some her would make like "
    "him into time has look two more write go see no way could people my than first been "
    "who its now find long down day did get come made may part".split()
)


def script_counts(text: str) -> tuple[int, int, int]:
    """Devuelve (kana, kanji, letras latinas)."""
    kana = kanji = latin = 0
    for ch in text:
        if is_kana(ch):
            kana += 1
        elif is_kanji(ch):
            kanji += 1
        elif is_latin_letter(ch):
            latin += 1
    return kana, kanji, latin


def detect_language(text: str) -> str:
    """Devuelve 'ja', 'es', 'en', 'mixto' (japonés + latino) o 'desconocido'."""
    kana, kanji, latin = script_counts(text)
    ja = kana + kanji
    if ja + latin < 3:
        return "desconocido"
    # Un carácter japonés lleva tanta información como ~3 letras latinas.
    ja_w = ja * 3
    share = ja_w / (ja_w + latin)
    if share > 0.85:
        return "ja"
    if share > 0.12:
        return "mixto"
    words = re.findall(r"[a-záéíóúüñ]+", text.lower())
    es = sum(w in _ES_WORDS for w in words) + 2 * len(re.findall(r"[ñáéíóú¿¡]", text.lower()))
    en = sum(w in _EN_WORDS for w in words)
    return "en" if en > es else "es"


def is_japanese_dominant(text: str) -> bool:
    return detect_language(text) == "ja"


# ---------------------------------------------------------------------------
# Normalización Unicode
# ---------------------------------------------------------------------------


def _needs_nfkc(ch: str) -> bool:
    o = ord(ch)
    return (
        0xFF61 <= o <= 0xFF9F  # katakana de ancho medio -> ancho completo
        or 0xFB00 <= o <= 0xFB06  # ligaduras latinas ﬁ ﬂ
        or 0x2F00 <= o <= 0x2FDF  # radicales Kangxi (⽇ en vez de 日)
        or 0x2E80 <= o <= 0x2EFF  # suplemento de radicales CJK
        or 0xF900 <= o <= 0xFAFF  # ideogramas de compatibilidad
        or 0xFE10 <= o <= 0xFE19  # puntuación en forma vertical (︑ ︒)
        or 0xFE30 <= o <= 0xFE4F  # comillas y paréntesis en forma vertical (﹁ ﹂)
        or o in (0x00A0, 0x2007, 0x202F)  # espacios duros
    )


_CJK_RADICALS_SUPP = {
    "⺟": "母", "⻑": "長", "⻄": "西", "⻘": "青", "⻝": "食", "⻤": "鬼", "⻨": "麦",
    "⻩": "黄", "⻫": "斉", "⻭": "歯", "⻯": "竜", "⻲": "亀", "⺠": "民", "⻁": "虎",
    "⻆": "角", "⻋": "車", "⻑": "長", "⻔": "門", "⻗": "雨", "⻚": "頁", "⻛": "風",
    "⻜": "飛", "⻢": "馬", "⻥": "魚", "⻦": "鳥", "⻬": "斉", "⼾": "戸",
}


def normalize_text(text: str) -> str:
    """Normaliza Unicode sin romper el japonés.

    - NFC siempre (une «か» + dakuten suelto, «a» + tilde suelta).
    - NFKC solo donde arregla errores típicos de PDF: katakana de ancho medio,
      ligaduras ﬁ/ﬂ, radicales Kangxi usados en lugar de kanji y espacios duros.
    - La puntuación y los caracteres de ancho completo se respetan.
    """
    if not text:
        return text
    out = []
    buf: list[str] = []  # tramo de katakana de ancho medio (para unir ｶﾞ -> ガ)
    for ch in text:
        if 0xFF61 <= ord(ch) <= 0xFF9F:
            buf.append(ch)
            continue
        if buf:
            out.append(unicodedata.normalize("NFKC", "".join(buf)))
            buf = []
        if ch in _CJK_RADICALS_SUPP:
            out.append(_CJK_RADICALS_SUPP[ch])
        elif _needs_nfkc(ch):
            out.append(unicodedata.normalize("NFKC", ch))
        elif ch == "­":  # guion blando
            continue
        else:
            out.append(ch)
    if buf:
        out.append(unicodedata.normalize("NFKC", "".join(buf)))
    return unicodedata.normalize("NFC", "".join(out))


# ---------------------------------------------------------------------------
# Texto ilegible (fuentes CJK mal codificadas, "mojibake")
# ---------------------------------------------------------------------------

_EXPECTED_LATIN1 = set("áéíóúüñçàèìòùâêîôûäëïöÁÉÍÓÚÜÑÇÀÈÌÒÙÂÊÎÔÛÄËÏÖß¿¡«»·ºª°±×÷§©®¢£¥€½¼¾²³µ¶´¨")
_CID_RE = re.compile(r"\(cid:\d+\)")
# Mojibake clásico: UTF-8 o Shift-JIS leído como Latin-1/CP1252.
_MOJI_RE = re.compile(r"[ÃÂãâ][\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ¡-¿]|ï¿½|[ƒ‚][A-Za-z@-_]{1}[ƒ‚]")


def broken_text_score(text: str) -> float:
    """0 = texto normal; 1 = texto ilegible. Por encima de 0.25 conviene OCR."""
    chars = [c for c in text if not c.isspace()]
    n = len(chars)
    if n == 0:
        return 0.0
    cid = len(_CID_RE.findall(text)) * 6
    bad = 0
    ctrl = 0
    odd_latin1 = 0
    cjk_total = 0
    cjk_rare = 0
    kana = 0
    for ch in chars:
        o = ord(ch)
        cat = unicodedata.category(ch)
        if o == 0xFFFD or cat in ("Co", "Cn", "Cc", "Cs") or 0xE000 <= o <= 0xF8FF:
            bad += 1
            ctrl += cat == "Cc"
        elif 0x80 <= o <= 0xFF and ch not in _EXPECTED_LATIN1:
            odd_latin1 += 1
        elif is_kana(ch):
            kana += 1
            cjk_total += 1
        elif is_kanji(ch):
            cjk_total += 1
            try:
                ch.encode("cp932")
            except UnicodeEncodeError:
                cjk_rare += 1
    score = (bad + cid) / n
    if ctrl >= 3 and ctrl / n > 0.015:
        score = max(score, 0.6)  # caracteres de control: glifos sin mapa a Unicode
    if odd_latin1 >= 4 and odd_latin1 / n > 0.03:
        score = max(score, 0.5)  # Ö Å ý Þ Ð…: codificación equivocada
    score = max(score, min(1.0, len(_MOJI_RE.findall(text)) * 2 / n * 3))
    if cjk_total >= 20 and cjk_rare / cjk_total > 0.2:
        score = max(score, cjk_rare / cjk_total)
    # Texto "latino" sin vocales ni espacios: glifos sin mapa a Unicode.
    letters = [c for c in chars if c.isalpha() and ord(c) < 0x250]
    if len(letters) >= 40 and len(letters) / n > 0.7:
        vowels = sum(c.lower() in "aeiouáéíóúü" for c in letters)
        if vowels / len(letters) < 0.12:
            score = max(score, 0.6)
    # Muchos símbolos y casi ninguna letra.
    if n >= 40:
        alnum = sum(c.isalnum() for c in chars)
        if alnum / n < 0.35:
            score = max(score, 0.5)
    return min(1.0, score)


def looks_broken(text: str, threshold: float = 0.25) -> bool:
    return broken_text_score(text) >= threshold


# ---------------------------------------------------------------------------
# Unión de líneas
# ---------------------------------------------------------------------------

_HYPHENS = "-‐‑"
_RUBY_END = re.compile(r"[\u3400-\u9fff々〆ヶ]\([\u3041-\u30ffー]+\)$")


def join_lines(lines: list[str]) -> str:
    """Une las líneas de un párrafo.

    - Entre dos caracteres japoneses no se pone espacio.
    - Una palabra latina partida con guion a final de línea se vuelve a unir.
    - En el resto de casos se pone un espacio.
    """
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        a, b = out[-1], line[0]
        if is_cjk(a) and b in "?!:;,.":
            out += line
            continue
        if a == ")" and _RUBY_END.search(out):
            a = "字"  # la línea acaba en 漢字(かんじ): cuenta como japonés
        if is_cjk(a) and is_cjk(b):
            out += line
        elif (
            a in _HYPHENS
            and len(out) >= 2
            and is_latin_letter(out[-2])
            and is_latin_letter(b)
            and b.islower()
        ):
            out = out[:-1] + line
        elif a == "­":
            out = out[:-1] + line
        elif is_cjk(a) and b in ".,;:!?)»”" or is_cjk(b) and a in "(«“¿¡":
            out += line
        else:
            out += " " + line
    return out


_SPACE_BETWEEN_CJK = re.compile(r"(?<=[぀-ヿ㐀-鿿　-〿＀-￯]) +(?=[぀-ヿ㐀-鿿　-〿＀-￯])")


def strip_cjk_spaces(text: str) -> str:
    """Quita los espacios que el OCR mete entre caracteres japoneses."""
    return _SPACE_BETWEEN_CJK.sub("", text)


_RUBY_PAREN = re.compile(r"(?<=[㐀-鿿々〆ヶ])[(（][ぁ-ゟ゠-ヿー]+[)）]")


def strip_furigana(text: str) -> str:
    """Quita lecturas del tipo 漢字(かんじ) de un Markdown ya generado."""
    return _RUBY_PAREN.sub("", text)


def escape_md(text: str) -> str:
    """Escapa lo mínimo para que el texto no se interprete como formato Markdown."""
    text = re.sub(r"^(\s*)([#>+\-*])(\s)", r"\1\\\2\3", text)
    text = re.sub(r"^(\s*\d+)([.)])(\s)", r"\1\\\2\3", text)
    return text.replace("|", "\\|") if False else text


def clean_markdown(md: str) -> str:
    md = md.replace("\r\n", "\n").replace("\r", "\n")
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n" if md.strip() else ""
