"""Tests de calidad con los PDFs de prueba, en modo rápido y en modo calidad."""

import re

import pytest

from conftest import JA_SPACE, in_order, similarity

MODES = ["rapido", "calidad"]
pytestmark = pytest.mark.parametrize("mode", MODES)


# -- 01: texto simple en español --------------------------------------------

def test_simple_titulos(convert, mode):
    md = convert("01_simple_es.pdf", mode).text_content
    assert md.startswith("# Manual de conversión de documentos")
    for t in ("1. Por qué es difícil convertir un PDF", "2. El problema de los guiones",
              "3. Cómo se mide la calidad", "4. Resumen final"):
        assert re.search(rf"^#+ {re.escape(t)}$", md, re.M), t
    # una línea que empieza en negrita no es un título
    assert re.search(r"^Nota importante: esta línea empieza en negrita", md, re.M)
    assert len(re.findall(r"^#", md, re.M)) == 5


def test_simple_sin_cabeceras_ni_pies(convert, mode):
    md = convert("01_simple_es.pdf", mode).text_content
    assert "Manual de pruebas de Papiro" not in md
    assert "Documento de prueba" not in md


def test_simple_guiones_y_parrafos(convert, mode):
    md = convert("01_simple_es.pdf", mode).text_content
    assert "esconde muchas dificultades" in md
    assert "innecesarios" in md and "conversión" in md
    assert not re.search(r"\w-\s+\w", md), "queda una palabra partida con guion"
    assert "físico-químico" in md and "teórico-práctico" in md  # guiones de verdad
    assert "­" not in md
    assert "á, é, í, ó, ú" in md and "«pingüino»" in md and "¿ y ¡" in md
    # los párrafos partidos entre dos páginas se vuelven a unir
    assert md.count("guarda letras colocadas en coordenadas de la página. Reconstruir") == 8


# -- 02: dos columnas ---------------------------------------------------------

def test_columnas_orden(convert, mode):
    md = convert("02_dos_columnas.pdf", mode).text_content
    assert in_order(md, "Boletín del club de lectura", "Novedades de otoño", "ALFA", "BETA", "BRAVO",
                    "Encuentro con autores", "GAMMA", "DELTA", "Taller de escritura", "ÉPSILON", "ZETA",
                    "Cuotas y contacto", "ETA La cuota", "ZETA-FIN")
    for line in md.splitlines():
        marks = [m for m in ("ALFA", "GAMMA", "ÉPSILON", "ETA La cuota") if m in line]
        assert len(marks) <= 1, f"línea con texto de dos bloques: {line[:80]}"
    assert "que se reúne los miércoles y prepara una antología anual." in md


def test_columnas_titulos(convert, mode):
    md = convert("02_dos_columnas.pdf", mode).text_content
    heads = re.findall(r"^#+ (.+)$", md, re.M)
    assert heads == ["Boletín del club de lectura", "Novedades de otoño", "Encuentro con autores",
                     "Taller de escritura", "Cuotas y contacto"]
    assert "Horario ampliado. BRAVO" in md


# -- 03: tabla ----------------------------------------------------------------

def test_tabla(convert, mode):
    md = convert("03_tabla.pdf", mode).text_content
    assert "| Mes | Ingresos | Gastos | Saldo |" in md
    assert "| Marzo | 1.480 | 1.120 | 360 |" in md
    assert "| Abril | 1.395 | 1.210 | 185 |" in md
    assert md.count("1.480") == 1, "el texto de la tabla está duplicado"
    assert in_order(md, "Todas las cifras están en euros.", "| Mes |", "El saldo fue positivo")


# -- 05: certificado ----------------------------------------------------------

def test_certificado(convert, mode):
    md = convert("05_certificado.pdf", mode).text_content
    assert in_order(md, "ACADEMIA DE IDIOMAS DEL MEDITERRÁNEO", "Certificado de aprovechamiento", "Se certifica que",
                    "Lucía Fernández Ortega", "ha completado con éxito el curso «Japonés inicial I»",
                    "Marta Soler Vidal", "Kenji Watanabe")
    for junk in ("MUESTRA", "SELLADO", "Registro", "QX7", "05 ABR"):
        assert junk not in md, f"no se quitó: {junk}"
    assert "Marta Soler Vidal Kenji" not in md  # las dos firmas no se mezclan


# -- 06: bilingüe japonés-español con furigana --------------------------------

PARES = [
    ("たけし：週末(しゅうまつ)は何(なに)をしますか。", "Takeshi: ¿Qué vas a hacer el fin de semana?"),
    ("マリア：土曜日(どようび)に友達(ともだち)と映画(えいが)を見(み)ます。", "María: El sábado voy a ver una película con una amiga."),
    ("たけし：いいですね。日曜日(にちようび)は？", "Takeshi: ¡Qué bien! ¿Y el domingo?"),
    ("マリア：図書館(としょかん)で日本語(にほんご)を勉強(べんきょう)します。来週(らいしゅう)、テストがありますから。",
     "María: Estudiaré japonés en la biblioteca, porque la semana que viene hay un examen."),
    ("毎朝(まいあさ)コーヒーを飲(の)みます。", "Bebo café todas las mañanas."),
    ("学校(がっこう)で昼(ひる)ごはんを食(た)べます。", "Almuerzo en la escuela."),
    ("明日(あした)、東京(とうきょう)に行(い)きますか。", "¿Vas a Tokio mañana?"),
    ("いいえ、行(い)きません。家(うち)で本(ほん)を読(よ)みます。", "No, no voy. Leeré un libro en casa."),
]


def test_bilingue_pares_juntos_y_furigana(convert, mode):
    md = convert("06_bilingue_horizontal.pdf", mode).text_content
    flat = [p for pair in PARES for p in pair]
    for p in flat:
        assert p in md, p
    assert in_order(md, *flat), "las frases y sus traducciones no van juntas y en orden"
    assert "第3課 週末(しゅうまつ)の予定(よてい)" in md  # este espacio sí está en el original (　)
    assert not JA_SPACE.search(md.replace("第3課 週末", "")), "hay espacios entre caracteres japoneses"
    assert "La partícula を marca el objeto directo del verbo, y で indica" in md
    assert "パンを食(た)べます significa «como pan»" in md
    # el furigana nunca aparece suelto
    assert not re.search(r"^[ぁ-ゟ ]{1,12}$", md, re.M)


def test_bilingue_sin_furigana(convert, mode):
    md = convert("06_bilingue_horizontal.pdf", mode, furigana=False).text_content
    assert "たけし：週末は何をしますか。" in md
    assert "明日、東京に行きますか。" in md
    assert "(しゅうまつ)" not in md and "しゅうまつ" not in md


# -- 07: tategaki con furigana -------------------------------------------------

TATEGAKI = (
    "山の小さな駅"
    "朝の電車は、霧の中をゆっくり走っていた。窓の外には田んぼが広がり、遠くに青い山が見えた。"
    "「次は終点です」と、車掌さんが言った。私は荷物を持って、ドアの前に立った。"
    "駅には誰もいなかった。ベンチの上で、白い猫が一匹、眠っていた。"
    "祖母の家までは、ここから歩いて二十分ほどだ。子供のころ、夏休みになると毎年この道を歩いた。"
)


def test_tategaki(convert, mode):
    md = convert("07_tategaki_furigana.pdf", mode).text_content
    assert md.startswith("# 山(やま)の小(ちい)さな駅(えき)")
    assert "朝(あさ)の電車(でんしゃ)は、霧(きり)の中(なか)をゆっくり走(はし)っていた。" in md
    assert "遠(とお)くに青(あお)い山(やま)が見(み)えた。" in md  # salto de columna sin espacio
    assert "「次(つぎ)は終点(しゅうてん)です」と、車掌(しゃしょう)さんが言(い)った。" in md
    assert not JA_SPACE.search(md)
    assert "17" not in md  # número de página
    assert len([p for p in md.split("\n\n") if p.strip()]) == 5  # título + 4 párrafos


def test_tategaki_sin_furigana_texto_exacto(convert, mode):
    md = convert("07_tategaki_furigana.pdf", mode, furigana=False).text_content
    assert re.sub(r"[\s#]", "", md) == TATEGAKI


# -- 08: tabla de vocabulario --------------------------------------------------

def test_vocabulario(convert, mode):
    md = convert("08_vocabulario.pdf", mode).text_content
    assert "| Lectura | Kanji | Español |" in md
    for row in ("| しゅうまつ | 週末 | fin de semana |", "| べんきょうする | 勉強する | estudiar |",
                "| コーヒー | — | café |", "| テスト | — | examen, prueba |"):
        assert row in md, row
    assert md.count("週末") == 1
    assert len(re.findall(r"^\|", md, re.M)) == 14
    assert "como コーヒー, vienen de otros idiomas" in md


# -- Escaneados (OCR) ----------------------------------------------------------

ACTA = ("Acta de la reunión de vecinos La reunión comenzó a las siete de la tarde en el salón de la comunidad. "
        "Asistieron veintitrés propietarios, que representan más de la mitad de las viviendas del edificio. "
        "En primer lugar se aprobó el presupuesto del año próximo. A continuación se decidió pintar la fachada en "
        "primavera y cambiar la iluminación del portal por lámparas de bajo consumo. Por último, la señora Núñez "
        "propuso instalar un aparcamiento para bicicletas en el patio. La propuesta se aprobó por unanimidad. "
        "Sin más asuntos que tratar, se levantó la sesión a las ocho y cuarto.")

DIARIO_JA = ("わたしの一日わたしは毎朝六時に起きます。朝ごはんを食べて、七時半に家を出ます。会社は東京にあります。"
             "電車で四十分ぐらいかかります。昼休みに同僚とラーメンを食べました。とてもおいしかったです。"
             "夜は家で日本語の本を読みます。十一時ごろ寝ます。")


@pytest.mark.ocr
def test_escaneado_es(convert, mode):
    res = convert("04_escaneado.pdf", mode)
    assert res.ocr_pages == [1]
    assert any("OCR" in w for w in res.warnings)
    assert similarity(ACTA, res.text_content) >= 0.985
    assert "señora Núñez" in res.text_content and "año próximo" in res.text_content
    assert len([p for p in res.text_content.split("\n\n") if p.strip()]) == 5


@pytest.mark.ocr
def test_escaneado_ja(convert, mode):
    res = convert("09_ja_escaneado.pdf", mode)
    md = res.text_content
    assert res.ocr_pages == [1]
    assert similarity(DIARIO_JA, md) >= 0.97
    assert "七時半に家を出ます。" in md  # línea partida unida sin espacio
    assert not JA_SPACE.search(md)
    assert "Me levanto todos los días a las seis. Desayuno y salgo de casa a las siete y media." in md
    assert in_order(md, "わたしの一日", "会社は東京にあります", "Traducción", "Mi empresa está en Tokio")


@pytest.mark.ocr
def test_fuente_rota_pasa_a_ocr(convert, mode):
    res = convert("10_fuente_rota.pdf", mode)
    md = res.text_content
    assert res.ocr_pages == [1], "no se detectó la fuente mal codificada"
    assert not re.search(r"[\x00-\x08\x0e-\x1f]", md)
    assert "Takeshi: ¿Qué vas a hacer el fin de semana?" in md
    sin = re.sub(r"\([ぁ-ヿー]+\)", "", md)
    esperado = "".join(re.sub(r"\([ぁ-ヿー]+\)", "", ja) for ja, _ in PARES)
    # Con furigana, Tesseract solo lee peor que RapidOCR (que viene con el modo calidad)
    from papiro.pdf import ocr

    solo_tesseract = not ocr.rapidocr_installed()
    assert similarity(esperado, sin) >= (0.85 if solo_tesseract else 0.93)
    assert in_order(md, "いいですね", "Takeshi: ¡Qué bien!", "Almuerzo en la escuela.", "¿Vas a Tokio mañana?")
    assert md.count("(") >= (8 if solo_tesseract else 12), "se perdió casi todo el furigana"
