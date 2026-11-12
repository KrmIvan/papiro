import difflib
import os
import re
from pathlib import Path

import pytest

PDFS = Path(__file__).parent / "fixtures" / "pdf"


def _has_ocr() -> bool:
    from papiro.pdf import ocr

    st = ocr.ocr_status()
    return st["motor"] == "tesseract" and not st["faltan"]


def _has_docling() -> bool:
    from papiro.pdf.layout import docling_installed

    return docling_installed() and os.environ.get("PAPIRO_TEST_CALIDAD", "1") != "0"


def pytest_collection_modifyitems(config, items):
    ocr = _has_ocr()
    docling = _has_docling()
    for item in items:
        mode = getattr(getattr(item, "callspec", None), "params", {}).get("mode")
        if "ocr" in item.keywords and not ocr:
            item.add_marker(pytest.mark.skip(reason="Tesseract con jpn, jpn_vert, spa y eng no está instalado"))
        if ("calidad" in item.keywords or mode == "calidad") and not docling:
            item.add_marker(pytest.mark.skip(reason="docling no está instalado"))


@pytest.fixture(scope="session")
def pdf():
    return lambda name: str(PDFS / name)


_cache: dict = {}


@pytest.fixture(scope="session")
def convert():
    """Convierte un PDF de prueba (con caché: el modo calidad es lento)."""
    from papiro import Converter

    def run(name: str, mode: str = "rapido", **kw):
        key = (name, mode, tuple(sorted(kw.items())))
        if key not in _cache:
            _cache[key] = Converter(mode=mode, **kw).convert(str(PDFS / name))
        return _cache[key]

    return run


def similarity(expected: str, got: str) -> float:
    """Fracción de los caracteres esperados que aparecen, en orden, en el resultado."""
    a = re.sub(r"\s+", "", expected)
    b = re.sub(r"\s+", "", got)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in sm.get_matching_blocks()) / max(len(a), 1)


def in_order(text: str, *parts: str) -> bool:
    pos = -1
    for p in parts:
        nxt = text.find(p, pos + 1)
        if nxt < 0:
            return False
        pos = nxt
    return True


JA_SPACE = re.compile(r"[぀-ヿ一-鿿] +[぀-ヿ一-鿿]")
