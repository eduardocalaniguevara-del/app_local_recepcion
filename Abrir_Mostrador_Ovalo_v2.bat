@echo off
chcp 65001 >nul
title Mostrador Operativo - Hostal Ovalo Sistema v2 (Puerto 8090 en L1 / 8080 en L2)
cd /d "C:\Ovalo_Sistema_v2"

REM Verificar si el puerto 8090 ya esta encendido
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue; if ($c) { exit 0 } else { exit 1 }"
if %ERRORLEVEL% EQU 0 (
    echo [OK] El Mostrador Sistema v2 ya estaba activo. Abriendo Microsoft Edge...
    start msedge "--app=http://127.0.0.1:8090"
    exit /b 0
)

echo ================================================================
echo   HOSTAL OVALO MARIATEGUI - SISTEMA V2 (http://127.0.0.1:8090)
echo   - Carpeta: C:\Ovalo_Sistema_v2
echo   - Base SQLite: 03_DB_sqlite_local\ovalo.db
echo   - Abriendo Mostrador en Microsoft Edge...
echo ================================================================

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 1400; Start-Process msedge '--app=http://127.0.0.1:8090'"
python "C:\Ovalo_Sistema_v2\main.py"
