"""Un test por formato. Los archivos de prueba se crean al vuelo."""

import io
import json
import zipfile

import pytest

from papiro import Converter, UnsupportedFormat

C = Converter(mode="rapido")


def md(data: bytes, ext: str) -> str:
    return C.convert(data, extension=ext).text_content


def make_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading("Informe de prueba", 1)
    doc.add_paragraph("Primer párrafo con eñe y 日本語.")
    doc.add_heading("Sección", 2)
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Palabra", "Significado"
    t.cell(1, 0).text, t.cell(1, 1).text = "猫", "gato"
    doc.add_paragraph("Elemento uno", style="List Bullet")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_pptx() -> bytes:
    from pptx import Presentation

    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = "Título de la diapositiva"
    s.placeholders[1].text = "Primer punto\nSegundo punto"
    s.notes_slide.notes_text_frame.text = "Recordar saludar: こんにちは"
    s2 = p.slides.add_slide(p.slide_layouts[5])
    s2.shapes.title.text = "Segunda"
    buf = io.BytesIO()
    p.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Ventas"
    ws.append(["Mes", "Total"])
    ws.append(["Enero", 1250])
    ws.append(["Febrero", 1310.5])
    wb.create_sheet("Vacía")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HTML = """<html><head><title>Página</title><style>p{color:red}</style></head><body><nav>menú</nav>
<h1>Hola</h1><p>Texto con <ruby>漢字<rp>(</rp><rt>かんじ</rt><rp>)</rp></ruby> y <b>negrita</b>.</p>
<ul><li>uno</li><li>dos</li></ul><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>
<script>alert(1)</script></body></html>""".encode("utf-8")


def make_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Libro de prueba</dc:title><dc:creator>Autora</dc:creator></metadata><manifest><item id="c2" href="cap2.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="cap1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/cap1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Capítulo 1</h1><p>Érase una vez <ruby>猫<rt>ねこ</rt></ruby>.</p></body></html>')
        z.writestr("OEBPS/cap2.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Capítulo 2</h1><p>Fin.</p></body></html>')
    return buf.getvalue()


def test_docx():
    out = md(make_docx(), ".docx")
    assert out.startswith("# Informe de prueba")
    assert "Primer párrafo con eñe y 日本語." in out and "## Sección" in out
    assert "| Palabra | Significado |" in out and "| 猫 | gato |" in out
    assert "- Elemento uno" in out


def test_pptx_con_notas_del_orador():
    out = md(make_pptx(), ".pptx")
    assert "## Diapositiva 1: Título de la diapositiva" in out and "## Diapositiva 2: Segunda" in out
    assert "- Primer punto\n- Segundo punto" in out
    assert "**Notas del orador:**" in out and "Recordar saludar: こんにちは" in out
    assert out.count("Título de la diapositiva") == 1


def test_xlsx():
    out = md(make_xlsx(), ".xlsx")
    assert "## Ventas" in out and "| Mes | Total |" in out
    assert "| Enero | 1250 |" in out and "| Febrero | 1310.5 |" in out
    assert "## Vacía" in out and "hoja vacía" in out


def test_csv_con_punto_y_coma_y_japones():
    out = md("palabra;lectura;significado\n猫;ねこ;gato\n犬;いぬ;perro\n".encode("utf-8"), ".csv")
    assert "| palabra | lectura | significado |" in out and "| 犬 | いぬ | perro |" in out


def test_csv_en_shift_jis():
    out = md("単語,意味\n猫,gato\n図書館,biblioteca\n".encode("cp932"), ".csv")
    assert "| 図書館 | biblioteca |" in out


def test_html_con_ruby():
    out = md(HTML, ".html")
    assert out.startswith("# Hola")
    assert "Texto con 漢字(かんじ) y **negrita**." in out
    assert "- uno\n- dos" in out and "| A | B |" in out
    assert "alert" not in out and "color:red" not in out and "menú" not in out
    assert "漢字 y" in Converter(furigana=False).convert(HTML, extension=".html").text_content


def test_epub_en_orden_del_libro():
    out = md(make_epub(), ".epub")
    assert out.startswith("# Libro de prueba")
    assert out.index("Capítulo 1") < out.index("Capítulo 2")
    assert "Érase una vez 猫(ねこ)." in out


def test_json_y_xml():
    out = md('{"nombre":"猫","n":[1,2]}'.encode(), ".json")
    assert out.startswith("```json") and '"nombre": "猫"' in out
    assert json.loads(out.strip().removeprefix("```json").removesuffix("```")) == {"nombre": "猫", "n": [1, 2]}
    out = md(b'<?xml version="1.0"?><raiz><a x="1">hola</a></raiz>', ".xml")
    assert out.startswith("```xml") and '<a x="1">hola</a>' in out
    roto = C.convert(b'{"a": ', extension=".json")
    assert roto.warnings and '{"a":' in roto.text_content


def test_texto_plano():
    assert md("Hola\r\n\r\n\r\n\r\nmundo ｶﾞ".encode("utf-8"), ".txt") == "Hola\n\nmundo ガ\n"


def test_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc/a.docx", make_docx())
        z.writestr("datos.csv", "a,b\n1,2\n")
        z.writestr("raro.bin", b"\x00\x01")
    res = C.convert(buf.getvalue(), extension=".zip")
    out = res.text_content
    assert "## datos.csv" in out and "| a | b |" in out
    assert "## doc/a.docx" in out and "### Informe de prueba" in out
    assert any("raro.bin" in w for w in res.warnings)


def test_deteccion_por_contenido(pdf):
    assert "| Mes | Ingresos | Gastos | Saldo |" in C.convert(open(pdf("03_tabla.pdf"), "rb")).text_content
    assert "## Ventas" in C.convert(make_xlsx()).text_content
    assert C.convert(HTML).text_content.startswith("# Hola")


def test_formato_no_compatible(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x00\x01\x02\x03" * 10)
    with pytest.raises(UnsupportedFormat):
        C.convert(str(f))
    with pytest.raises(FileNotFoundError):
        C.convert(str(tmp_path / "no_existe.pdf"))


def test_resultado_y_titulo(pdf):
    res = Converter(mode="rapido").convert(pdf("03_tabla.pdf"))
    assert res.title == "Informe trimestral" and res.pages == 1 and res.mode == "rapido"
    assert res.markdown == res.text_content == str(res)


def test_marcas_de_pagina(pdf):
    out = Converter(mode="rapido", page_marks=True).convert(pdf("01_simple_es.pdf")).text_content
    assert out.startswith("<!-- página 1 -->") and "<!-- página 3 -->" in out


def test_url(tmp_path):
    import functools
    import http.server
    import threading

    (tmp_path / "p.html").write_bytes(HTML)
    (tmp_path / "t.xlsx").write_bytes(make_xlsx())
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert "漢字(かんじ)" in C.convert(f"{base}/p.html").text_content
        assert "## Ventas" in C.convert(f"{base}/t.xlsx").text_content
        with pytest.raises(ConnectionError):
            C.convert(f"{base}/no-existe.html")
    finally:
        srv.shutdown()


@pytest.mark.ocr
def test_imagen_con_ocr_japones(pdf, tmp_path):
    import pypdfium2 as pdfium

    img = pdfium.PdfDocument(pdf("09_ja_escaneado.pdf"))[0].render(scale=200 / 72).to_pil()
    for name in ("pagina.png", "pagina.jpg"):
        path = tmp_path / name
        img.convert("RGB").save(path, quality=92)
        res = C.convert(str(path))
        assert "わたしは毎朝六時に起きます。" in res.text_content, name
        assert "Mi empresa está en Tokio." in res.text_content, name
        assert any("OCR" in w for w in res.warnings)


def test_sin_ocr_avisa_y_nunca_devuelve_vacio(pdf, monkeypatch):
    from papiro.pdf import ocr

    monkeypatch.setattr(ocr, "pick_engine", lambda preferred="auto": None)
    for name in ("04_escaneado.pdf", "09_ja_escaneado.pdf", "10_fuente_rota.pdf"):
        res = Converter(mode="rapido").convert(pdf(name))
        assert res.text_content.strip(), name
        assert "Aviso" in res.text_content and "OCR" in res.text_content
        assert any("OCR" in w and "instal" in w for w in res.warnings)


def test_ocr_desactivado_avisa(pdf):
    res = Converter(mode="rapido", ocr="nunca").convert(pdf("04_escaneado.pdf"))
    assert "OCR está desactivado" in res.text_content and res.warnings
