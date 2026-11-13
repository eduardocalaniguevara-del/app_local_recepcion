@echo off
chcp 65001 >nul
title Visor SQLite + Arnes2 Recepcion (Laptop 2)
set "BASE=%~dp0"
echo [1/3] Instalando / verificando Arnes2 Recepcion...
python "%BASE%instalar_arnes_laptop2.py" --verificar
echo [2/3] Iniciando visor local si existe...
if exist "%BASE%whatsapp_mcp_recepcion\visor_web.js" (
  start "" /min node "%BASE%whatsapp_mcp_recepcion\visor_web.js"
  timeout /t 3 >nul
)
echo [3/3] Abriendo en Microsoft Edge...
start msedge "http://127.0.0.1:8788/"
echo Hecho. Salud del nodo publicada en Supabase (estado_sistema: arnes_salud_LAPTOP_2_RECEPCION).
pause
