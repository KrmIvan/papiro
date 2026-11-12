@echo off
rem Instalador de Papiro para Windows.
rem   install.bat            pregunta lo necesario (doble clic)
rem   install.bat /si        instala todo sin preguntar
rem   opciones: /calidad /sin-calidad /ocr /sin-ocr /sin-icono
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"

set "YES=0"
set "CALIDAD="
set "OCR="
set "ICONO=1"
:args
if "%~1"=="" goto args_done
if /i "%~1"=="/si" set "YES=1"
if /i "%~1"=="--si" set "YES=1"
if /i "%~1"=="/calidad" set "CALIDAD=1"
if /i "%~1"=="/sin-calidad" set "CALIDAD=0"
if /i "%~1"=="/ocr" set "OCR=1"
if /i "%~1"=="/sin-ocr" set "OCR=0"
if /i "%~1"=="/sin-icono" set "ICONO=0"
shift
goto args
:args_done

echo.
echo ===== 1/5  Python =====
call :find_python
if not defined PY (
  echo No hay Python 3.10 o superior. Se instala ahora.
  where winget >nul 2>&1
  if not errorlevel 1 (
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  )
  call :find_python
)
if not defined PY (
  echo Descargando Python desde python.org...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile \"$env:TEMP\python-papiro.exe\""
  if exist "%TEMP%\python-papiro.exe" (
    "%TEMP%\python-papiro.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_launcher=1
  )
  call :find_python
)
if not defined PY (
  echo ERROR: no se pudo instalar Python. Instalalo desde https://www.python.org/downloads/
  echo        marcando "Add python.exe to PATH", y vuelve a ejecutar este instalador.
  goto fail
)
echo Python: %PY%

if not defined CALIDAD (
  echo.
  echo El MODO CALIDAD entiende mejor las paginas complicadas ^(columnas, tablas^) y lee
  echo mucho mejor el japones escaneado. Ocupa unos 2 GB y tarda en descargarse.
  echo Sin el, Papiro funciona en modo rapido.
  call :ask "Instalar el modo calidad? [S/n] " CALIDAD
)
if not defined OCR (
  echo.
  echo El OCR lee documentos ESCANEADOS ^(fotos de paginas^). Se instala Tesseract
  echo con japones ^(horizontal y vertical^), espanol e ingles.
  call :ask "Instalar el OCR? [S/n] " OCR
)

echo.
echo ===== 2/5  Entorno virtual y dependencias =====
if not exist "%DIR%\.venv\Scripts\python.exe" (
  %PY% -m venv "%DIR%\.venv"
  if errorlevel 1 (
    echo ERROR: no se pudo crear el entorno virtual.
    goto fail
  )
)
set "VPY=%DIR%\.venv\Scripts\python.exe"
"%VPY%" -m pip install --quiet --upgrade pip
"%VPY%" -m pip install --quiet -e "%DIR%"
if errorlevel 1 (
  echo ERROR: no se pudieron instalar las dependencias.
  goto fail
)
"%VPY%" -m pip install --quiet "tkinterdnd2>=0.4"
if errorlevel 1 echo AVISO: sin arrastrar y soltar dentro de la ventana.

echo.
if "%CALIDAD%"=="1" (
  echo ===== 3/5  Modo calidad ^(descarga grande, paciencia^) =====
  "%VPY%" -m pip install --quiet -e "%DIR%[calidad]"
  if errorlevel 1 (
    echo AVISO: no se pudo instalar el modo calidad. Papiro funcionara en modo rapido.
  ) else (
    "%VPY%" -m papiro --descargar-modelos
    if errorlevel 1 echo AVISO: los modelos se descargaran la primera vez que conviertas un PDF.
  )
) else (
  echo ===== 3/5  Modo calidad: omitido ^(se usara el modo rapido^) =====
)

echo.
if "%OCR%"=="1" (
  echo ===== 4/5  OCR: Tesseract con japones, japones vertical, espanol e ingles =====
  call :find_tesseract
  if not defined TESS (
    where winget >nul 2>&1
    if not errorlevel 1 (
      winget install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements
    )
    call :find_tesseract
  )
  if not defined TESS (
    where choco >nul 2>&1
    if not errorlevel 1 choco install tesseract -y --no-progress
    call :find_tesseract
  )
  if not defined TESS (
    echo Descargando Tesseract...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe' -OutFile \"$env:TEMP\tesseract-papiro.exe\""
    if exist "%TEMP%\tesseract-papiro.exe" "%TEMP%\tesseract-papiro.exe" /S
    call :find_tesseract
  )
  if defined TESS (
    echo Tesseract: !TESS!
    echo Descargando los idiomas ^(jpn, jpn_vert, spa, eng^)...
    "%VPY%" -m papiro --instalar-idiomas
    if errorlevel 1 echo AVISO: no se pudieron descargar todos los idiomas.
  ) else (
    echo AVISO: Tesseract no se instalo. Los documentos escaneados no se podran convertir
    echo        ^(Papiro te avisara^). Puedes instalarlo desde
    echo        https://github.com/UB-Mannheim/tesseract/wiki y volver a ejecutar este instalador.
  )
) else (
  echo ===== 4/5  OCR: omitido =====
)

echo.
echo ===== 5/5  Iconos =====
if "%ICONO%"=="1" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$d='%DIR%'; $w=New-Object -ComObject WScript.Shell; foreach($p in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ if($p){ $s=$w.CreateShortcut((Join-Path $p 'Papiro.lnk')); $s.TargetPath=(Join-Path $d '.venv\Scripts\pythonw.exe'); $s.Arguments='-m papiro --gui'; $s.WorkingDirectory=$d; $s.IconLocation=(Join-Path $d 'papiro\assets\papiro.ico'); $s.Description='Papiro: documentos a Markdown'; $s.Save(); Write-Host ('Icono: ' + (Join-Path $p 'Papiro.lnk')) } }"
  if errorlevel 1 echo AVISO: no se pudieron crear los iconos.
)

echo.
echo ===== Instalacion terminada =====
"%VPY%" -m papiro --estado
echo.
echo Para abrir Papiro: doble clic en el icono "Papiro" del escritorio o del menu Inicio.
echo Tambien puedes arrastrar archivos encima del icono.
echo Desde la terminal:  "%DIR%\.venv\Scripts\papiro.exe" archivo.pdf -o salida.md
if "%YES%"=="0" pause
endlocal
exit /b 0

:fail
if "%YES%"=="0" pause
endlocal
exit /b 1

:find_python
set "PY="
for %%C in ("py -3" "python" "python3") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=%%~C"
  )
)
if not defined PY (
  for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%LOCALAPPDATA%\Programs\Python\Python310\python.exe") do (
    if not defined PY if exist %%P set PY=%%P
  )
)
exit /b 0

:find_tesseract
set "TESS="
for %%P in ("%ProgramFiles%\Tesseract-OCR\tesseract.exe" "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe") do (
  if not defined TESS if exist %%P set "TESS=%%~P"
)
if not defined TESS (
  for /f "delims=" %%T in ('where tesseract 2^>nul') do if not defined TESS set "TESS=%%T"
)
exit /b 0

:ask
rem :ask "pregunta" VARIABLE  -> 1 (por defecto) o 0
set "%~2=1"
if "%YES%"=="1" exit /b 0
set "ANS="
set /p "ANS=%~1"
if /i "!ANS!"=="n" set "%~2=0"
if /i "!ANS!"=="no" set "%~2=0"
exit /b 0
