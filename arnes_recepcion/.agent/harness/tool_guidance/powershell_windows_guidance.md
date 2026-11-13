# Guía de Ejecución: PowerShell & Entorno Windows

## Convenciones de Línea de Comandos
1. **Rutas con Espacios:** El espacio de trabajo contiene espacios en su ruta (`Sistema de agentes IA`). Toda ruta pasada a PowerShell o Node.js debe estar debidamente entrecomillada (`"path\with spaces"`).
2. **Uso de Herramientas de Temporización:** No utilizar comandos de suspensión del sistema (`Start-Sleep`, `sleep`) como sustitutos de temporizadores reactivos o herramientas de schedule.
3. **Manejo de Tareas Asíncronas:**
   - Comandos sincrónicos estándar: `WaitMsBeforeAsync` entre 5000 y 10000 ms.
   - Daemons o procesos de fondo: `IsDaemon: true` con `WaitMsBeforeAsync: 500`.
4. **Verificación de Procesos y Puertos:**
   - Para verificar puertos en escucha: `Get-NetTCPConnection -LocalPort <puerto> -State Listen`.
   - Para detener un proceso colgado: `Stop-Process -Id <PID> -Force`.
