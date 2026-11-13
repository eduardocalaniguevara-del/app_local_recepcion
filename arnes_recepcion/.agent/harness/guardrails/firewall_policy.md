# Política de Cortafuegos y Aislamiento Operativo — Harness²

## 1. Política del Cortafuegos de Dos Intentos (Loop-Breaker)
- **Principio:** Ningún agente del sistema puede ejecutar más de dos intentos consecutivos que resulten en el mismo error o en la misma falla de validación.
- **Acción Obligatoria:** Al segundo fallo consecutivo, el agente debe detener de inmediato la ejecución y responder con un informe resumido de exactamente 3 líneas:
  1. *Qué se intentó y qué falló exactamente (mensaje de error o código).*
  2. *Por qué falló (causa raíz observada o hipótesis confirmada).*
  3. *Qué se propone para avanzar (cambio de modelo a Sonnet/Opus, solicitud de captura de pantalla o aclaración de datos).*

## 2. Aislamiento Sandbox y Promoción Controlada
- **Ambiente de Pruebas Obligatorio:**
  - Directorio: `C:\ovalo_mariategui_prueba`
  - Puerto de red: `8082` (sin sincronización con Supabase ni Sheets)
  - Toda modificación de código a la WebApp de Recepción debe implementarse y validarse primero en este sandbox.
- **Protocolo de Promoción a Producción:**
  - Única herramienta autorizada: `promover_a_operativa.py` (`Promover_a_Operativa.bat`).
  - Prohibido utilizar `git add -A` o copia directa indiscriminada hacia la carpeta operativa (`app_local_recepcion`).
  - **Archivos en Observación Estricta:** Queda prohibido sobreescribir de forma desatendida los módulos en calibración:
    - `auditor_audio_noche.py`
    - `pases_logic.py`
    - Archivos de bases de datos locales (`*.db`, `*.db-wal`, `*.db-shm`).

## 3. Bóveda de Credenciales y Política de Revelación

## 4. Política de Navegadores
- **Navegador Primario:** Microsoft Edge (`edge-browser` MCP y Playwright con canal `msedge`).
- **Navegador Secundario:** Google Chrome (`chrome-devtools` MCP), que solo puede activarse ante requerimiento expreso del usuario.


## GR-08 — Candado de Código de Autorización (Laptop 2)
Cambios a manifest/guardrails/probes/skills del Arnés² o promoción Prueba (8082) → Operativa (8080) exigen código de administrador (hash SHA-256 o `ARNES_AUTH_HASH`). Las operaciones diarias de la WebApp (huéspedes, pagos, estancias, tienda) son LIBRES.
