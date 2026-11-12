#!/usr/bin/env bash
# Instalador de Papiro para Linux y macOS.
#
#   ./install.sh              pregunta lo necesario
#   ./install.sh --si         instala todo sin preguntar
#   opciones: --calidad | --sin-calidad | --ocr | --sin-ocr | --sin-icono
set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

YES=0; CALIDAD=""; OCR=""; ICONO=1
for arg in "$@"; do
  case "$arg" in
    --si|-y|--yes) YES=1 ;;
    --calidad) CALIDAD=1 ;;
    --sin-calidad) CALIDAD=0 ;;
    --ocr) OCR=1 ;;
    --sin-ocr) OCR=0 ;;
    --sin-icono) ICONO=0 ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opción desconocida: $arg"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33mAVISO: %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*"; exit 1; }

ask() {  # ask "pregunta" valor_por_defecto(1/0)
  local def="$2" ans
  if [ "$YES" = 1 ] || [ ! -t 0 ]; then echo "$def"; return; fi
  if [ "$def" = 1 ]; then read -r -p "$1 [S/n] " ans; else read -r -p "$1 [s/N] " ans; fi
  case "${ans:-}" in
    [sSyY]*) echo 1 ;;
    [nN]*) echo 0 ;;
    *) echo "$def" ;;
  esac
}

SUDO=""
if [ "$(id -u)" != 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

OS="$(uname -s)"
PM=""
if [ "$OS" = "Darwin" ]; then
  command -v brew >/dev/null 2>&1 && PM="brew"
else
  for c in apt-get dnf pacman zypper; do
    if command -v "$c" >/dev/null 2>&1; then PM="$c"; break; fi
  done
fi

pkg_install() {  # instala paquetes del sistema; devuelve 1 si no puede
  case "$PM" in
    apt-get) $SUDO apt-get update -qq && $SUDO apt-get install -y "$@" ;;
    dnf)     $SUDO dnf install -y "$@" ;;
    pacman)  $SUDO pacman -S --needed --noconfirm "$@" ;;
    zypper)  $SUDO zypper --non-interactive install "$@" ;;
    brew)    brew install "$@" ;;
    *)       return 1 ;;
  esac
}

py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then echo "$c"; return 0; fi
  done
  return 1
}

# ---------------------------------------------------------------- 1. Python
say "1/5  Python"
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  echo "No hay Python 3.10 o superior. Se instala ahora."
  case "$PM" in
    apt-get) pkg_install python3 python3-venv python3-pip python3-tk ;;
    dnf)     pkg_install python3 python3-pip python3-tkinter ;;
    pacman)  pkg_install python python-pip tk ;;
    zypper)  pkg_install python3 python3-pip python3-tk ;;
    brew)    pkg_install python@3.12 python-tk@3.12 ;;
    *)
      if [ "$OS" = "Darwin" ]; then
        PKG="/tmp/python-papiro.pkg"
        echo "Descargando Python desde python.org…"
        curl -fL -o "$PKG" "https://www.python.org/ftp/python/3.12.8/python-3.12.8-macos11.pkg" \
          && sudo installer -pkg "$PKG" -target / || die "No se pudo instalar Python. Instálalo desde https://www.python.org/downloads/ y vuelve a ejecutar este instalador."
        export PATH="/Library/Frameworks/Python.framework/Versions/3.12/bin:$PATH"
      else
        die "No sé instalar Python en este sistema. Instala Python 3.10 o superior y vuelve a ejecutar este instalador."
      fi ;;
  esac
  PY="$(find_python || true)"
  [ -n "$PY" ] || die "Python sigue sin estar disponible."
fi
echo "Python: $($PY --version 2>&1) ($(command -v "$PY"))"

# En Linux, venv y tkinter van en paquetes aparte
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  echo "Falta el módulo venv. Se instala."
  case "$PM" in
    apt-get) pkg_install python3-venv python3-pip ;;
    dnf|zypper) pkg_install python3-pip ;;
    pacman) pkg_install python-pip ;;
  esac
  "$PY" -c 'import venv, ensurepip' 2>/dev/null || die "No se pudo preparar venv. Instala el paquete python3-venv."
fi
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
  echo "Falta tkinter (hace falta para la ventana). Se instala."
  case "$PM" in
    apt-get|zypper) pkg_install python3-tk ;;
    dnf) pkg_install python3-tkinter ;;
    pacman) pkg_install tk ;;
    brew) pkg_install python-tk ;;
  esac
  "$PY" -c 'import tkinter' 2>/dev/null || warn "No se pudo instalar tkinter: la ventana no funcionará, pero la línea de comandos sí."
fi

# ---------------------------------------------------------------- 2. Preguntas
if [ -z "$CALIDAD" ]; then
  echo
  echo "El MODO CALIDAD entiende mejor las páginas complicadas (columnas, tablas) y lee"
  echo "mucho mejor el japonés escaneado. Ocupa unos 2 GB y tarda en descargarse."
  echo "Sin él, Papiro funciona en modo rápido."
  CALIDAD="$(ask '¿Instalar el modo calidad?' 1)"
fi
if [ -z "$OCR" ]; then
  echo
  echo "El OCR lee documentos ESCANEADOS (fotos de páginas). Se instala Tesseract"
  echo "con japonés (horizontal y vertical), español e inglés."
  OCR="$(ask '¿Instalar el OCR?' 1)"
fi

# ---------------------------------------------------------------- 3. Entorno y dependencias
say "2/5  Entorno virtual y dependencias"
if [ ! -x "$DIR/.venv/bin/python" ]; then
  "$PY" -m venv "$DIR/.venv" || die "No se pudo crear el entorno virtual."
fi
VPY="$DIR/.venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip || die "pip no funciona."
"$VPY" -m pip install --quiet -e "$DIR" || die "No se pudieron instalar las dependencias."
"$VPY" -m pip install --quiet "tkinterdnd2>=0.4" || warn "Sin arrastrar y soltar dentro de la ventana (tkinterdnd2 no se pudo instalar)."

if [ "$CALIDAD" = 1 ]; then
  say "3/5  Modo calidad (descarga grande, paciencia)"
  if [ "$OS" = "Linux" ]; then
    # PyTorch solo para CPU: evita más de 2 GB de bibliotecas de tarjeta gráfica
    "$VPY" -m pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu \
      || warn "No se pudo instalar PyTorch para CPU; se intentará la versión normal."
  fi
  if "$VPY" -m pip install --quiet -e "$DIR[calidad]"; then
    "$VPY" -m papiro --descargar-modelos || warn "No se pudieron descargar los modelos ahora. Se descargarán la primera vez que conviertas un PDF."
  else
    warn "No se pudo instalar el modo calidad. Papiro funcionará en modo rápido."
  fi
else
  say "3/5  Modo calidad: omitido (se usará el modo rápido)"
fi

# ---------------------------------------------------------------- 4. OCR
if [ "$OCR" = 1 ]; then
  say "4/5  OCR: Tesseract con japonés, japonés vertical, español e inglés"
  if ! command -v tesseract >/dev/null 2>&1; then
    case "$PM" in
      apt-get) pkg_install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert tesseract-ocr-spa tesseract-ocr-eng ;;
      dnf)     pkg_install tesseract tesseract-langpack-jpn tesseract-langpack-jpn_vert tesseract-langpack-spa tesseract-langpack-eng ;;
      pacman)  pkg_install tesseract tesseract-data-jpn tesseract-data-jpn_vert tesseract-data-spa tesseract-data-eng ;;
      zypper)  pkg_install tesseract-ocr tesseract-ocr-traineddata-japanese tesseract-ocr-traineddata-spanish tesseract-ocr-traineddata-english ;;
      brew)    pkg_install tesseract tesseract-lang ;;
      *)       warn "No sé instalar Tesseract en este sistema. Instálalo a mano y vuelve a ejecutar este instalador." ;;
    esac
  elif [ "$PM" = "apt-get" ]; then
    pkg_install tesseract-ocr-jpn tesseract-ocr-jpn-vert tesseract-ocr-spa tesseract-ocr-eng || true
  fi
  if command -v tesseract >/dev/null 2>&1; then
    # Si falta algún idioma, se descargan todos a la carpeta propia de Papiro (~/.papiro/tessdata)
    MISSING="$("$VPY" -c 'from papiro.pdf import ocr; print(" ".join(ocr.ocr_status()["faltan"]))')"
    if [ -n "$MISSING" ]; then
      echo "Faltan idiomas ($MISSING). Se descargan de github.com/tesseract-ocr/tessdata_fast…"
      "$VPY" -m papiro --instalar-idiomas || warn "No se pudieron descargar los idiomas."
    fi
  else
    warn "Tesseract no se instaló. Los documentos escaneados no se podrán convertir (Papiro te avisará)."
  fi
else
  say "4/5  OCR: omitido"
fi

# ---------------------------------------------------------------- 5. Icono y comando
say "5/5  Icono y comando"
mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/.venv/bin/papiro" "$HOME/.local/bin/papiro" 2>/dev/null || true

if [ "$ICONO" = 1 ]; then
  if [ "$OS" = "Darwin" ]; then
    APP="$HOME/Applications/Papiro.app"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    cat > "$APP/Contents/MacOS/Papiro" <<EOF
#!/bin/sh
exec "$VPY" -m papiro --gui "\$@"
EOF
    chmod +x "$APP/Contents/MacOS/Papiro"
    cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Papiro</string>
  <key>CFBundleIdentifier</key><string>io.github.papiro</string>
  <key>CFBundleExecutable</key><string>Papiro</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>papiro</string>
  <key>CFBundleDocumentTypes</key><array><dict>
    <key>CFBundleTypeRole</key><string>Viewer</string>
    <key>LSItemContentTypes</key><array><string>public.data</string><string>public.folder</string></array>
  </dict></array>
</dict></plist>
EOF
    if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
      ICONSET="$(mktemp -d)/papiro.iconset"; mkdir -p "$ICONSET"
      for s in 16 32 128 256 512; do sips -z $s $s "$DIR/papiro/assets/papiro.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1; done
      iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/papiro.icns" 2>/dev/null || true
    fi
    ln -sfn "$APP" "$HOME/Desktop/Papiro" 2>/dev/null || true
    echo "Aplicación: $APP (y acceso directo en el Escritorio)"
  else
    APPS="$HOME/.local/share/applications"
    mkdir -p "$APPS"
    DESKTOP_FILE="$APPS/papiro.desktop"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Papiro
GenericName=Documentos a Markdown
Comment=Convierte PDF, Word, PowerPoint, Excel y más a Markdown para IA
Exec="$VPY" -m papiro --gui %F
Icon=$DIR/papiro/assets/papiro.png
Terminal=false
Categories=Office;Utility;
MimeType=application/pdf;application/epub+zip;text/html;text/csv;application/zip;image/png;image/jpeg;application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/vnd.openxmlformats-officedocument.presentationml.presentation;application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;
StartupNotify=true
EOF
    chmod +x "$DESKTOP_FILE"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
    DESK="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    [ -n "$DESK" ] && [ "$DESK" != "$HOME" ] || DESK="$HOME/Desktop"
    [ -d "$DESK" ] || DESK="$HOME/Escritorio"
    if [ -d "$DESK" ]; then
      cp "$DESKTOP_FILE" "$DESK/papiro.desktop" && chmod +x "$DESK/papiro.desktop"
      # GNOME y Cinnamon piden marcar el lanzador como de confianza
      gio set "$DESK/papiro.desktop" metadata::trusted true >/dev/null 2>&1 || true
      echo "Icono en el escritorio: $DESK/papiro.desktop"
    else
      warn "No encuentro la carpeta del escritorio; el icono está solo en el menú de aplicaciones."
    fi
    echo "Icono en el menú de aplicaciones: $DESKTOP_FILE"
  fi
fi

say "Instalación terminada"
"$VPY" -m papiro --estado || true
echo
echo "Para abrir Papiro: doble clic en el icono «Papiro» del escritorio o del menú de aplicaciones."
echo "También puedes arrastrar archivos encima del icono."
echo "Desde la terminal:  papiro archivo.pdf -o salida.md"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "(Si el comando «papiro» no se encuentra, cierra la sesión y vuelve a entrar.)" ;; esac
