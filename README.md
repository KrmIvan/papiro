# Papiro

Convierte documentos a **Markdown limpio para usar con IA** (RAG, LLM). Gratis, de código abierto y **todo en tu ordenador**: nada se envía a internet ni a servicios de pago.

Hace lo mismo que `markitdown`, pero cuida mucho más los PDF y el **japonés**: texto vertical (tategaki), furigana, libros bilingües japonés-español y páginas escaneadas.

[![CI](https://github.com/KrmIvan/papiro/actions/workflows/ci.yml/badge.svg)](https://github.com/KrmIvan/papiro/actions/workflows/ci.yml)

## Qué convierte

| Formato | Notas |
| --- | --- |
| PDF | Orden de lectura, columnas, títulos, tablas, cabeceras y pies repetidos, sellos, OCR automático |
| Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`) | PowerPoint incluye las notas del orador |
| CSV, HTML, páginas web (URL), EPUB, JSON, XML, texto | El furigana de HTML y EPUB (`<ruby>`) también se conserva |
| Imágenes (`.png`, `.jpg`, `.tif`…) | Con OCR, también en japonés |
| ZIP | Convierte todo lo que hay dentro |

## Instalación paso a paso

No hace falta saber programar. Necesitas conexión a internet solo durante la instalación.

### 1. Descargar

1. Entra en <https://github.com/KrmIvan/papiro>.
2. Pulsa el botón verde **Code** y después **Download ZIP**.
3. Descomprime el ZIP en una carpeta que no vayas a borrar (por ejemplo, `Documentos/papiro`). La aplicación se queda en esa carpeta.

### 2. Instalar

**Windows**

1. Abre la carpeta y haz doble clic en **`install.bat`**.
2. Si Windows muestra «Windows protegió su PC», pulsa **Más información** y **Ejecutar de todas formas**.
3. Responde a las dos preguntas (pulsa Intro para decir que sí):
   - **¿Instalar el modo calidad?** Mejor resultado, sobre todo con japonés escaneado. Ocupa unos 2 GB.
   - **¿Instalar el OCR?** Necesario para documentos escaneados.
4. Espera a que ponga «Instalación terminada». Puede tardar entre 5 y 20 minutos.

**Linux y macOS**

1. Abre una terminal en la carpeta (clic derecho dentro de la carpeta, «Abrir en una terminal»).
2. Escribe esto y pulsa Intro:

```bash
./install.sh
```

3. Responde a las dos preguntas. Si pide tu contraseña, es para instalar Tesseract (el programa de OCR).

El instalador hace todo esto por ti: instala Python si falta, crea un entorno aislado dentro de la carpeta, descarga las dependencias, instala Tesseract con japonés (`jpn`), japonés vertical (`jpn_vert`), español (`spa`) e inglés (`eng`), y crea el icono.

### 3. Abrir

Haz **doble clic en el icono «Papiro»** del escritorio o del menú de aplicaciones (menú Inicio en Windows).

También puedes **arrastrar archivos encima del icono**: se abre con esos archivos ya en la lista.

## Cómo se usa la ventana

1. **Añadir archivos…** o **Añadir carpeta…** (o arrástralos a la lista).
2. Deja las opciones en «Automático», salvo que quieras algo concreto:
   - **Modo**: *Calidad* (más lento, mejor) o *Rápido*.
   - **OCR**: *Automático* solo lo usa en páginas escaneadas o ilegibles. *Siempre* lo fuerza. *Nunca* lo desactiva.
   - **Idioma del OCR**: fuerza japonés o español si el automático se equivoca.
   - **Mantener el furigana**: `漢字(かんじ)` si está marcado, `漢字` si no.
3. Pulsa **Convertir**. La barra muestra el progreso y puedes cancelar. La ventana no se bloquea aunque el libro tenga cientos de páginas.
4. Revisa la **vista previa**; puedes corregirla a mano.
5. **Copiar**, **Guardar .md…** o **Guardar todos en carpeta…**

Si un archivo falla o estaba escaneado, aparece en naranja o rojo y el motivo se explica debajo de la vista previa.

## Línea de comandos

```bash
papiro archivo.pdf -o salida.md
```

```bash
papiro carpeta/ -o markdown/
```

```bash
papiro informe.docx notas.pptx datos.xlsx
```

```bash
cat archivo.pdf | papiro - > salida.md
```

```bash
papiro https://ejemplo.org/articulo.html -o articulo.md
```

Opciones principales: `--modo calidad|rapido`, `--ocr auto|siempre|nunca`, `--idioma auto|ja|es|en`, `--sin-furigana`, `--marcar-paginas`, `--estado`. Lista completa: `papiro --help`.

En Windows el comando está en `.venv\Scripts\papiro.exe` dentro de la carpeta.

## Desde Python

```python
from papiro import Converter

resultado = Converter().convert("libro.pdf")
print(resultado.text_content)   # el Markdown
print(resultado.warnings)       # avisos: páginas con OCR, etc.

# Opciones
Converter(mode="rapido", furigana=False, lang="ja").convert("pagina.png")
```

`convert()` acepta una ruta, una URL, bytes o un archivo abierto.

## Japonés

- **Idioma automático** por página y por bloque. No hay que elegirlo.
- **Tategaki**: el texto vertical se lee de arriba abajo y de derecha a izquierda. No se confunde con texto girado del margen.
- **Furigana**: nunca se mezcla con el texto. Sale como `漢字(かんじ)`, o se quita con una opción.
- **Sin espacios falsos** entre caracteres japoneses al unir líneas. Se respetan `。、「」『』・…` y los caracteres de ancho completo. Los guiones de final de línea solo se unen en texto latino.
- **Unicode**: se corrigen katakana de ancho medio, radicales Kangxi usados como kanji (`⽇` → `日`), ligaduras y dakuten sueltos, sin tocar el resto.
- **Fuentes mal codificadas**: si el texto de una página sale ilegible (mojibake), esa página pasa a OCR sola.
- **Pares japonés-español**: frase y traducción quedan juntas y en el orden del libro, también cuando van lado a lado. Las tablas de vocabulario salen como tablas Markdown.

Ejemplo real (PDF de prueba `06_bilingue_horizontal.pdf`):

```markdown
たけし：週末(しゅうまつ)は何(なに)をしますか。

Takeshi: ¿Qué vas a hacer el fin de semana?

| Lectura | Kanji | Español |
| --- | --- | --- |
| しゅうまつ | 週末 | fin de semana |
```

La salida completa de los diez PDF de prueba está en [`ejemplos/`](ejemplos/).

## Por qué estos motores

### Maqueta del PDF: docling

Papiro no analiza la maqueta por su cuenta en el modo calidad: usa los modelos de [docling](https://github.com/docling-project/docling) (IBM, licencia MIT) para encontrar bloques, títulos, tablas y orden de lectura.

| Motor | Licencia | Por qué sí o por qué no |
| --- | --- | --- |
| **docling** | MIT | Elegido. Modelo de maqueta y de tablas, funciona en CPU, licencia compatible. |
| marker / Surya | GPL-3.0, modelos con uso comercial restringido | No permite publicar el proyecto como MIT. |
| MinerU | AGPL-3.0 | Misma razón. |
| PyMuPDF / pymupdf4llm | AGPL-3.0 | Misma razón. Por eso Papiro lee el PDF con pypdfium2 (Apache/BSD). |
| pdfplumber solo | MIT | No tiene modelo de maqueta. Se usa en el modo rápido para las tablas con líneas. |

Docling solo no basta para japonés: mezcla el furigana con el texto y lee las columnas verticales al revés. Por eso Papiro solo le pide **dónde están los bloques** y pone él mismo el texto, carácter a carácter: así controla el furigana, el tategaki, los espacios y los guiones. En las páginas verticales no se usa docling.

El **modo rápido** no usa modelos: separa columnas por los huecos en blanco y detecta tablas dibujadas con líneas. Se instala en un minuto y ocupa unos 100 MB.

### OCR: Tesseract y RapidOCR, cada uno donde gana

Medido con las páginas escaneadas de prueba, en un portátil de 4 núcleos sin tarjeta gráfica:

| Página escaneada | Tesseract 5 | RapidOCR (PP-OCRv6 pequeño) |
| --- | --- | --- |
| Español | **100 %**, 1,4 s | 88 %, 7,9 s |
| Japonés horizontal, sin furigana | 100 %, 2,8 s | 100 %, 10 s |
| Japonés horizontal **con furigana** | mezcla el furigana, pierde kanji | **casi perfecto**, 13 s |
| Japonés vertical | **lo lee** (`jpn_vert`) | 30 %, pierde la puntuación |
| Tabla de vocabulario | falla en kana sueltos | **perfecta** |

(El modelo «medio» de PP-OCRv6 acierta lo mismo, pero tarda más de dos minutos por página en CPU.)

Por eso, en automático:

- **Español, inglés y japonés vertical** → Tesseract.
- **Japonés horizontal** → RapidOCR, si está instalado (viene con el modo calidad). Las líneas solo en español de esa página se toman de Tesseract, que conserva mejor `¿ ¡` y los espacios.
- Si solo hay Tesseract, se usa para todo: Papiro separa el furigana de la imagen antes de leer y relee aparte los bloques que están lado a lado.

Si no hay ningún OCR instalado, Papiro **avisa con claridad** y lo escribe también en el Markdown. Nunca devuelve un archivo vacío.

## Límites que conviene conocer

- El **furigana en páginas escaneadas** sale bien casi siempre, no siempre. Son letras de 2 mm; alguna lectura se pierde o se lee mal. En PDF con texto real sale exacto.
- En el **modo rápido**, los títulos se deciden por el tamaño de letra y las tablas escaneadas no se reconocen. Para eso está el modo calidad.
- El modo calidad es **lento sin tarjeta gráfica**: de 3 a 10 segundos por página con texto, de 15 a 25 por página japonesa escaneada. Un libro escaneado de 300 páginas puede tardar más de una hora.
- `.doc`, `.ppt` y `.xls` antiguos no se convierten: guárdalos antes como `.docx`, `.pptx` o `.xlsx`.

## Desarrollo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,gui]"          # modo rápido
pip install -e ".[calidad,dev,gui]"  # con docling y RapidOCR
pytest
```

Los PDF de prueba están en `tests/fixtures/pdf/` y se regeneran con `python tests/fixtures/build_fixtures.py` (necesita Chrome). Todo su contenido es original.

La integración continua ejecuta los tests y los dos instaladores en Ubuntu y Windows.

## Licencia

[MIT](LICENSE).
