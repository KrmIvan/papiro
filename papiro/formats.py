"""Conversores de los formatos que no son PDF.

Cada función recibe los bytes del archivo y devuelve Markdown (y avisos).
"""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from .pdf.convert import render_table
from .textutil import clean_markdown, normalize_text

Result = tuple[str, list[str]]


# ---------------------------------------------------------------------------
# Texto y codificaciones
# ---------------------------------------------------------------------------


def decode_text(data: bytes) -> str:
    """Decodifica texto probando UTF-8, UTF-16 y las codificaciones japonesas y europeas."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    for enc in ("cp932", "euc_jp", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", "replace")


def convert_text(data: bytes, **_) -> Result:
    return clean_markdown(normalize_text(decode_text(data))), []


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def html_to_markdown(html: str, furigana: bool = True) -> str:
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe", "form", "nav", "footer", "aside"]):
        tag.decompose()
    # Furigana: <ruby>漢字<rt>かんじ</rt></ruby> -> 漢字(かんじ)
    for ruby in soup.find_all("ruby"):
        for rp in ruby.find_all("rp"):
            rp.decompose()
        parts: list[str] = []
        base = ""
        for child in list(ruby.children):
            name = getattr(child, "name", None)
            if name == "rt":
                reading = child.get_text(strip=True)
                if base:
                    parts.append(base + (f"({reading})" if furigana and reading else ""))
                    base = ""
            elif name == "rtc":
                continue
            else:
                base += child.get_text() if name else str(child)
        if base:
            parts.append(base)
        ruby.replace_with("".join(parts))
    # Tablas sin fila de cabecera (p. ej. las de Word): la primera fila hace de cabecera
    for table in soup.find_all("table"):
        if table.find("th") is None:
            first = table.find("tr")
            if first is not None:
                for td in first.find_all("td", recursive=False):
                    td.name = "th"
    body = soup.body or soup
    main = body.find("main") or body.find("article") or body
    md = markdownify(str(main), heading_style="ATX", bullets="-")
    md = re.sub(r"[ \t]+\n", "\n", md)
    return clean_markdown(normalize_text(md))


def convert_html(data: bytes, furigana: bool = True, **_) -> Result:
    from bs4 import BeautifulSoup

    # BeautifulSoup detecta la codificación declarada en <meta charset>
    soup = BeautifulSoup(data, "lxml")
    md = html_to_markdown(str(soup), furigana)
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title and not md.lstrip().startswith("# "):
        md = f"# {title}\n\n{md}"
    return md, []


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def convert_docx(data: bytes, furigana: bool = True, **_) -> Result:
    import mammoth

    result = mammoth.convert_to_html(io.BytesIO(data))
    warnings = []
    md = html_to_markdown(result.value, furigana)
    if not md.strip():
        warnings.append("El documento Word no contiene texto.")
    return md, warnings


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------


def _pptx_shape_text(shape, out: list[str], title_shape) -> None:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for sub in sorted(shape.shapes, key=lambda s: (s.top or 0, s.left or 0)):
            _pptx_shape_text(sub, out, title_shape)
        return
    if getattr(shape, "has_table", False) and shape.has_table:
        rows = [[" ".join(c.text.split()) for c in row.cells] for row in shape.table.rows]
        if rows:
            out.append(render_table(rows))
        return
    is_title = title_shape is not None and shape.shape_id == title_shape.shape_id
    if is_title or not getattr(shape, "has_text_frame", False) or not shape.has_text_frame:
        return
    lines = []
    for para in shape.text_frame.paragraphs:
        text = "".join(run.text for run in para.runs).strip()
        if not text:
            continue
        lines.append("  " * (para.level or 0) + "- " + text if len(shape.text_frame.paragraphs) > 1 else text)
    if lines:
        out.append("\n".join(lines))


def convert_pptx(data: bytes, **_) -> Result:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        parts.append(f"## Diapositiva {i}" + (f": {' '.join(title.split())}" if title else ""))
        body: list[str] = []
        for shape in sorted(slide.shapes, key=lambda s: (s.top or 0, s.left or 0)):
            _pptx_shape_text(shape, body, title_shape)
        parts.extend(body)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            text = notes.text.strip() if notes is not None else ""
            if text:
                parts.append("**Notas del orador:**\n\n" + text)
    return clean_markdown(normalize_text("\n\n".join(parts))), []


# ---------------------------------------------------------------------------
# XLSX y CSV
# ---------------------------------------------------------------------------


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _trim_table(rows: list[list[str]]) -> list[list[str]]:
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any(r[j] for r in rows)]
    return [[r[j] for j in keep] for r in rows]


def convert_xlsx(data: bytes, **_) -> Result:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    warnings: list[str] = []
    for ws in wb.worksheets:
        rows = _trim_table([[_cell_str(v) for v in row] for row in ws.iter_rows(values_only=True)])
        parts.append(f"## {ws.title}")
        if rows:
            parts.append(render_table(rows))
        else:
            parts.append("*(hoja vacía)*")
    wb.close()
    return clean_markdown(normalize_text("\n\n".join(parts))), warnings


def convert_csv(data: bytes, extension: str = ".csv", **_) -> Result:
    text = decode_text(data)
    sample = text[:8192]
    delimiter = "\t" if extension == ".tsv" else ","
    if extension != ".tsv":
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = _trim_table([[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)])
    if not rows:
        return "", ["El archivo CSV está vacío."]
    return clean_markdown(normalize_text(render_table(rows))), []


# ---------------------------------------------------------------------------
# JSON y XML
# ---------------------------------------------------------------------------


def convert_json(data: bytes, extension: str = ".json", **_) -> Result:
    text = decode_text(data)
    warnings: list[str] = []
    if extension == ".jsonl":
        body = text.strip()
    else:
        try:
            body = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError as exc:
            warnings.append(f"El JSON no es válido ({exc.msg}, línea {exc.lineno}). Se copia tal cual.")
            body = text.strip()
    return f"```json\n{body}\n```\n", warnings


def convert_xml(data: bytes, **_) -> Result:
    text = decode_text(data).strip()
    warnings: list[str] = []
    try:
        root = ET.fromstring(data)
        ET.indent(root)
        body = ET.tostring(root, encoding="unicode")
    except ET.ParseError as exc:
        warnings.append(f"El XML no es válido ({exc}). Se copia tal cual.")
        body = text
    return f"```xml\n{body}\n```\n", warnings


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def convert_epub(data: bytes, furigana: bool = True, **_) -> Result:
    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = next(e.attrib["full-path"] for e in container.iter() if _local(e.tag) == "rootfile")
        except (KeyError, StopIteration, ET.ParseError) as exc:
            raise ValueError("El EPUB no es válido: falta META-INF/container.xml.") from exc
        opf = ET.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        title = next((e.text for e in opf.iter() if _local(e.tag) == "title" and e.text), "")
        author = next((e.text for e in opf.iter() if _local(e.tag) == "creator" and e.text), "")
        manifest = {
            e.attrib.get("id"): e.attrib.get("href", "")
            for e in opf.iter() if _local(e.tag) == "item"
        }
        spine = [e.attrib.get("idref") for e in opf.iter() if _local(e.tag) == "itemref"]
        parts: list[str] = []
        if title:
            parts.append(f"# {title.strip()}")
        if author:
            parts.append(f"*{author.strip()}*")
        names = set(z.namelist())
        for idref in spine:
            href = manifest.get(idref)
            if not href:
                continue
            from urllib.parse import unquote

            path = posixpath.normpath(posixpath.join(base, unquote(href.split("#")[0])))
            if path not in names:
                warnings.append(f"Falta el capítulo «{href}» dentro del EPUB.")
                continue
            md = html_to_markdown(z.read(path).decode("utf-8", "replace"), furigana)
            if md.strip():
                parts.append(md.strip())
    return clean_markdown("\n\n".join(parts)), warnings


# ---------------------------------------------------------------------------
# Detección del tipo de archivo
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".log"}

SUPPORTED = (
    {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".tsv", ".html", ".htm", ".xhtml", ".epub",
     ".json", ".jsonl", ".xml", ".zip"}
    | IMAGE_EXTS | TEXT_EXTS
)


def sniff_extension(data: bytes) -> str:
    """Adivina el formato por el contenido (para stdin y URLs)."""
    head = data[:2048]
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
            if "word/document.xml" in names:
                return ".docx"
            if "ppt/presentation.xml" in names:
                return ".pptx"
            if "xl/workbook.xml" in names:
                return ".xlsx"
            if "META-INF/container.xml" in names:
                return ".epub"
        except zipfile.BadZipFile:
            pass
        return ".zip"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return ".tiff"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head.startswith(b"BM"):
        return ".bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<!doctype html", b"<html")) or b"<body" in text or b"<head" in text:
        return ".html"
    if text.startswith(b"<?xml") or text.startswith(b"<"):
        return ".xml"
    if text.startswith((b"{", b"[")):
        return ".json"
    return ".txt"


CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/json": ".json",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/epub+zip": ".epub",
    "application/zip": ".zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
