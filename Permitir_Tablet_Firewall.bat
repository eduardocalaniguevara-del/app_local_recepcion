@echo off
chcp 65001 >nul
title Habilitar Conexion Wi-Fi para Tablet Samsung S8+ (Puerto 8080 / 8081)
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Solicitando permiso de Administrador para abrir el puerto 8080 en el Firewall de Windows...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
netsh advfirewall firewall delete rule name="Recepcion Local Tablet S8+ (8080-8081)" >nul 2>&1
netsh advfirewall firewall add rule name="Recepcion Local Tablet S8+ (8080-8081)" dir=in action=allow protocol=TCP localport=8080,8081 profile=any
echo.
echo [OK] Firewall configurado correctamente para la Tablet Samsung Galaxy Tab S8+.
timeout /t 3 >nul
exit /b
