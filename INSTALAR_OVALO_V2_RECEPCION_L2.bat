@echo off
chcp 65001 >nul
title Instalador 1-Clic — Hostal Ovalo Sistema v2 (LAPTOP 2 RECEPCION 24/7)
echo ============================================================================
echo   INSTALADOR AUTOMATICO — C:\Ovalo_Sistema_v2 (MODO: NIVEL_2_MADRE_HOTEL)
echo   - Configura las 5 carpetas (01_PRIV a 05_GIT) en Solo Lectura Git
echo   - Rescata la base SQLite local y sesion sin perder datos ni pedir QR nuevo
echo   - Configura hostal.ovalo@gmail.com, WhatsApp Recepcion y Vigilante 24/7
echo   - Crea el icono de Google Chrome en el Escritorio de Recepcion (:8080)
echo ============================================================================
echo.

set "DEST=C:\Ovalo_Sistema_v2"
set "REPO_URL=https://github.com/eduardocalaniguevara-del/app_local_recepcion.git"

REM 1. Si se ejecuta desde el ZIP descargado o si Git esta disponible, instalar en C:\Ovalo_Sistema_v2
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
    git fetch origin sistema-v2 >nul 2>&1
    git reset --hard origin/main >nul 2>&1
) else (
    echo [1/5] Actualizando codigo desde GitHub ^(rama main^)...
    git fetch origin main >nul 2>&1
    git fetch origin sistema-v2 >nul 2>&1
    git reset --hard origin/main >nul 2>&1
)

REM 2. Crear carpetas locales 01_PRIV_borradores y 02_DB_sqlite_local
if not exist "%DEST%\01_PRIV_borradores" mkdir "%DEST%\01_PRIV_borradores"
if not exist "%DEST%\02_DB_sqlite_local" mkdir "%DEST%\02_DB_sqlite_local"

REM 3. Rescatar base de datos SQLite actual de Recepcion si aún no existe en 02_DB_sqlite_local
if not exist "%DEST%\02_DB_sqlite_local\ovalo.db" (
    if exist "C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db" (
        echo [2/5] Migrando base de datos local de Recepcion hacia 02_DB_sqlite_local\ovalo.db...
        copy /Y "C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db" "%DEST%\02_DB_sqlite_local\ovalo.db" >nul
    )
)
if exist "C:\Recepcion_Ovalo_Local\data\credentials.json" (
    copy /Y "C:\Recepcion_Ovalo_Local\data\credentials.json" "%DEST%\02_DB_sqlite_local\credentials.json" >nul
)

REM 4. Escribir el archivo .env oficial de LAPTOP 2 RECEPCION (NIVEL_2_MADRE_HOTEL)
echo [3/5] Configurando .env local para LAPTOP 2 RECEPCION ^(NIVEL_2_MADRE_HOTEL^)...
(
echo ROL_NODO=NIVEL_2_MADRE_HOTEL
echo EQUIPO_ID=LAPTOP_2_RECEPCION
echo PUERTO_WEB=8080
echo NOMBRE_HOTEL=Hotel El Óvalo Mariátegui
echo CORREO_RECEPCION_HOTEL=hostal.ovalo@gmail.com
echo ACTIVAR_MOTOR_24X7=true
echo TIENE_QR_WSP_FISICO=true
) > "%DEST%\.env"

REM 5. Inicializar esquema UNIQUE y triggers de Supabase en 02_DB_sqlite_local\ovalo.db
echo [4/5] Verificando candados UNIQUE en SQLite y conexion con Supabase...
python "%DEST%\03_GIT_app_motor\db.py"
python "%DEST%\03_GIT_app_motor\sync_nube.py" --test

REM 6. Liberar puerto 8080 de la carpeta antigua, crear icono de Google Chrome y encender Sistema v2
echo [5/5] Creando icono de Google Chrome en el Escritorio y encendiendo Mostrador (:8080) + Vigilante 24/7...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 600 };" ^
  "$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $pyw) { $pyw = 'python.exe' };" ^
  "$chromeIcon = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0'; if (-not (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe')) { $chromeIcon = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe,0' };" ^
  "$WshShell = New-Object -ComObject WScript.Shell;" ^
  "foreach ($d in @([Environment]::GetFolderPath('Desktop'), 'C:\Users\ovalo mariategui\Desktop', 'C:\Users\ovalo mariategui\OneDrive\Desktop')) { if ($d -and (Test-Path $d)) { $sc = $WshShell.CreateShortcut((Join-Path $d 'Hostal Ovalo - Sistema v2.lnk')); $sc.TargetPath = $pyw; $sc.Arguments = '\"C:\Ovalo_Sistema_v2\lanzador_escritorio.py\"'; $sc.WorkingDirectory = 'C:\Ovalo_Sistema_v2'; $sc.IconLocation = $chromeIcon; $sc.Save() } };" ^
  "Start-Process -FilePath $pyw -ArgumentList '\"C:\Ovalo_Sistema_v2\lanzador_escritorio.py\"' -WorkingDirectory 'C:\Ovalo_Sistema_v2';" ^
  "Start-Process -FilePath $pyw -ArgumentList '\"C:\Ovalo_Sistema_v2\05_GIT_vigilante_24x7\motor_24x7.py\"' -WorkingDirectory 'C:\Ovalo_Sistema_v2'"

echo.
echo ============================================================================
echo   INSTALACION COMPLETADA CON EXITO EN C:\Ovalo_Sistema_v2
echo   - La ventana de Google Chrome se abrira automaticamente en :8080
echo   - El Vigilante 24/7 ya quedo activo en segundo plano
echo ============================================================================
pause
