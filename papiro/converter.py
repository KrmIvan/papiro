"""Punto de entrada: ``Converter().convert("archivo.pdf").text_content``."""

from __future__ import annotations

import io
import os
import re
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import urlparse

from . import formats
from .textutil import clean_markdown, strip_furigana

MAX_ZIP_MEMBER = 300 * 1024 * 1024
MAX_ZIP_DEPTH = 2


class UnsupportedFormat(ValueError):
    """Formato de archivo que Papiro no sabe convertir."""


@dataclass
class ConversionResult:
    text_content: str
    title: str = ""
    source: str = ""
    warnings: list[str] = field(default_factory=list)
    pages: int = 0
    ocr_pages: list[int] = field(default_factory=list)
    mode: str = ""

    @property
    def markdown(self) -> str:
        return self.text_content

    def __str__(self) -> str:
        return self.text_content


class Converter:
    """Convierte documentos a Markdown, todo en local.

    Parámetros:
        mode: "auto" (calidad si docling está instalado), "calidad" o "rapido".
        ocr: "auto" (solo páginas sin texto o ilegibles), "siempre" o "nunca".
        lang: "auto", "ja", "es" o "en" (idioma del OCR).
        furigana: True deja las lecturas como 漢字(かんじ); False las quita.
        ocr_engine: "auto", "tesseract" o "rapidocr".
        page_marks: añade un comentario ``<!-- página N -->`` al empezar cada página.
    """

    def __init__(self, mode: str = "auto", ocr: str = "auto", lang: str = "auto", furigana: bool = True,
                 ocr_engine: str = "auto", page_marks: bool = False) -> None:
        self.mode = mode
        self.ocr = ocr
        self.lang = lang
        self.furigana = furigana
        self.ocr_engine = ocr_engine
        self.page_marks = page_marks

    # -- API pública --------------------------------------------------------

    def convert(self, source, *, extension: str | None = None,
                progress: Callable[[int, int, str], None] | None = None,
                cancel: Callable[[], bool] | None = None) -> ConversionResult:
        """Convierte una ruta, una URL, unos bytes o un flujo binario."""
        if isinstance(source, (bytes, bytearray)):
            return self.convert_bytes(bytes(source), extension or formats.sniff_extension(bytes(source)),
                                      progress=progress, cancel=cancel)
        if hasattr(source, "read"):
            return self.convert_stream(source, extension, progress=progress, cancel=cancel)
        text = os.fspath(source)
        if re.match(r"^https?://", text, re.IGNORECASE):
            return self.convert_url(text, progress=progress, cancel=cancel)
        path = Path(text)
        if not path.exists():
            raise FileNotFoundError(f"No existe el archivo: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"«{path}» es una carpeta. Usa convert_folder() o pasa los archivos uno a uno.")
        ext = (extension or path.suffix).lower()
        if ext == ".pdf":  # el PDF se lee directamente del disco: no se carga entero en memoria
            return self._finish(self._pdf(str(path), progress, cancel), str(path))
        data = path.read_bytes()
        if ext not in formats.SUPPORTED:
            guess = formats.sniff_extension(data)
            if guess == ".txt" and b"\x00" in data[:4096]:
                raise UnsupportedFormat(f"Formato no compatible: «{path.suffix or path.name}».")
            ext = guess
        return self._finish(self._dispatch(data, ext, str(path), progress, cancel), str(path))

    def convert_stream(self, stream: BinaryIO, extension: str | None = None, **kw) -> ConversionResult:
        data = stream.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return self.convert_bytes(data, extension or formats.sniff_extension(data), **kw)

    def convert_bytes(self, data: bytes, extension: str, *, name: str = "", progress=None, cancel=None) -> ConversionResult:
        ext = extension.lower() if extension.startswith(".") else "." + extension.lower()
        return self._finish(self._dispatch(data, ext, name or f"entrada{ext}", progress, cancel), name)

    def convert_url(self, url: str, *, progress=None, cancel=None) -> ConversionResult:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Papiro)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                ctype = (resp.headers.get_content_type() or "").lower()
        except Exception as exc:
            raise ConnectionError(f"No se pudo descargar {url}: {exc}") from exc
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in formats.SUPPORTED or ext in (".htm", ".html"):
            ext = formats.CONTENT_TYPES.get(ctype) or formats.sniff_extension(data)
        return self._finish(self._dispatch(data, ext, url, progress, cancel), url)

    def supported_extensions(self) -> list[str]:
        return sorted(formats.SUPPORTED)

    # -- Interno ------------------------------------------------------------

    def _finish(self, res: ConversionResult, source: str) -> ConversionResult:
        res.source = source
        if not self.furigana:
            res.text_content = strip_furigana(res.text_content)
        res.text_content = clean_markdown(res.text_content)
        if not res.text_content.strip():
            msg = "El archivo no contiene texto que se pueda convertir."
            res.warnings.append(msg)
            res.text_content = f"> **Aviso:** {msg}\n"
        m = re.search(r"^#{1,6}\s+(.+)$", res.text_content, re.MULTILINE)
        res.title = m.group(1).strip() if m else ""
        return res

    def _pdf(self, path: str, progress, cancel) -> ConversionResult:
        from .pdf.convert import PdfOptions, convert_pdf

        opts = PdfOptions(mode=self.mode, ocr=self.ocr, lang=self.lang, furigana=self.furigana,
                          ocr_engine=self.ocr_engine, page_marks=self.page_marks, progress=progress, cancel=cancel)
        r = convert_pdf(path, opts)
        return ConversionResult(r.markdown, warnings=r.warnings, pages=r.pages, ocr_pages=r.ocr_pages, mode=r.mode)

    def _pdf_bytes(self, data: bytes, progress, cancel) -> ConversionResult:
        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="papiro_")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            return self._pdf(tmp, progress, cancel)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _image(self, data: bytes, progress, cancel) -> ConversionResult:
        from PIL import Image, ImageOps

        try:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)
        except Exception as exc:
            raise ValueError(f"No se pudo abrir la imagen: {exc}") from exc
        frames = []
        try:
            for i in range(getattr(img, "n_frames", 1)):
                img.seek(i)
                # gris con paleta: Pillow lo guarda en el PDF sin pérdida (RGB iría como JPEG)
                frames.append(img.convert("L").convert("P"))
                if i >= 199:
                    break
        except EOFError:
            pass
        dpi = img.info.get("dpi", (0, 0))[0] or 0
        # Sin resolución declarada se supone una foto o captura: 200 ppp da buen tamaño para el OCR
        res = float(dpi) if 100 <= float(dpi) <= 600 else 200.0
        buf = io.BytesIO()
        frames[0].save(buf, "PDF", resolution=res, save_all=len(frames) > 1, append_images=frames[1:])
        out = self._pdf_bytes(buf.getvalue(), progress, cancel)
        out.warnings = [w.replace("página(s)", "imagen(es)") if w.startswith("Se aplicó OCR") else w for w in out.warnings]
        return out

    def _zip(self, data: bytes, name: str, progress, cancel, depth: int = 0) -> ConversionResult:
        parts = [f"# {Path(name).name or 'archivo.zip'}"]
        warnings: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [m for m in z.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX/")]
            members.sort(key=lambda m: m.filename)
            for i, m in enumerate(members):
                if cancel and cancel():
                    from .pdf.convert import ConversionCancelled

                    raise ConversionCancelled()
                if progress:
                    progress(i, len(members), f"ZIP: {m.filename}")
                ext = Path(m.filename).suffix.lower()
                if ext not in formats.SUPPORTED:
                    warnings.append(f"{m.filename}: formato no compatible, se omite.")
                    continue
                if m.file_size > MAX_ZIP_MEMBER:
                    warnings.append(f"{m.filename}: demasiado grande, se omite.")
                    continue
                if ext == ".zip" and depth >= MAX_ZIP_DEPTH:
                    warnings.append(f"{m.filename}: ZIP dentro de ZIP demasiado profundo, se omite.")
                    continue
                try:
                    if ext == ".zip":
                        sub = self._zip(z.read(m), m.filename, None, cancel, depth + 1)
                    else:
                        sub = self._dispatch(z.read(m), ext, m.filename, None, cancel)
                except Exception as exc:
                    if type(exc).__name__ == "ConversionCancelled":
                        raise
                    warnings.append(f"{m.filename}: no se pudo convertir ({exc}).")
                    continue
                # los títulos del archivo quedan por debajo del título «## nombre»
                body = re.sub(r"^(#{1,6}) ", lambda m: "#" * min(len(m.group(1)) + 2, 6) + " ", sub.text_content, flags=re.MULTILINE)
                parts.append(f"## {m.filename}\n\n{body.strip()}")
                warnings.extend(f"{m.filename}: {w}" for w in sub.warnings)
        if len(parts) == 1:
            warnings.append("El ZIP no contiene archivos compatibles.")
        return ConversionResult("\n\n".join(parts), warnings=warnings)

    def _dispatch(self, data: bytes, ext: str, name: str, progress, cancel) -> ConversionResult:
        if ext == ".pdf":
            return self._pdf_bytes(data, progress, cancel)
        if ext in formats.IMAGE_EXTS:
            return self._image(data, progress, cancel)
        if ext == ".zip":
            return self._zip(data, name, progress, cancel)
        table = {
            ".docx": formats.convert_docx,
            ".pptx": formats.convert_pptx,
            ".xlsx": formats.convert_xlsx, ".xlsm": formats.convert_xlsx,
            ".csv": formats.convert_csv, ".tsv": formats.convert_csv,
            ".html": formats.convert_html, ".htm": formats.convert_html, ".xhtml": formats.convert_html,
            ".epub": formats.convert_epub,
            ".json": formats.convert_json, ".jsonl": formats.convert_json,
            ".xml": formats.convert_xml,
        }
        fn = table.get(ext)
        if fn is None and ext in formats.TEXT_EXTS:
            fn = formats.convert_text
        if fn is None:
            raise UnsupportedFormat(f"Formato no compatible: «{ext}».")
        if progress:
            progress(0, 1, f"Convirtiendo {Path(name).name}…")
        try:
            md, warnings = fn(data, extension=ext, furigana=self.furigana)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ValueError(f"El archivo está dañado o no es un «{ext}» válido ({exc}).") from exc
        if progress:
            progress(1, 1, "Hecho")
        return ConversionResult(md, warnings=warnings)


def iter_files(folder: str | os.PathLike, recursive: bool = True) -> list[Path]:
    """Archivos compatibles de una carpeta, ordenados."""
    root = Path(folder)
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in formats.SUPPORTED and not p.name.startswith("."))
