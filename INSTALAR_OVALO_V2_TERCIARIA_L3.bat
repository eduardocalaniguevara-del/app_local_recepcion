@echo off
chcp 65001 >nul
title Instalador 1-Clic — Hostal Ovalo Sistema v2 (MODO: NIVEL_3_TERCIARIA / VISOR)
echo ============================================================================
echo   INSTALADOR AUTOMATICO — C:\Ovalo_Sistema_v2 (MODO: NIVEL_3_TERCIARIA)
echo   - Para Laptop 3 o PC de Pruebas / Administracion
echo   - Configura las 5 carpetas (01_PRIV a 05_GIT) en Solo Lectura Git
echo   - Recibe TODA la informacion en vivo desde Supabase (02_DB_sqlite_local\ovalo.db)
echo   - CERO QR de WhatsApp y CERO Vigilante 24/7 encendido (para no duplicar envios)
echo   - Crea el icono de Google Chrome en el Escritorio (:8080)
echo ============================================================================
echo.

set "DEST=C:\Ovalo_Sistema_v2"
set "REPO_URL=https://github.com/eduardocalaniguevara-del/app_local_recepcion.git"

if not exist "%DEST%" mkdir "%DEST%"

if exist "%~dp003_GIT_app_motor\config.py" (
    echo [1/5] Copiando archivos desde paquete local hacia %DEST%...
    xcopy "%~dp0*" "%DEST%\" /E /I /Y /Q >nul
)

cd /d "%DEST%"
if not exist "%DEST%\.git" (
    echo [1/5] Conectando %DEST% con GitHub en modo SOLO LECTURA...
    git init -b main >nul 2>&1
    git remote add origin "%REPO_URL%" >nul 2>&1
    git fetch origin main >nul 2>&1
    git reset --hard origin/main >nul 2>&1
) else (
    echo [1/5] Actualizando codigo desde GitHub ^(rama main^)...
    git fetch origin main >nul 2>&1
    git reset --hard origin/main >nul 2>&1
)

if not exist "%DEST%\01_PRIV_borradores" mkdir "%DEST%\01_PRIV_borradores"
if not exist "%DEST%\02_DB_sqlite_local" mkdir "%DEST%\02_DB_sqlite_local"

echo [2/5] Configurando .env local para LAPTOP TERCIARIA / PRUEBA ^(NIVEL_3_TERCIARIA^)...
(
echo ROL_NODO=NIVEL_3_TERCIARIA
echo EQUIPO_ID=%COMPUTERNAME%
echo PUERTO_WEB=8080
echo NOMBRE_HOTEL=Hotel El Óvalo Mariátegui
echo CORREO_RECEPCION_HOTEL=hostal.ovalo@gmail.com
echo ACTIVAR_MOTOR_24X7=false
echo TIENE_QR_WSP_FISICO=false
) > "%DEST%\.env"

echo [3/5] Inicializando base SQLite local y descargando datos desde Supabase...
python "%DEST%\03_GIT_app_motor\db.py"
python "%DEST%\03_GIT_app_motor\sync_nube.py" --test

echo [4/5] Creando icono en el Escritorio y encendiendo Mostrador (:8080)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 600 };" ^
  "$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $pyw) { $pyw = 'python.exe' };" ^
  "$chromeIcon = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0'; if (-not (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe')) { $chromeIcon = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe,0' };" ^
  "$WshShell = New-Object -ComObject WScript.Shell;" ^
  "$d = [Environment]::GetFolderPath('Desktop'); if ($d -and (Test-Path $d)) { $sc = $WshShell.CreateShortcut((Join-Path $d 'Hostal Ovalo - Sistema v2.lnk')); $sc.TargetPath = $pyw; $sc.Arguments = '\"C:\Ovalo_Sistema_v2\lanzador_escritorio.py\"'; $sc.WorkingDirectory = 'C:\Ovalo_Sistema_v2'; $sc.IconLocation = $chromeIcon; $sc.Save() };" ^
  "Start-Process -FilePath $pyw -ArgumentList '\"C:\Ovalo_Sistema_v2\lanzador_escritorio.py\"' -WorkingDirectory 'C:\Ovalo_Sistema_v2'"

echo.
echo ============================================================================
echo   INSTALACION COMPLETADA CON EXITO EN C:\Ovalo_Sistema_v2 (MODO TERCIARIA)
echo   - La ventana del Sistema v2 se abrira automaticamente en :8080
echo ============================================================================
pause
