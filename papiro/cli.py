"""Línea de comandos de Papiro.

    papiro archivo.pdf -o salida.md
    papiro carpeta/ -o markdown/
    papiro a.docx b.pptx c.xlsx
    cat archivo.pdf | papiro - > salida.md
    papiro https://ejemplo.org/pagina.html
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__
from .converter import Converter, iter_files


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="papiro",
        description="Convierte documentos (PDF, Word, PowerPoint, Excel, HTML, EPUB, imágenes…) a Markdown limpio para IA. Todo en local.",
        epilog="Sin argumentos se abre la interfaz gráfica.",
    )
    p.add_argument("entradas", nargs="*", metavar="ENTRADA", help="archivos, carpetas, URLs o «-» para leer de la entrada estándar")
    p.add_argument("-o", "--salida", metavar="RUTA", help="archivo .md de salida (una entrada) o carpeta (varias)")
    p.add_argument("--modo", choices=["auto", "calidad", "rapido"], default="auto",
                   help="calidad = maqueta con docling; rapido = ligero, sin modelos (por defecto: auto)")
    p.add_argument("--ocr", choices=["auto", "siempre", "nunca"], default="auto",
                   help="auto = solo páginas escaneadas o ilegibles (por defecto)")
    p.add_argument("--idioma", choices=["auto", "ja", "es", "en"], default="auto", help="idioma del OCR (por defecto: auto)")
    p.add_argument("--motor-ocr", choices=["auto", "tesseract", "rapidocr"], default="auto", help="motor de OCR")
    p.add_argument("--sin-furigana", action="store_true", help="quita las lecturas: 漢字 en vez de 漢字(かんじ)")
    p.add_argument("--marcar-paginas", action="store_true", help="añade <!-- página N --> al empezar cada página")
    p.add_argument("--formato", metavar="EXT", help="formato de la entrada estándar (pdf, docx, html…); si falta, se detecta")
    p.add_argument("--no-recursivo", action="store_true", help="no entrar en subcarpetas")
    p.add_argument("--estado", action="store_true", help="muestra qué motores hay instalados y sale")
    p.add_argument("--descargar-modelos", action="store_true", help="descarga los modelos de docling (modo calidad) y sale")
    p.add_argument("--instalar-idiomas", action="store_true",
                   help="descarga los idiomas de Tesseract (jpn, jpn_vert, spa, eng) a la carpeta de Papiro y sale")
    p.add_argument("--gui", action="store_true", help="abre la interfaz gráfica")
    p.add_argument("-q", "--silencio", action="store_true", help="no mostrar progreso ni avisos")
    p.add_argument("--version", action="version", version=f"papiro {__version__}")
    return p


def print_status() -> int:
    from .pdf import ocr
    from .pdf.layout import docling_installed

    st = ocr.ocr_status()
    print(f"Papiro {__version__}")
    print("Modo calidad (docling): " + ("instalado" if docling_installed() else "NO instalado (se usa el modo rápido)"))
    if st["tesseract"]:
        print(f"Tesseract: {st['tesseract']}")
        print("  idiomas: " + (", ".join(st["idiomas"]) or "ninguno"))
        if st["faltan"]:
            print("  FALTAN: " + ", ".join(st["faltan"]))
    else:
        print("Tesseract: NO instalado")
    print("RapidOCR: " + ("instalado" if st["rapidocr"] else "no instalado"))
    print("OCR activo: " + (st["motor"] or "NINGUNO: las páginas escaneadas no se podrán convertir"))
    return 0 if st["motor"] else 1


TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"


def install_languages() -> int:
    """Descarga los modelos de idioma oficiales de Tesseract a ~/.papiro/tessdata."""
    import urllib.request

    from .pdf import ocr

    target = ocr.app_dir() / "tessdata"
    target.mkdir(parents=True, exist_ok=True)
    ok = True
    for lang in ocr.NEEDED_LANGS + ["osd"]:
        dest = target / f"{lang}.traineddata"
        if dest.exists() and dest.stat().st_size > 100_000:
            print(f"  {lang}: ya estaba")
            continue
        try:
            print(f"  {lang}: descargando…", flush=True)
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(TESSDATA_URL.format(lang=lang), tmp)
            tmp.replace(dest)
        except Exception as exc:
            ok = False
            print(f"  {lang}: ERROR al descargar ({exc})", file=sys.stderr)
    ocr.reset_cache()
    print(f"Idiomas en: {target}")
    return 0 if ok else 1


def _progress(quiet: bool):
    if quiet or not sys.stderr.isatty():
        return None

    def show(done: int, total: int, msg: str) -> None:
        pct = int(100 * done / total) if total else 0
        sys.stderr.write(f"\r\033[K[{pct:3d}%] {msg[:90]}")
        sys.stderr.flush()
        if total and done >= total:
            sys.stderr.write("\r\033[K")

    return show


def _out_name(src: str) -> str:
    if re.match(r"^https?://", src, re.IGNORECASE):
        name = re.sub(r"[^\w.-]+", "_", re.sub(r"^https?://", "", src)).strip("_")[:80] or "pagina"
        return name + ".md"
    return Path(src).stem + ".md"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if args.estado:
        return print_status()
    if args.descargar_modelos:
        from .pdf.convert import get_docling
        from .pdf.layout import docling_installed

        if not docling_installed():
            print("docling no está instalado: no hay modelos que descargar.", file=sys.stderr)
            return 1
        print("Descargando los modelos de docling (solo la primera vez)…", file=sys.stderr)
        get_docling().preload()
        from .pdf import ocr

        if ocr.rapidocr_installed():
            print("Descargando los modelos de OCR japonés (RapidOCR)…", file=sys.stderr)
            ocr._rapid_engine()
        print("Modelos listos.", file=sys.stderr)
        return 0
    if args.instalar_idiomas:
        return install_languages()
    if args.gui or not args.entradas:
        if not args.gui and not sys.stdin.isatty() and not args.entradas:
            args.entradas = ["-"]
        else:
            from .gui import main as gui_main

            return gui_main(args.entradas)

    conv = Converter(mode=args.modo, ocr=args.ocr, lang=args.idioma, furigana=not args.sin_furigana,
                     ocr_engine=args.motor_ocr, page_marks=args.marcar_paginas)

    # Expandir carpetas
    jobs: list[tuple[str, Path | None]] = []  # (entrada, carpeta base para conservar la estructura)
    for e in args.entradas:
        if e != "-" and not re.match(r"^https?://", e, re.IGNORECASE) and Path(e).is_dir():
            files = iter_files(e, recursive=not args.no_recursivo)
            if not files:
                print(f"papiro: la carpeta «{e}» no contiene archivos compatibles.", file=sys.stderr)
            jobs.extend((str(f), Path(e)) for f in files)
        else:
            jobs.append((e, None))
    if not jobs:
        return 1

    many = len(jobs) > 1
    out = Path(args.salida) if args.salida else None
    out_is_dir = out is not None and (many or out.is_dir() or args.salida.endswith(("/", "\\")))
    if out_is_dir:
        out.mkdir(parents=True, exist_ok=True)

    failures = 0
    for src, base in jobs:
        try:
            if src == "-":
                data = sys.stdin.buffer.read()
                ext = ("." + args.formato.lstrip(".")) if args.formato else None
                res = conv.convert(data, extension=ext, progress=_progress(args.silencio))
            else:
                res = conv.convert(src, progress=_progress(args.silencio))
        except KeyboardInterrupt:
            print("\npapiro: cancelado.", file=sys.stderr)
            return 130
        except Exception as exc:
            failures += 1
            print(f"papiro: ERROR en «{src}»: {exc}", file=sys.stderr)
            continue
        if not args.silencio:
            for w in res.warnings:
                print(f"papiro: aviso ({src}): {w}", file=sys.stderr)
        if out is None and not many:
            sys.stdout.write(res.text_content)
            continue
        if out is None:
            target = (Path(src).with_suffix(".md") if src != "-" and not src.lower().startswith("http")
                      else Path(_out_name(src if src != "-" else "entrada")))
        elif out_is_dir:
            rel = Path(src).relative_to(base).with_suffix(".md") if base is not None else Path(_out_name(src if src != "-" else "entrada"))
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            target = out
            if target.parent != Path(""):
                target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(res.text_content, encoding="utf-8")
        if not args.silencio:
            print(f"papiro: {src} -> {target}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
