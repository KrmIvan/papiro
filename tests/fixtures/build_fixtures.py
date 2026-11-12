"""Genera los PDFs de prueba de Papiro.

Los PDFs ya están guardados en el repositorio; este script solo hace falta
para regenerarlos. Necesita Google Chrome o Chromium (para imprimir HTML a
PDF) y las fuentes Noto CJK.

    python tests/fixtures/build_fixtures.py

Todo el contenido es original, escrito para estas pruebas.
"""

from __future__ import annotations

import io
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
PDF_DIR = HERE / "pdf"

BASE_CSS = """
@page { size: A4; margin: 22mm 20mm; }
html { font-size: 11.5pt; }
body { font-family: 'Liberation Serif', 'DejaVu Serif', serif; line-height: 1.45; color: #111; }
.ja, :lang(ja) { font-family: 'Noto Serif CJK JP', 'Noto Sans CJK JP', serif; }
h1 { font-size: 20pt; margin: 0 0 10pt; }
h2 { font-size: 14pt; margin: 16pt 0 6pt; }
p { margin: 0 0 8pt; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt; }
th, td { border: 0.6pt solid #444; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #e8e8e8; }
rt { font-size: 50%; }
"""



def running(header: str, footer: str) -> str:
    """Cabecera y pie repetidos en todas las páginas (cajas de margen de CSS)."""
    return f"""
    @page {{
      @top-center {{ content: "{header}"; font-size: 8.5pt; color: #555; }}
      @bottom-center {{ content: {footer}; font-size: 8.5pt; color: #555; }}
    }}"""


def r(base: str, reading: str) -> str:
    return f"<ruby>{base}<rt>{reading}</rt></ruby>"


# --------------------------------------------------------------------------
# 1. Texto simple en español (3 páginas, cabecera y pie repetidos, guiones)
# --------------------------------------------------------------------------
SIMPLE_PARRAFOS = [
    "La conversión de documentos a texto plano parece una tarea sencilla, pero "
    "es&shy;con&shy;de muchas dificultades. Un archivo PDF no guarda párrafos ni títulos: "
    "guarda letras colocadas en coordenadas de la página. Reconstruir el orden de "
    "lec&shy;tu&shy;ra exige entender la maqueta completa del documento.",
    "Cuando el texto está justificado, las palabras largas se parten al final de "
    "la línea con un guion. Un buen conversor debe volver a unirlas para que la "
    "pa&shy;la&shy;bra original quede intacta, sin gui&shy;o&shy;nes sobrantes ni espacios "
    "in&shy;ne&shy;ce&shy;sa&shy;rios en medio.",
    "Las palabras compuestas, como físico-químico o teórico-práctico, llevan un "
    "guion que sí forma parte de la palabra y que no debe desaparecer al "
    "con&shy;ver&shy;tir el documento.",
    "Este manual explica, paso a paso, cómo se comprueba la calidad de una "
    "con&shy;ver&shy;sión: primero se revisa el orden de lectura, después los títulos, "
    "luego las tablas y por último los caracteres especiales, como la eñe, las "
    "vocales acentuadas (á, é, í, ó, ú), la diéresis de «pingüino» y los signos de "
    "apertura ¿ y ¡.",
]


def html_simple() -> str:
    cuerpo = ["<h1>Manual de conversión de documentos</h1>"]
    secciones = [
        "1. Por qué es difícil convertir un PDF",
        "2. El problema de los guiones",
        "3. Cómo se mide la calidad",
        "4. Resumen final",
    ]
    for i, titulo in enumerate(secciones):
        cuerpo.append(f"<h2>{titulo}</h2>")
        for j in range(8):
            cuerpo.append(f"<p>{SIMPLE_PARRAFOS[(i + j) % len(SIMPLE_PARRAFOS)]}</p>")
        if i == 1:
            cuerpo.append(
                "<p><b>Nota importante:</b> esta línea empieza en negrita, pero "
                "no es un título y no debe convertirse en uno.</p>"
            )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><style>{BASE_CSS}{running("Manual de pruebas de Papiro — Edición de ejemplo", '"Documento de prueba · Página " counter(page)')}
    p {{ hyphens: manual; }}</style>
    {''.join(cuerpo)}</html>"""


# --------------------------------------------------------------------------
# 2. Dos columnas
# --------------------------------------------------------------------------
def html_dos_columnas() -> str:
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><style>{BASE_CSS}
    .cols {{ column-count: 2; column-gap: 9mm; }}
    h2 {{ break-after: avoid; }}</style>
    <h1>Boletín del club de lectura</h1>
    <div class="cols">
    <h2>Novedades de otoño</h2>
    <p>ALFA Este trimestre la biblioteca del club recibe cuarenta títulos nuevos.
    La mayoría son novelas contemporáneas, aunque también llegan ensayos de
    divulgación científica y dos colecciones de poesía.</p>
    <p>BETA Los socios pueden reservar hasta tres libros a la vez. Las reservas
    se guardan durante siete días en el mostrador de la entrada.</p>
    <p><b>Horario ampliado.</b> BRAVO Desde octubre, la sala abre también los
    sábados por la mañana, de diez a dos.</p>
    <h2>Encuentro con autores</h2>
    <p>GAMMA El primer jueves de cada mes recibimos a un escritor invitado.
    La charla dura una hora y después hay tiempo para preguntas y firmas.</p>
    <p>DELTA Para asistir no hace falta inscribirse, pero el aforo es limitado
    a sesenta personas y se respeta el orden de llegada.</p>
    <h2>Taller de escritura</h2>
    <p>ÉPSILON El taller de los martes está pensado para quienes empiezan.
    Cada sesión se trabaja un ejercicio breve y se comenta en grupo.</p>
    <p>ZETA Quienes ya tienen experiencia pueden apuntarse al grupo avanzado,
    que se reúne los miércoles y prepara una antología anual.</p>
    <h2>Cuotas y contacto</h2>
    <p>ETA La cuota anual sigue siendo de veinte euros. Los estudiantes y los
    mayores de sesenta y cinco años pagan la mitad.</p>
    <p>ZETA-FIN Para cualquier duda, escribe al correo del club o pregunta en
    el mostrador. ¡Te esperamos!</p>
    </div></html>"""


# --------------------------------------------------------------------------
# 3. Tabla
# --------------------------------------------------------------------------
def html_tabla() -> str:
    filas = [
        ("Enero", "1.250", "980", "270"),
        ("Febrero", "1.310", "1.045", "265"),
        ("Marzo", "1.480", "1.120", "360"),
        ("Abril", "1.395", "1.210", "185"),
    ]
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in f) + "</tr>" for f in filas)
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><style>{BASE_CSS}</style>
    <h1>Informe trimestral</h1>
    <p>La tabla siguiente resume los ingresos y los gastos de la asociación
    durante los cuatro primeros meses del año. Todas las cifras están en euros.</p>
    <table><tr><th>Mes</th><th>Ingresos</th><th>Gastos</th><th>Saldo</th></tr>{trs}</table>
    <p>El saldo fue positivo todos los meses. El mejor resultado se obtuvo en
    marzo, gracias a la feria del libro.</p></html>"""


# --------------------------------------------------------------------------
# 4. Página para escanear (español)
# --------------------------------------------------------------------------
def html_escaneado() -> str:
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><style>{BASE_CSS}
    html {{ font-size: 12.5pt; }}</style>
    <h1>Acta de la reunión de vecinos</h1>
    <p>La reunión comenzó a las siete de la tarde en el salón de la comunidad.
    Asistieron veintitrés propietarios, que representan más de la mitad de las
    viviendas del edificio.</p>
    <p>En primer lugar se aprobó el presupuesto del año próximo. A continuación
    se decidió pintar la fachada en primavera y cambiar la iluminación del
    portal por lámparas de bajo consumo.</p>
    <p>Por último, la señora Núñez propuso instalar un aparcamiento para
    bicicletas en el patio. La propuesta se aprobó por unanimidad.</p>
    <p>Sin más asuntos que tratar, se levantó la sesión a las ocho y cuarto.</p>
    </html>"""


# --------------------------------------------------------------------------
# 5. Certificado con sellos, marca de agua y texto girado
# --------------------------------------------------------------------------
def html_certificado() -> str:
    return """<!doctype html><html lang="es"><meta charset="utf-8"><style>
    @page { size: A4 landscape; margin: 0; }
    body { margin: 0; font-family: 'Liberation Serif', serif; color: #1a1a1a; }
    .hoja { position: relative; width: 297mm; height: 209mm; box-sizing: border-box;
            padding: 22mm 30mm; overflow: hidden; }
    .marco { position: absolute; inset: 9mm; border: 2.2pt double #7a5c12; }
    .centro { text-align: center; position: relative; }
    .org { font-size: 13pt; letter-spacing: 2pt; text-transform: uppercase; margin-top: 4mm; }
    .tit { font-size: 34pt; margin: 9mm 0 6mm; font-weight: bold; }
    .txt { font-size: 14pt; margin: 3mm 0; }
    .nombre { font-size: 26pt; font-style: italic; margin: 6mm 0; }
    .firmas { position: absolute; left: 40mm; right: 40mm; bottom: 26mm; display: flex;
              justify-content: space-between; font-size: 11pt; text-align: center; }
    .firmas div { width: 70mm; border-top: 0.8pt solid #333; padding-top: 2mm; }
    .agua { position: absolute; left: 20mm; top: 78mm; font-size: 92pt; font-weight: bold;
            color: rgba(120,120,120,0.16); transform: rotate(-28deg); letter-spacing: 8pt; }
    .sello { position: absolute; right: 34mm; bottom: 44mm; width: 36mm; height: 36mm;
             border: 2.4pt solid #b3261e; border-radius: 50%; color: #b3261e;
             transform: rotate(-18deg); display: flex; flex-direction: column;
             align-items: center; justify-content: center; font-family: 'Liberation Sans', sans-serif;
             font-weight: bold; font-size: 10pt; line-height: 1.25; text-align: center; }
    .lateral { position: absolute; left: 13mm; top: 150mm; transform-origin: left top;
               transform: rotate(-90deg); font-size: 8pt; color: #666;
               font-family: 'Liberation Sans', sans-serif; white-space: nowrap; }
    </style><div class="hoja"><div class="marco"></div>
    <div class="agua">MUESTRA</div>
    <div class="lateral">Registro n.º 2024/00417 · Libro 12 · Folio 88 · Código de verificación QX7-PL92</div>
    <div class="centro">
      <div class="org">Academia de Idiomas del Mediterráneo</div>
      <div class="tit">Certificado de aprovechamiento</div>
      <div class="txt">Se certifica que</div>
      <div class="nombre">Lucía Fernández Ortega</div>
      <div class="txt">ha completado con éxito el curso «Japonés inicial I», de 60 horas de duración,</div>
      <div class="txt">celebrado entre el 15 de enero y el 28 de marzo de 2024, con la calificación de sobresaliente.</div>
      <div class="txt">Valencia, a 5 de abril de 2024</div>
    </div>
    <div class="sello">ACADEMIA<br>SELLADO<br>05 ABR 2024</div>
    <div class="firmas"><div>Marta Soler Vidal<br>Directora académica</div>
    <div>Kenji Watanabe<br>Profesor del curso</div></div>
    </div></html>"""


# --------------------------------------------------------------------------
# 6. Página bilingüe japonés-español, horizontal, con furigana
# --------------------------------------------------------------------------
def html_bilingue() -> str:
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><style>{BASE_CSS}{running("Japonés paso a paso · Lección 3", '"— " counter(page, decimal) " —"')}
    .ja {{ font-size: 13pt; line-height: 2.0; }}
    .dial p {{ margin: 0 0 2pt; text-align: left; }}
    .es {{ color: #222; margin-bottom: 9pt !important; }}
    .par {{ display: flex; gap: 8mm; margin-bottom: 4pt; }}
    .par div {{ flex: 1; }}
    ol {{ margin: 0 0 8pt 0; }} li {{ margin-bottom: 3pt; }}
    </style>
    <h1><span class="ja">第3課　{r('週末', 'しゅうまつ')}の{r('予定', 'よてい')}</span><br>Lección 3: Planes para el fin de semana</h1>
    <h2><span class="ja">{r('会話', 'かいわ')}</span> · Diálogo</h2>
    <div class="dial">
    <p class="ja">たけし：{r('週末', 'しゅうまつ')}は{r('何', 'なに')}をしますか。</p>
    <p class="es">Takeshi: ¿Qué vas a hacer el fin de semana?</p>
    <p class="ja">マリア：{r('土曜日', 'どようび')}に{r('友達', 'ともだち')}と{r('映画', 'えいが')}を{r('見', 'み')}ます。</p>
    <p class="es">María: El sábado voy a ver una película con una amiga.</p>
    <p class="ja">たけし：いいですね。{r('日曜日', 'にちようび')}は？</p>
    <p class="es">Takeshi: ¡Qué bien! ¿Y el domingo?</p>
    <p class="ja">マリア：{r('図書館', 'としょかん')}で{r('日本語', 'にほんご')}を{r('勉強', 'べんきょう')}します。{r('来週', 'らいしゅう')}、テストがありますから。</p>
    <p class="es">María: Estudiaré japonés en la biblioteca, porque la semana que viene hay un examen.</p>
    </div>
    <h2><span class="ja">{r('文法', 'ぶんぽう')}</span> · Gramática</h2>
    <p>La partícula <span class="ja">を</span> marca el objeto directo del verbo, y
    <span class="ja">で</span> indica el lugar donde ocurre la acción. El verbo va
    siempre al final de la frase: <span class="ja">パンを{r('食', 'た')}べます</span>
    significa «como pan». Para preguntar se añade <span class="ja">か</span> al
    final, sin cambiar el orden de las palabras.</p>
    <h2><span class="ja">{r('例文', 'れいぶん')}</span> · Ejemplos</h2>
    <div class="par"><div class="ja">{r('毎朝', 'まいあさ')}コーヒーを{r('飲', 'の')}みます。</div><div>Bebo café todas las mañanas.</div></div>
    <div class="par"><div class="ja">{r('学校', 'がっこう')}で{r('昼', 'ひる')}ごはんを{r('食', 'た')}べます。</div><div>Almuerzo en la escuela.</div></div>
    <div class="par"><div class="ja">{r('明日', 'あした')}、{r('東京', 'とうきょう')}に{r('行', 'い')}きますか。</div><div>¿Vas a Tokio mañana?</div></div>
    <div class="par"><div class="ja">いいえ、{r('行', 'い')}きません。{r('家', 'うち')}で{r('本', 'ほん')}を{r('読', 'よ')}みます。</div><div>No, no voy. Leeré un libro en casa.</div></div>
    </html>"""


# --------------------------------------------------------------------------
# 7. Página japonesa vertical (tategaki) con furigana
# --------------------------------------------------------------------------
def html_tategaki() -> str:
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><style>
    @page {{ size: A5; margin: 16mm 14mm; }}
    html {{ writing-mode: vertical-rl; font-size: 12.5pt; }}
    body {{ font-family: 'Noto Serif CJK JP', serif; line-height: 2.1; margin: 0; color: #111; }}
    h1 {{ font-size: 18pt; margin: 0 0 0 10pt; font-weight: bold; }}
    p {{ margin: 0; text-indent: 1em; text-align: justify; }}
    rt {{ font-size: 50%; }}
    .num {{ position: fixed; bottom: -9mm; left: 50%; writing-mode: horizontal-tb; font-size: 8pt; }}
    </style>
    <div class="num">17</div>
    <h1>{r('山', 'やま')}の{r('小', 'ちい')}さな{r('駅', 'えき')}</h1>
    <p>{r('朝', 'あさ')}の{r('電車', 'でんしゃ')}は、{r('霧', 'きり')}の{r('中', 'なか')}をゆっくり{r('走', 'はし')}っていた。{r('窓', 'まど')}の{r('外', 'そと')}には{r('田', 'た')}んぼが{r('広', 'ひろ')}がり、{r('遠', 'とお')}くに{r('青', 'あお')}い{r('山', 'やま')}が{r('見', 'み')}えた。</p>
    <p>「{r('次', 'つぎ')}は{r('終点', 'しゅうてん')}です」と、{r('車掌', 'しゃしょう')}さんが{r('言', 'い')}った。{r('私', 'わたし')}は{r('荷物', 'にもつ')}を{r('持', 'も')}って、ドアの{r('前', 'まえ')}に{r('立', 'た')}った。</p>
    <p>{r('駅', 'えき')}には{r('誰', 'だれ')}もいなかった。ベンチの{r('上', 'うえ')}で、{r('白', 'しろ')}い{r('猫', 'ねこ')}が{r('一匹', 'いっぴき')}、{r('眠', 'ねむ')}っていた。</p>
    <p>{r('祖母', 'そぼ')}の{r('家', 'いえ')}までは、ここから{r('歩', 'ある')}いて{r('二十分', 'にじゅっぷん')}ほどだ。{r('子供', 'こども')}のころ、{r('夏休', 'なつやす')}みになると{r('毎年', 'まいとし')}この{r('道', 'みち')}を{r('歩', 'ある')}いた。</p>
    </html>"""


# --------------------------------------------------------------------------
# 8. Tabla de vocabulario japonés-español
# --------------------------------------------------------------------------
VOCAB = [
    ("しゅうまつ", "週末", "fin de semana"),
    ("どようび", "土曜日", "sábado"),
    ("にちようび", "日曜日", "domingo"),
    ("ともだち", "友達", "amigo, amiga"),
    ("えいが", "映画", "película"),
    ("としょかん", "図書館", "biblioteca"),
    ("べんきょうする", "勉強する", "estudiar"),
    ("みる", "見る", "ver, mirar"),
    ("たべる", "食べる", "comer"),
    ("のむ", "飲む", "beber"),
    ("コーヒー", "—", "café"),
    ("テスト", "—", "examen, prueba"),
]


def html_vocabulario() -> str:
    trs = "".join(
        f'<tr><td class="ja">{k}</td><td class="ja">{j}</td><td>{e}</td></tr>' for k, j, e in VOCAB
    )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><style>{BASE_CSS}
    td.ja {{ font-size: 12.5pt; }}</style>
    <h1><span class="ja">単語</span> · Vocabulario de la lección 3</h1>
    <p>Estas son las palabras nuevas del diálogo. Apréndelas antes de pasar a
    los ejercicios.</p>
    <table><tr><th>Lectura</th><th>Kanji</th><th>Español</th></tr>{trs}</table>
    <p>Las palabras escritas en katakana, como <span class="ja">コーヒー</span>,
    vienen de otros idiomas y no tienen kanji.</p></html>"""


# --------------------------------------------------------------------------
# 9. Página japonesa para escanear
# --------------------------------------------------------------------------
def html_ja_escaneado() -> str:
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><style>{BASE_CSS}
    html {{ font-size: 13pt; }}
    .ja {{ font-size: 14.5pt; line-height: 2.0; }}
    p {{ text-align: left; }}</style>
    <h1><span class="ja">わたしの一日</span></h1>
    <p class="ja">わたしは毎朝六時に起きます。朝ごはんを食べて、七時半に家を出ます。</p>
    <p class="ja">会社は東京にあります。電車で四十分ぐらいかかります。</p>
    <p class="ja">昼休みに同僚とラーメンを食べました。とてもおいしかったです。</p>
    <p class="ja">夜は家で日本語の本を読みます。十一時ごろ寝ます。</p>
    <h2>Traducción</h2>
    <p>Me levanto todos los días a las seis. Desayuno y salgo de casa a las siete y media.</p>
    <p>Mi empresa está en Tokio. Se tarda unos cuarenta minutos en tren.</p>
    </html>"""


FIXTURES = {
    "01_simple_es": html_simple,
    "02_dos_columnas": html_dos_columnas,
    "03_tabla": html_tabla,
    "04_escaneado_src": html_escaneado,
    "05_certificado": html_certificado,
    "06_bilingue_horizontal": html_bilingue,
    "07_tategaki_furigana": html_tategaki,
    "08_vocabulario": html_vocabulario,
    "09_ja_escaneado_src": html_ja_escaneado,
}


def find_chrome() -> str:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    sys.exit("Hace falta Google Chrome o Chromium para regenerar los PDFs de prueba.")


def print_pdf(chrome: str, html: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "p.html"
        src.write_text(html, encoding="utf-8")
        subprocess.run(
            [chrome, "--disable-features=PrintCompositorLPAC", "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             f"--user-data-dir={tmp}/perfil", f"--print-to-pdf={out}", src.as_uri()],
            check=True, capture_output=True,
        )


def scan(src: Path, out: Path, dpi: int = 200, angle: float = 0.6, seed: int = 7) -> None:
    """Convierte un PDF en un "escaneado": imagen con ruido, algo torcida, sin texto."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter

    rng = random.Random(seed)
    pages = []
    doc = pdfium.PdfDocument(str(src))
    for page in doc:
        img = page.render(scale=dpi / 72).to_pil().convert("L")
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        px = img.load()
        w, h = img.size
        for _ in range(w * h // 400):  # motas de polvo
            x, y = rng.randrange(w), rng.randrange(h)
            px[x, y] = rng.randrange(60, 200)
        img = img.point(lambda v: min(255, int(v * 0.93 + 14)))  # papel grisáceo
        pages.append(img.convert("RGB"))
    doc.close()
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", resolution=dpi, save_all=True, append_images=pages[1:], quality=80)
    out.write_bytes(buf.getvalue())


def strip_tounicode(src: Path, out: Path) -> None:
    """Quita los mapas ToUnicode: el texto se ve bien pero se extrae como basura."""
    import pikepdf

    with pikepdf.open(src) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") == "/Font":
                if "/ToUnicode" in obj:
                    del obj["/ToUnicode"]
        pdf.save(out)


def main() -> None:
    chrome = find_chrome()
    PDF_DIR.mkdir(exist_ok=True)
    for name, fn in FIXTURES.items():
        print_pdf(chrome, fn(), PDF_DIR / f"{name}.pdf")
        print("ok", name)
    scan(PDF_DIR / "04_escaneado_src.pdf", PDF_DIR / "04_escaneado.pdf")
    scan(PDF_DIR / "09_ja_escaneado_src.pdf", PDF_DIR / "09_ja_escaneado.pdf", angle=-0.4)
    (PDF_DIR / "04_escaneado_src.pdf").unlink()
    (PDF_DIR / "09_ja_escaneado_src.pdf").unlink()
    strip_tounicode(PDF_DIR / "06_bilingue_horizontal.pdf", PDF_DIR / "10_fuente_rota.pdf")
    print("ok escaneados y fuente rota")


if __name__ == "__main__":
    main()
