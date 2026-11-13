@echo off
rem Ejecuta un script Python de esta carpeta con el Python correcto (ventana visible y espera a que termine).
rem Uso: _py.bat <script.py> [argumentos]
chcp 65001 > nul
cd /d "%~dp0"
set "PY_BIN="
if not defined PY_BIN if exist "%LocalAppData%\Python\bin\python.exe" set "PY_BIN=%LocalAppData%\Python\bin\python.exe"
if not defined PY_BIN if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" set "PY_BIN=%LocalAppData%\Python\pythoncore-3.14-64\python.exe"
if not defined PY_BIN if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python314\python.exe"
if not defined PY_BIN if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PY_BIN if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY_BIN if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_BIN=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PY_BIN if exist "C:\Python312\python.exe" set "PY_BIN=C:\Python312\python.exe"
if not defined PY_BIN if exist "C:\Python311\python.exe" set "PY_BIN=C:\Python311\python.exe"
if not defined PY_BIN (
    for /f "tokens=*" %%I in ('where python 2^>nul') do (
        echo %%I | findstr /I "WindowsApps" >nul || (if not defined PY_BIN set "PY_BIN=%%I")
    )
)
if not defined PY_BIN (
    for /f "tokens=*" %%I in ('where py 2^>nul') do (
        echo %%I | findstr /I "WindowsApps" >nul || (if not defined PY_BIN set "PY_BIN=%%I")
    )
)
if not defined PY_BIN (
    for /f "usebackq tokens=*" %%I in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*.exe' -and $_.CommandLine -like '*lanzador_escritorio*' } | Select-Object -First 1).ExecutablePath"`) do set "PY_BIN=%%I"
)
if defined PY_BIN set "PY_BIN=%PY_BIN:pythonw.exe=python.exe%"
if not defined PY_BIN (
    echo [ERROR] No se encontro Python en esta computadora.
    exit /b 1
)
"%PY_BIN%" %*
exit /b %ERRORLEVEL%
