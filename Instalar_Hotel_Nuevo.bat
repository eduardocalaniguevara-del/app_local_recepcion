@echo off
chcp 65001 > nul
title Instalador Multi-Hotel (Descarga desde GitHub)
echo ====================================================================
echo   INSTALADOR AUTOMATICO DEL SISTEMA DE RECEPCION (MULTI-HOTEL)
echo   Repositorio: https://github.com/eduardocalaniguevara-del/app_local_recepcion.git
echo ====================================================================
echo.

set DEST_DIR=C:\Recepcion_Ovalo_Local
set GIT_DIR=C:\Hostal_Sistema\tools\mingit
set GIT_EXE=%GIT_DIR%\cmd\git.exe

if not exist "%GIT_EXE%" (
    echo [1/4] Descargando Git portable silencioso...
    powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path '%GIT_DIR%' | Out-Null; Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip' -OutFile '%GIT_DIR%\mingit.zip'; Expand-Archive -Path '%GIT_DIR%\mingit.zip' -DestinationPath '%GIT_DIR%' -Force; Remove-Item '%GIT_DIR%\mingit.zip' -Force"
)

echo [2/4] Descargando el programa desde GitHub en %DEST_DIR%...
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"
if not exist "%DEST_DIR%\.git" (
    "%GIT_EXE%" -C "%DEST_DIR%" init
    "%GIT_EXE%" -C "%DEST_DIR%" branch -M main
    "%GIT_EXE%" -C "%DEST_DIR%" remote add origin https://github.com/eduardocalaniguevara-del/app_local_recepcion.git
)
"%GIT_EXE%" -C "%DEST_DIR%" fetch origin main
"%GIT_EXE%" -C "%DEST_DIR%" reset --hard origin/main

if not exist "%DEST_DIR%\data" mkdir "%DEST_DIR%\data"

if not exist "%DEST_DIR%\data\hotel_perfil.json" (
    echo.
    echo [3/4] Configuracion de la hoja de Google Sheets de este Hotel:
    set /p HOTEL_NOMBRE="Nombre del Hotel (ej. Terrazas de Villa): "
    set /p SHEET_ID="Pega el ID del Google Sheet de este hotel: "
    powershell -NoProfile -Command "$p = @{ nombre_hotel = '%HOTEL_NOMBRE%'; spreadsheet_id = '%SHEET_ID%'; col_tipo_adicional = 'BA'; formula_snacks_en_s2 = $false; app_port = 8080 }; $p | ConvertTo-Json | Set-Content -Path '%DEST_DIR%\data\hotel_perfil.json' -Encoding UTF8"
    echo [OK] Perfil guardado en %DEST_DIR%\data\hotel_perfil.json
) else (
    echo [3/4] Perfil existente conservado en %DEST_DIR%\data\hotel_perfil.json
)

echo.
echo [4/4] Instalacion completada. Recuerda copiar credentials.json dentro de %DEST_DIR%\data\credentials.json
echo       El sistema se actualizara solo desde GitHub cada 45 segundos.
pause
