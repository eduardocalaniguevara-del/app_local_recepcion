# 🤖 PLAN DE IMPLEMENTACIÓN EN LAPTOP 2: ASISTENTE OPERATIVO DE RECEPCIÓN ("COPILOTO ÓVALO") + CONTROL ARNÉS² (`GR-09`)

**Fecha:** 2026-11-14  
**Entorno inicial de construcción y prueba:** `C:\ovalo_mariategui_prueba` (`http://127.0.0.1:8082`)  
**Promoción posterior a operativa:** `C:\Recepcion_Ovalo_Local` (`http://127.0.0.1:8080`) vía `Promover_a_Operativa.bat`  

---

## 1. Objetivo General
Implementar en `Ovalo_Mariategui_PRUEBA` (`puerto 8082`) un asistente integrado en el `PanelDisponibilidad.html` diseñado específicamente para los recepcionistas del **Hotel El Óvalo Mariátegui**, bajo el control del sistema de seguridad **Arnés²** y con extracto de movimientos en tiempo real para el dueño.

---

## 2. Reglas de Negocio Definidas por el Dueño (Obligatorias)

### ✅ A. Lo que SÍ hace el Asistente ("Copiloto Óvalo"):
1. **Consultor Operativo Instantáneo (4 Frentes):**
   - **Cuentas, Deudas y Yapes del Día:** Desglose de deuda por habitación (`estancias`, `pagos`, `ventas_tienda`, `servicios_detalle`) y verificación de Yapes recibidos hoy (`yape_conciliacion_hostal` + `cache_correo_maelen_bcp.json`).
   - **WhatsApp de Recepción y Comprobantes Pendientes:** Responde qué facturas o boletas pedidas por WhatsApp (`wsp_recepcion_mensajes`, `wsp_recepcion_documentos`) o marcadas en estancias (`solicita_comp = 'Sí'`, `estado_comp != 'Emitido'`) están pendientes de emitir o enviar (`wsp_recepcion_cola_envio`).
   - **Auditor de Vitrina y Faltantes (`vitrina_local` `h1..h36`):** Responde qué productos faltan hoy, **por qué faltan** (cruzando stock de conteos anteriores vs. `ventas_tienda` y `almacen_movimientos`) y **desde qué turno/fecha y con qué responsable empezaron a faltar** (usando los 36 turnos históricos `vitrina_h1_header..vitrina_h36_header`).
   - **Cuadre Diario del Turno Actual:** Permite consultar únicamente el cuadre del día/turno actual para la entrega de turno.

2. **Doble Modalidad Práctica para Resolver Dudas (Cero texto largo que complique):**
   - **Modalidad 1 — Guía Visual con Fotos Reales (`📸 Muéstrame con fotos`):**
     - Como el sistema tiene acceso a la aplicación en `http://127.0.0.1:8082`, el asistente muestra **tarjetas con capturas reales de la pantalla** (`/static/img/guia_asistente/*.png`) con flechas y números grandes (`①`, `②`, `③`) señalando dónde hacer clic (ej. lápiz `✏️` para cambio de habitación, 2 casillas de Yape, venta/cambio en tienda, conteo de vitrina, comprobantes).
   - **Modalidad 2 — Ejecución Directa por el Asistente (`⚡ Hazlo tú por mí`) + Extracto para el Dueño:**
     - El recepcionista puede pedirle al asistente que haga el movimiento (ej. *"Cambia la 302 a la 201"*, *"Anótale 1 Agua sin gas a la 204"*, *"Registra pago Yape de S/ 40 en la 305"*).
     - El asistente pide 1 clic de confirmación dentro del mismo chat (`[✅ Confirmar que el asistente lo haga]`), ejecuta la función oficial de `domain_logic.py` y **registra obligatoriamente el movimiento en el Extracto del Dueño (`asistente_movimientos_log`)**.

### 🚫 B. Cortafuegos Estricto Arnés² (`GR-09: Confidencialidad Financiera, Gastos y Credenciales`):
- **PROHIBIDO dar claves o accesos:** Jamás revela ni lee claves SOL de SUNAT, contraseñas en general, tokens, código de Arnés² ni correos que no sean de la empresa.
- **PROHIBIDO dar ingresos semanales, mensuales o totales:** Nunca informa cuánto generó el hotel en la semana, en el mes ni acumulados totales (para evitar envidia o filtraciones). **Solo puede dar el monto diario del día/turno actual.**
- **PROHIBIDO hablar o analizar Gastos (`gastos` / `compras_tienda_lotes`):** La tabla `gastos` está **100% vetada** para el asistente porque son gastos personales/internos de la empresa. Jamás lista ni analiza en qué se gasta ni cuánto se gasta.
- **PROHIBIDO usar "Acción Guiada" que abra modales solos:** Descartado abrir formularios emergentes automáticamente para que los recepcionistas no se asusten.
- **Registro de intentos bloqueados:** Si un recepcionista pregunta por ingresos del mes, gastos o claves, el asistente lo rechaza cortésmente y guarda una alerta `INTENTO_BLOQUEADO_GR09` en el Extracto del Dueño.

---

## 3. Cambios Técnicos a Ejecutar en Laptop 2 (`C:\ovalo_mariategui_prueba`)

### Paso 1: Tabla `asistente_movimientos_log` y Sincronización Supabase (`database.py` + `supabase_sync.py`)
Agregar en `database.py` (`init_db`) y en `supabase_sync.py` (`TABLAS_GRUPO2_PK` + `init_supabase_local_triggers`):
```sql
CREATE TABLE IF NOT EXISTS asistente_movimientos_log (
    log_uuid          TEXT PRIMARY KEY,
    fecha_dmy         TEXT NOT NULL DEFAULT '',
    hora              TEXT NOT NULL DEFAULT '',
    ts_iso            TEXT NOT NULL DEFAULT '',
    recepcionista     TEXT NOT NULL DEFAULT '',
    tipo_interaccion  TEXT NOT NULL DEFAULT '', -- 'MOVIMIENTO_EJECUTADO' | 'CONSULTA' | 'INTENTO_BLOQUEADO_GR09'
    pedido_original   TEXT NOT NULL DEFAULT '', -- Texto exacto escrito por el recepcionista
    accion_ejecutada  TEXT NOT NULL DEFAULT '', -- 'CAMBIO_HABITACION', 'VENTA_TIENDA', 'PAGO_POSTERIOR', 'CONTINUAR_ESTANCIA'
    hab               TEXT NOT NULL DEFAULT '',
    huesped           TEXT NOT NULL DEFAULT '',
    monto             REAL NOT NULL DEFAULT 0,
    estado_anterior   TEXT NOT NULL DEFAULT '{}', -- JSON antes del movimiento
    estado_nuevo      TEXT NOT NULL DEFAULT '{}', -- JSON después del movimiento
    resumen_dueno     TEXT NOT NULL DEFAULT '', -- Resumen claro para el extracto del dueño
    equipo            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_asist_log_fecha ON asistente_movimientos_log (fecha_dmy, tipo_interaccion);
```

### Paso 2: Crear `asistente_recepcion.py` en `C:\ovalo_mariategui_prueba\`
1. **`verificar_guardrail_gr09(texto)`:** Filtra y bloquea consultas sobre claves/SUNAT/correos privados, ingresos semanales/mensuales/totales y gastos de la empresa (`gastos`). Registra cualquier intento bloqueado en `asistente_movimientos_log`.
2. **Consultas Operativas:**
   - `_consultar_deuda_o_cuenta_hab(hab)`
   - `_consultar_yapes_recientes()`
   - `_consultar_wsp_comprobantes_pendientes()` (cruza `wsp_recepcion_mensajes`, `wsp_recepcion_documentos`, `wsp_recepcion_cola_envio` y `estancias`)
   - `_consultar_faltantes_vitrina(producto)` (cruza `vitrina_local` `h1..h36`, `vitrina_h1_header..vitrina_h36_header`, `ventas_tienda` y `almacen_movimientos` para explicar **qué falta, desde qué turno/responsable empezó a faltar y por qué**).
3. **Guía Visual con Fotos (`_obtener_guia_visual_fotos`):** Sirve tarjetas con capturas reales anotadas (`/static/img/guia_asistente/*.png`) tomadas con Microsoft Edge en `http://127.0.0.1:8082`.
4. **Ejecutor Directo Auditado (`preparar_movimiento_asistente_local` + `confirmar_movimiento_asistente_local`):** Ejecuta cambios de habitación, ventas de tienda, pagos posteriores o continuaciones tras confirmación en el chat, guardando el Antes/Después en `asistente_movimientos_log`.
5. **Extracto para el Dueño (`obtener_extracto_dueno_asistente_local`):** Devuelve la bitácora completa de movimientos generados por los recepcionistas a través del asistente y los intentos bloqueados por `GR-09`.

### Paso 3: Regla `GR-09` en Arnés² (`arnes_recepcion/.agent/harness/guardrails/safety_rules.json`)
Registrar formalmente la regla `GR-09` en `safety_rules.json`.

### Paso 4: Interfaz Flotante en `static/PanelDisponibilidad.html` + Rutas RPC en `domain_logic.py` / `main.py`
- Botón flotante `🤖 Copiloto Óvalo` con selector de recepcionista en turno, 5 botones rápidos (`📄 WSP Comprobantes`, `🥤 Faltantes Vitrina`, `📱 Yapes de Hoy`, `📸 Guía con Fotos`, `⚡ Haz un movimiento por mí`), visor de fotos guía y botón de **Extracto del Dueño**.
- Conectar métodos RPC: `consultarAsistenteRecepcion`, `confirmarMovimientoAsistente` y `obtenerExtractoDuenoAsistente`.

---

## 4. Cómo Continuar en Laptop 2 (Comando Listo para Copiar)
Al abrir Antigravity en la **Laptop 2 (Recepción)**, pega directamente:
> *"Lee `C:\Users\ovalo mariategui\Downloads\leemeantrigravityrecepcion\PLAN_ASISTENTE_RECEPCION.md` y `leemeantrigravityrecepcion.md` y ejecuta la implementación del Asistente de Recepción en `C:\ovalo_mariategui_prueba` (puerto 8082)."*
