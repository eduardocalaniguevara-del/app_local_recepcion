@echo off
chcp 65001 >nul
echo Cerrando servidor de prueba (Ovalo_Mariategui_PRUEBA - puerto 8082)...
powershell -NoProfile -Command "$p = (Get-NetTCPConnection -LocalPort 8082 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique; foreach ($i in $p) { if ($i -gt 4) { Stop-Process -Id $i -Force -ErrorAction SilentlyContinue } }"
echo Listo. La operativa (puerto 8080) sigue funcionando normal.
timeout /t 3 >nul
