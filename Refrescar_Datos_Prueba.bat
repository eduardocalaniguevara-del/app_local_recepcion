@echo off
echo Trayendo datos FRESCOS de la operativa a la PRUEBA (la base de prueba actual se guarda como respaldo)...
call "%~dp0_py.bat" crear_entorno_prueba.py --solo-datos %*
echo.
pause
