@echo off
chcp 65001 > nul
cd /d "%~dp0"
set GIT_EXE=C:\Hostal_Sistema\tools\mingit\cmd\git.exe
if not exist "%GIT_EXE%" set GIT_EXE=git

echo ========================================================
echo   PUBLICANDO ACTUALIZACION A LOS 3 HOTELES (GITHUB)
echo ========================================================
"%GIT_EXE%" add -A
"%GIT_EXE%" commit -m "Actualizacion automatica del sistema"
"%GIT_EXE%" push -u origin main
echo.
echo [OK] Cambios enviados a GitHub. Las laptops de recepcion
echo      descargaran la actualizacion automaticamente en ~45s.
pause
