# 📋 GUÍA MAESTRA PARA ANTIGRAVITY EN LAPTOP 2 (RECEPCIÓN): `leemeantrigravityrecepcion`
**Fecha:** 2026-11-11
**Equipo destino:** Laptop 2 de Recepción (`C:\Users\ovalo mariategui`)
**WebApp Local en Laptop 2:** `C:\Recepcion_Ovalo_Local`
**Base de Datos Unificada del Hostal (Grupo 2):** `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db`

---

## 🎯 1. OBJETIVO PRINCIPAL EN ESTA LAPTOP 2 (RECEPCIÓN)

En la **Laptop 1 (Administración)** ya dejamos construidas y funcionando las **4 Fases** de la arquitectura **SQLite + Supabase (`hostal-ovalo`)**:
1. **`ovalo_sistema.db` (Grupo 2 Compartido):** Contiene las **35 tablas del Hostal** (Recepción, Estancias, Pagos, Tienda, Almacén, Gastos, Horarios, Pases, SUNAT Ventas/Líneas/Calzado, SIRE Compras, Conciliación Yape y **las 4 tablas de WhatsApp de Recepción**).
2. **Sincronización en Tiempo Real con Supabase (`hostal-ovalo`):**
   - **Project ID:** `fmlfqupwscymosvwnnzx`
   - **URL:** `https://fmlfqupwscymosvwnnzx.supabase.co`
   - **Anon Key:** `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0.pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g`
   - **Header RLS (`x-hostal-sync-token`):** `ovalo_master_sync_2026_k9x`
   - **Sin necesidad de correo:** Laptop 2 se autentica automáticamente con `SUPABASE_URL + SUPABASE_ANON_KEY + x-hostal-sync-token` desde `config.py` y `supabase_sync.py`.

---

## 💬 2. WHATSAPP DE RECEPCIÓN INTEGRADO EN `ovalo_sistema.db` (CRUCE AUTOMÁTICO CON CELULAR DE HUÉSPEDES)

El usuario decidió que el **WhatsApp de Recepción** se guarde **dentro de la misma base `ovalo_sistema.db`** (en las 4 tablas ya creadas y conectadas por Triggers a Supabase):
- `wsp_recepcion_chats`
- `wsp_recepcion_mensajes`
- `wsp_recepcion_documentos` (OCR de vouchers Yape/Plin y facturas enviadas por clientes)
- `wsp_recepcion_cola_envio`

### 🔗 ¿Por qué va dentro de `ovalo_sistema.db`?
Porque en nuestra WebApp Local (`estancias` y `huespedes_padron`) tenemos la casilla **`celular`** de cada huésped:
- Al estar `wsp_recepcion_mensajes` y `wsp_recepcion_chats` dentro de `ovalo_sistema.db`, los últimos 9 dígitos del número de WhatsApp (`SUBSTR(REPLACE(jid, '@s.whatsapp.net', ''), -9)`) se cruzan automáticamente con `huespedes_padron.celular` y `estancias.celular`.
- Así, cuando un cliente escribe o manda su voucher al WhatsApp de Recepción en Laptop 2:
  1. Se guarda en `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db`.
  2. El Trigger SQLite (`trg_sb_ins_wsp_recepcion_mensajes`) lo sube automáticamente a **Supabase (`hostal-ovalo`)** en `< 150 ms`.
  3. La **Laptop 1** lo descarga automáticamente desde Supabase (`supabase_change_log`) y en el visor de Laptop 1 (`http://127.0.0.1:8788`, pestaña `💬 WSP Recepción`) aparece el mensaje junto con el **Nombre del Huésped, DNI y Habitación** cruzados por su número de celular.

---

## 🛠️ 3. PASOS EXACTOS QUE DEBE EJECUTAR EL AGENTE ANTIGRAVITY EN LAPTOP 2

### Paso A: Verificar que la WebApp Local (`C:\Recepcion_Ovalo_Local`) esté sincronizando con Supabase
1. Consultar `http://127.0.0.1:8080/api/supabase/status`.
2. Si aún no responde o le falta actualizar, copiar los archivos de `webapp_actualizada\` (`config.py`, `supabase_sync.py`, `main.py`, `sync_engine.py`) hacia `C:\Recepcion_Ovalo_Local\` y reiniciar el servidor local (`reiniciar_app.ps1`).
3. Confirmar que `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db` exista y que `/api/supabase/status` devuelva `"status": "OK"`.

### Paso B: Activar el Servicio de WhatsApp de Recepción (`whatsapp_mcp_recepcion`) en Laptop 2
1. En esta carpeta encontrarás `whatsapp_mcp_recepcion\` con los scripts (`package.json`, `db.js`, `wsp_daemon.js`, `ocr_extractor.py`, `mcp_server.js`).
2. Copiar `whatsapp_mcp_recepcion` a `C:\Recepcion_Ovalo_Local\whatsapp_mcp_recepcion\`.
3. Asegurar que `db.js` en Laptop 2 apunte a `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db` y use las tablas `wsp_recepcion_chats`, `wsp_recepcion_mensajes`, `wsp_recepcion_documentos` y `wsp_recepcion_cola_envio`.
4. Ejecutar `npm install` dentro de `C:\Recepcion_Ovalo_Local\whatsapp_mcp_recepcion\`, iniciar `node wsp_daemon.js` y mostrarle al usuario el código QR (`qr_actual.png`) para que lo escanee con el celular de Recepción.
5. Una vez conectado el WhatsApp de Recepción, todos los chats, mensajes y vouchers de clientes fluirán automáticamente:
   **WhatsApp Recepción (Laptop 2) → `ovalo_sistema.db` (Laptop 2) → Supabase (`hostal-ovalo`) → `ovalo_sistema.db` (Laptop 1) → Visor de Laptop 1 (`8788`)**.

---

## 📏 4. REGLAS DE ESTILO DEL USUARIO (OBLIGATORIAS EN LAPTOP 2)
- **Navegador por defecto:** Usar siempre **Microsoft Edge** (`edge-browser` MCP / `msedge`).
- **Orden obligatorio de cada respuesta:**
  1. `> **🎓 En esta respuesta puedes aplicar:** [Herramienta] — [Explicación sencilla] → *Ejemplo: "..."*` (SIEMPRE EN LA PRIMERA LÍNEA).
  2. Respuesta directa al grano (Modo Turbo).
  3. `🏗️ Arquitectura In-House (Cómo funciona por dentro):` (3-4 líneas con analogías prácticas de negocio/construcción).
  4. `💡 Extrapolación` (máximo 3 ideas al final, sin implementar hasta aprobación).


---

## 🩹 5. HISTORIAL DE CORRECCIONES RECIENTES (2026-11-11 / 2026-11-12)
- **Commit `e7d1d39` (2026-11-12 — Autorreparación SQLite + Sincronización Supabase + Cambio de Habitación con Lápiz `✏️`):**
  1. **Autorreparación automática de `ovalo_sistema.db` (`database.py`, `main.py`, `supabase_sync.py`):** Se añadió `repair_db_if_malformed()` que detecta cualquier corrupción B-tree en la bandeja temporal `_supabase_outbox` (`database disk image is malformed`) y reconstruye la base conservando el 100% de las 33 tablas operativas, respaldando el archivo previo en `ovalo_sistema.corrupt_bak.db`. Además se configuró `PRAGMA busy_timeout = 20000` en Python y `15000` en Node (`whatsapp_mcp/db.js`).
  2. **Sincronización Laptop 1 ↔ Supabase ↔ Google Sheets (`supabase_sync.py`, `sync_engine.py`, `auto_updater.py`):**
     - Se corrigieron las llaves primarias de `wsp_recepcion_mensajes` (`id` en lugar de `id_mensaje`) y `sunat_ventas` (`nro_cp` en lugar de `id`), más resolución dinámica `_resolve_pk_cols()` y recreación limpia de triggers (`DROP TRIGGER IF EXISTS`), eliminando el error `no such column: NEW.id_mensaje` al recibir mensajes del WhatsApp de Recepción.
     - Se preservan `_sync_lock` y `_sb_lock` frente a recargas en caliente y `pull_from_sheets` ahora encola en `_supabase_outbox` las estancias nuevas o modificadas traídas de Google Sheets para mantener Supabase (`hostal-ovalo`) 100% al día.
  3. **Cambio de Habitación con el Lápiz de Editar `✏️` (ej. pasar huésped de `302` a `201` en `domain_logic.py`, `sync_engine.py` y `PanelDisponibilidad.html`):**
     - `modificar_estancia_local` ahora busca la estancia usando `estanciaUuid` y `habOriginal` (la habitación de origen, ej. `302`) en vez de filtrar por la habitación destino (`201`), y normaliza ceros iniciales en horas (`7:38:52` vs `07:38:52`).
     - Valida colisiones reales de fechas en la habitación destino (permitiendo entrar hoy si el huésped anterior hace Check-Out hoy) y traslada automáticamente `hab` en `pagos`, `ventas_tienda`, `servicios_detalle`, `control_pases`, cola pendiente `sync_queue` y hojas `Registro_Pagos` / `2.Registro_Ventas_Tienda`.
- **Commit `05f1036` / `6571792` (`app_local_recepcion/domain_logic.py`):**
  1. **Deuda real visible en habitaciones con RUC/Empresa (ej. Habs. 406, 407, 205, 206, 307):** Se eliminó el recorte que ocultaba el saldo de alojamiento (`saldo = min(saldo_real_est, adic_pend)`) cuando el campo `empresa` tenía una razón social (como `REGIST. NAC. DE IDENTIF. Y ESTADO CIVIL`). Ahora el Panel muestra siempre el `saldo` completo (`S/ 132.50` de los 3 días en 406 y 407, `deudaAnt: S/ 92.50` + `tarifa: S/ 40.00`).
  2. **Fecha del Panel siempre en `Hoy` al abrir/recargar:** `obtener_datos_panel_local(fecha_override=None)` ahora carga siempre la fecha actual del día (`hoy_dmy()`) en lugar de quedarse pegado en `_ULTIMA_FECHA_PANEL_ISO` del día anterior cuando la PC queda encendida de un día para otro.
- **Commit `63af335` y `69e353e` (`app_local_recepcion/domain_logic.py`):**
  1. **Corrección al abrir formularios (`PagosPosteriores`, `CheckInRapido`, `VentaTienda`, `GastosRegistro`):** En `_inyectar_rutas_en_servidor_activo()` se corrigió la referencia al módulo `main` (evitando que apunte a `__main__` cuando se lanza desde `lanzador_escritorio.py`), eliminando el error `module '__main__' has no attribute '_obtener_vista_formulario_local'`.
  2. **Total Cuenta y filtro en Pagos Posteriores (`obtener_datos_pagos_posteriores_local`):** Ahora `totalCuenta` suma también `deuda_ant` (`subtotal_aloj + snacks + servicios + comp_extra + deuda_ant`) y filtra estancias con estado `Finalizado - Continúa` para que no aparezcan duplicadas en el selector de cobros.
  3. **Corrección de Recarga en Caliente (`_nuevo_rpc_handler` con `**_kwargs`), Persistencia WMI y Reconciliación Automática entre Laptops (Commits `9117216`..`e470618`):**
     - **Causa raíz de por qué F5 no actualizaba en Laptop 1:** Al agregar `_retried: bool = False` en `main.py` (`api_rpc_handler`), FastAPI lo registraba como parámetro en `r.dependant`; cuando `domain_logic.py` se recargaba en memoria 20s después, `_inyectar_rutas_en_servidor_activo()` sustituía la ruta con `_nuevo_rpc_handler(method_name, body)` sin aceptar `_retried`, causando `TypeError` en `/api/rpc/obtenerDatosPanelDisponibilidad` al presionar `F5`. Además, cuando `reiniciar_app.ps1` era invocado desde una consola hija, el Job Object de Windows cerraba `pythonw.exe` al terminar la sesión padre.
     - **Solución definitiva:** Se separó `_ejecutar_rpc_handler` en `main.py`, se añadió `**_kwargs` y limpieza de `query_params` en `_nuevo_rpc_handler` (`domain_logic.py`), se usa `Win32_Process.Create` (WMI) en `reiniciar_app.ps1` para que el proceso viva independiente de cualquier consola, y se incorporó `_reconciliar_ultimos_dias_una_vez()` en `supabase_sync.py` junto con recarga en memoria de `supabase_sync` y `sync_engine` en `auto_updater.py`.

## [2026-11-12] SISTEMA DE PRUEBA aislado: "ovalo_mariategui_prueba" (ambas laptops)
- **Qué es:** copia completa del sistema en `C:\ovalo_mariategui_prueba` (puerto **8082**; la operativa usa 8080). Acceso directo en el Escritorio: **Ovalo_Mariategui_PRUEBA** (ícono naranja "P", banner rojo "SISTEMA DE PRUEBA").
- **Aislamiento (`data/hotel_perfil.json` con `modo_prueba: true`):** sin Supabase, sin Google Sheets (hoja = `SIN_HOJA_MODO_PRUEBA`), sin auto-actualización desde GitHub, sin SUNAT, sin WhatsApp, solo `127.0.0.1` (la tablet no la ve). Probado: un gasto de prueba quedó SOLO en la base de prueba (operativa 839→839, Supabase sin cambios).
- **Datos:** FOTO (backup seguro) de la base operativa. Refrescar: `Refrescar_Datos_Prueba.bat`.
- **Flujo:** editar/probar en la prueba → `Promover_a_Operativa.bat` (muestra plan; con `--aplicar` hace respaldo en `C:\Hostal_Sistema\respaldos_promocion\`, etiqueta git `respaldo-previo-*`, detecta CONFLICTOS 3-vías y publica a GitHub solo los archivos promovidos).
- **Laptop 2:** se crea sola por la orden remota `CREAR_ENTORNO_PRUEBA_01` (`ordenes_hoteles.json` → `Crear_Entorno_Prueba.bat`); deja constancia en Supabase `estado_sistema` clave `entorno_prueba_<EQUIPO_ID>`.
- **GitHub = ÚNICA fuente de verdad:** se desactivó el overlay `_preservar_tablet_andino_local` (copiaba desde `C:\Hostal_Sistema\actualizacion_webapp_local` y pisaba cambios). Solo se reactiva con `data/activar_overlay_local.flag`.
- **Yape:** ya en GitHub (commit `21c0d33`) + migración de la tabla antigua `yape_conciliacion_hostal` (commit `98ceea6`) que impedía arrancar Laptop 1.
- **Pendiente de otro chat (EN OBSERVACIÓN — NO publicar):** `auditor_audio_noche.py`, `pases_logic.py` modificados y archivos de audio/cámara sin versionar en la carpeta operativa de Laptop 1 quedan en observación local sin subir hasta nueva orden.
- **Regla Permanente (`/learn`):** **Toda mejora se edita y prueba primero en `Ovalo_Mariategui_PRUEBA` (`C:\ovalo_mariategui_prueba`, puerto `8082`) y solo después se promueve a la operativa** con `Promover_a_Operativa.bat` (`promover_a_operativa.py`). El servidor de prueba permanece apagado en reposo y se enciende automáticamente al abrir el ícono `Ovalo_Mariategui_PRUEBA` del Escritorio (y puede apagarse con `Cerrar_Prueba.bat`).
- **Commit `bc87875` (Promovido desde `Ovalo_Mariategui_PRUEBA`):**
  1. **Eliminado `IntegrityError: UNIQUE constraint failed: _supabase_outbox...`:** Los triggers `trg_sb_ins_*`, `trg_sb_upd_*` y `trg_sb_del_*` ahora usan `ON CONFLICT(tabla, pk1, pk2) DO UPDATE` en lugar de `INSERT OR REPLACE` (que SQLite anulaba cuando la sentencia externa usaba `ON CONFLICT DO UPDATE`).
  2. **Fin de inundación de `_supabase_outbox` (~9,700 filas cada 25s):** `pull_from_sheets()` activa `_supabase_sync_control.pulling = 1` al reconstruir cachés espejo de Google Sheets y solo encola deltas reales de `estancias`, más limpieza única `_limpiar_inundacion_outbox_una_vez()`.
  3. **Fin de `database is locked` en `supabase_sync.py`:** `push_to_supabase()` y `pull_from_supabase()` separan lectura SQLite (`< 5 ms`), llamadas HTTP por red (`0` conexiones SQLite abiertas) y borrado en lote (`DELETE ... in.(...)`), manteniendo `0 ms` de bloqueo sobre `ovalo_sistema.db`.


---

## 🛡️ 6. ARQUITECTURA ARNÉS² EN LAPTOP 2, NODO 24/7, PUENTE DE PRUEBAS Y APRENDIZAJE COMPARTIDO [2026-11-13]

### A. Arnés² (Harness² v2.0.0) en Laptop 2 (`arnes_recepcion`):
- **Aislamiento Hermético de Bóveda:** En Laptop 2 no existe ninguna referencia a finanzas personales del administrador (expurgado 100% de bases, credenciales y skills del Grupo 1 privado). Solo administra el negocio del Hotel El Óvalo Mariátegui (RUC 20611986484).
- **6 Sondas de Salud Operativa:** `sqlite_probe.js`, `supabase_probe.js`, `sunat_probe.js`, `whatsapp_probe.js`, `webapp_probe.js` y la sonda maestra `operational_health_probe.js`.
- **Telemetría en Vivo:** Publica continuamente el estado de salud operativa en Supabase `estado_sistema` bajo la clave `arnes_salud_LAPTOP_2_RECEPCION`.
- **Visor Web en Laptop 1 (`8788`):** Expone `/api/harness/laptop2-telemetry` y muestra en la pestaña `🛡️ Arnés²` una tarjeta en vivo con el estado del Nodo 24/7 y los últimos eventos registrados.

### B. Motor 24/7 de Automatizaciones Diarias (`scheduler_recepcion_24x7.py`):
- **Servicio Ligero en Segundo Plano:** Corre en Laptop 2 aprovechando que el equipo de Recepción permanece activo 24 horas al día.
- **Sonda Periódica:** Cada 30 minutos ejecuta la sonda maestra de Arnés² y actualiza la constancia de salud en `estado_sistema`.
- **Despachador de Tareas Diarias (`config_automatizaciones_24x7`):**
  1. `facturacion_diaria_sunat`: Verificación y consolidación de comprobantes de pago del Hostal.
  2. `conciliacion_yape_diaria`: Conciliación bancaria nocturna de Yape/Plin contra `Registro_Pagos`.
  3. `envio_wsp_comprobantes`: Vaciado y entrega de la cola `wsp_recepcion_cola_envio` por WhatsApp.
  4. `captura_aprendizaje_recepcionistas`: Colector de eventos y patrones de operación.

### C. Puente Conectado de Pruebas (`8082 ↔ 8082`) y Extracción en Solo Lectura:
- **Canal de Sincronización Aislado (`sandbox_change_log`):** `Ovalo_Mariategui_PRUEBA` (puerto `8082`) en ambas laptops sincroniza sus pruebas a través de la tabla `sandbox_change_log` en Supabase sin tocar jamás las 33 tablas operativas productivas.
- **Extracción Segura en Solo Lectura (`extraer_datos_reales_supabase_readonly`):** La base de prueba puede refrescarse en cualquier momento importando los datos más recientes desde las tablas reales de Supabase mediante consultas `GET` en solo lectura con `pulling = 1`, garantizando 0 riesgo de sobrescritura sobre producción.

### D. Aprendizaje Compartido Bidireccional (`aprendizaje_compartido_hostal`):
- **Colector `analizar_aprendizaje_recepcionistas()`:** Monitorea de forma continua `tablet_buzon_mejoras`, `historial_movimientos` y `wsp_recepcion_mensajes`.
- **Detección de Patrones Reales:**
  - *Pagos posteriores diferidos:* Identifica cuándo las recepcionistas liquidan cuentas al momento del Check-Out y asegura que el cálculo de `totalCuenta` contemple `deuda_ant` y consumos sin fricción.
  - *Cambios de habitación con lápiz `✏️`:* Mantiene la integridad referencial de `estanciaUuid` y `habOriginal` para preservar pagos y tienda.
  - *Ventas y correcciones de tienda:* Permite registrar y cambiar productos rápidamente sin bloqueos.
  - *Consultas WhatsApp:* Detecta solicitudes recurrentes de comprobantes con RUC o vouchers de pago para acelerar la conciliación.
- **Replicación:** Los patrones detectados se almacenan localmente y se propagan en tiempo real a Supabase `aprendizaje_compartido_hostal` para nutrir la memoria de ambas laptops y de los agentes IA.

### E. Guardrail `GR-08: Candado de Código para Arnés² y Promoción de Pruebas`:
- **100% LIBRE para Operaciones Diarias del Negocio:** En la WebApp y Hoja operativa de recepción, el personal puede realizar check-in, registrar o pagar consumos posteriores, cambiar de habitación, cambiar productos en tienda por error de digitación y eliminar estancias libres de cualquier código o contraseña restrictiva.
- **CÓDIGO OBLIGATORIO para Configuración y Promoción:** Exige estrictamente el código de administrador (`7991`, validado contra el hash SHA-256 `c2f593c98b648395cc08947d97a7d4829db29ffa5c924c44ae1bc2eeac30b05c` / `ARNES_AUTH_HASH`) únicamente al:
  1. Modificar manifiesto, guardrails, sondas o skills del sistema de seguridad Arnés².
  2. Ejecutar la promoción del sistema de pruebas a la base operativa mediante `promover_a_operativa.py --aplicar --codigo-arnes <codigo>`.

### F. Promoción a Operativa Completada (`2026-11-14` - Commit `8f16801`):
- **Archivos promovidos desde `C:\ovalo_mariategui_prueba` a `app_local_recepcion` (`8080`) y GitHub:**
  - `config.py`, `database.py`, `domain_logic.py`, `instalar_arnes_laptop2.py`, `sync_engine.py`, `yape_manager.py`
  - `static/CheckInRapido.html`, `static/PagosPosteriores.html`, `static/PanelDisponibilidad.html`, `static/VentaTienda.html`, `static/js/yape_vivo.js`
- **Respaldo previo creado en:** `C:\Hostal_Sistema\respaldos_promocion\20261114_081935` (tag git `respaldo-previo-20261114_081935`).
- **Estado verificado en `http://127.0.0.1:8080`:**
  - 2 casillas de Yape (Monto + Hora 12h AM/PM) operativas en todas las vistas.
  - Extractor Maelen BCP (49 movimientos históricos, 23 del mes), Vigilante WhatsApp +5m y Agente Revisor 08:30 AM con regla Cero Omisiones activos.
  - Vitrina Tienda con 36 turnos históricos (`h1..h36`), encabezados completos (N° conteo, responsable, fecha y hora) y columnas fijas de `145px` sin letras cortadas.

### G. Optimización Definitiva de Supabase y Entorno de Prueba 100% Local (`2026-11-17 / 2026-11-18` — Commits `3f74e18`..actual):
- **¿Por qué se hizo?**
  1. **Consumo de Egress Bandwidth en Supabase (`> 5.5 GB`):** El entorno de pruebas (`Ovalo_Mariategui_PRUEBA`, puerto `8082`) estaba subiendo y descargando continuamente en `sandbox_change_log` (`140,074 filas` / `73 MB`) cada vez que corría el conciliador de Yape (`yape_conciliacion_hostal`) y `calzado_cache`.
  2. **Bucle de descarga en Laptop 2 (`log_id=30931`):** La Laptop 2 estaba detenida en el cursor `30931` desde el `14/11/2026` porque unas filas intermedias en `supabase_change_log` (`hostal_documentos_ocr` con columna `procesado_en` y colisiones de `id_timestamp_str` en `estancias`) lanzaban excepción SQLite, haciendo `rollback` del lote de 500 filas y volviendo a descargar las mismas 500 filas cada 25 segundos (~1.2 GB/día).
  3. **Esquema antiguo de `yape_conciliacion_hostal` en Supabase:** La tabla en Supabase tenía las columnas antiguas (`fecha_hora_banco`, `origen_destino`) mientras SQLite usaba las nuevas (`fecha_operacion`, `hora_operacion`, `remitente_bcp`, `monto_bcp`), provocando avisos `HTTP 400`.
- **¿Qué se hizo exactamente (en Supabase, en Prueba `8082` y promovido a Operativa `8080`)?**
  1. **`Ovalo_Mariategui_PRUEBA` (`8082`) ahora es 100% LOCAL (Cero Supabase):**
     - En `config.py`, `instalar_arnes_laptop2.py` y `data/hotel_perfil.json` se fijó `"conectar_pruebas_supabase": false` y en `supabase_sync.py` se fijó `_SANDBOX = False`.
     - En Supabase se vació `sandbox_change_log` (`0 filas` / `0 KB`) y se instaló el trigger `trg_discard_sandbox_log` (`RETURN NULL`) para que jamás vuelva a acumular datos de prueba en la nube.
     - Toda edición o prueba en `C:\ovalo_mariategui_prueba` ocurre 100% en el disco duro local de la laptop y luego se pasa a la operativa (`C:\Recepcion_Ovalo_Local` o `app_local_recepcion`) con `Promover_a_Operativa.bat` (`promover_a_operativa.py`).
  2. **Destrabe Total del Cursor y Filtro Anti-Rebote en `pull_from_supabase()` (`supabase_sync.py`):**
     - Cada fila individual descargada desde `supabase_change_log` ahora se ejecuta dentro de su propio bloque `try...except sqlite3.Error`, de modo que si una fila aislada tiene una diferencia de esquema o índice, se ignora esa fila puntual sin abortar las otras 499 y el cursor `sb_last_log_id` avanza siempre.
     - Se excluyeron de `supabase_change_log` las 3 tablas de caché recalculables (`TABLAS_SOLO_LOCAL_CACHE = {"yape_conciliacion_hostal", "calzado_cache", "sunat_lineas_cache"}`) tanto en Python como en la función `fn_log_hostal_change()` de Postgres.
     - Se alinearon las columnas de `yape_conciliacion_hostal` en Supabase (`HTTP 200 OK`).
  3. **Autolimpieza Nocturna 03:00 AM (`autolimpieza_bitacora_supabase_3am` en `scheduler_recepcion_24x7.py`):**
     - Cada madrugada a las `03:00 AM` invoca la función RPC `purgar_bitacoras_transito(p_keep_log_id)` en Supabase.
     - **Seguridad Total de Datos:** Esta limpieza borra **SOLO** los "sobres de aviso" viejos de la tabla de mensajería `supabase_change_log` que **ambas laptops ya leyeron y guardaron**. **JAMÁS toca ni borra** las 33 tablas operativas reales de Supabase ni el archivo `ovalo_sistema.db` (SQLite) de ninguna de las dos laptops.

---

## 🤖 7. PLAN PENDIENTE DE EJECUCIÓN EN LAPTOP 2: ASISTENTE DE RECEPCIÓN ("COPILOTO ÓVALO") + CONTROL ARNÉS² `GR-09` [2026-11-14]

> **Archivo detallado del Plan:** `PLAN_ASISTENTE_RECEPCION.md` (ubicado en esta misma carpeta `leemeantrigravityrecepcion` y en `Downloads\PLAN_ASISTENTE_RECEPCION.md`).

### A. Qué debe hacer el Asistente en `Ovalo_Mariategui_PRUEBA` (`puerto 8082`):
1. **Consultor Operativo Instantáneo (4 Frentes):**
   - **Deudas de Habitación y Yapes del Día:** Desglose exacto de cuentas (`estancias`, `pagos`, `ventas_tienda`, `servicios_detalle`) y Yapes recibidos hoy (`yape_conciliacion_hostal` + `cache_correo_maelen_bcp.json`).
   - **WhatsApp de Recepción (Comprobantes Pendientes):** Responde qué facturas o boletas pedidas por WhatsApp (`wsp_recepcion_mensajes`, `wsp_recepcion_documentos`) o en estancias (`solicita_comp = 'Sí'`, `estado_comp != 'Emitido'`) están pendientes de emitir o enviar (`wsp_recepcion_cola_envio`).
   - **Vitrina y Faltantes (`vitrina_local` `h1..h36`):** Responde qué productos faltan hoy, **por qué faltan** (cruzando stock previo vs. `ventas_tienda` y `almacen_movimientos`) y **desde qué turno/conteo, fecha/hora y con qué responsable empezaron a faltar** (`vitrina_h1_header..vitrina_h36_header`).
   - **Caja Diaria del Turno Actual:** Solo informa el cuadre del día actual para entrega de turno.
2. **Doble Modalidad Práctica (Cero texto largo que confunda y Cero "Acción Guiada" que abra modales solos):**
   - **Modalidad 1 — Guía Visual con Fotos Reales (`📸 Muéstrame con fotos`):** Muestra tarjetas paso a paso con capturas reales de la aplicación (`/static/img/guia_asistente/*.png`) con flechas y numeración (`①`, `②`, `③`) indicando dónde hacer clic.
   - **Modalidad 2 — Ejecución Directa por el Asistente (`⚡ Hazlo tú por mí`) + Extracto para el Dueño:** El recepcionista le pide hacer el movimiento (ej. cambio de habitación, venta de tienda, pago posterior, continuar estancia), confirma con 1 clic en el mismo chat (`✅ Confirmar`), el asistente ejecuta la función oficial de `domain_logic.py` y **guarda obligatoriamente el registro Antes/Después en `asistente_movimientos_log` para el Extracto del Dueño**.

### B. Cortafuegos Arnés² `GR-09` (Confidencialidad Financiera, Gastos y Credenciales):
- **PROHIBIDO dar claves o accesos:** Claves SOL de SUNAT, contraseñas en general, tokens, código de Arnés² o correos personales/externos.
- **PROHIBIDO dar ingresos semanales, mensuales o totales:** Nunca revelar cuánto generó el hotel en la semana, en el mes ni acumulados totales (solo se permite el monto diario del turno actual).
- **PROHIBIDO hablar o analizar Gastos (`gastos` / `compras_tienda_lotes`):** La tabla `gastos` está 100% bloqueada para el asistente porque son gastos personales/internos de la empresa.
- **PROHIBIDO usar "Acción Guiada" intrusiva:** No abrir ventanas emergentes solas para no asustar al recepcionista.
- **Bitácora de Intentos Bloqueados:** Cualquier pregunta bloqueada por `GR-09` queda registrada como `INTENTO_BLOQUEADO_GR09` en `asistente_movimientos_log` para el Extracto del Dueño.

### C. Archivos a Crear / Modificar en `C:\ovalo_mariategui_prueba`:
1. `database.py` y `supabase_sync.py`: Crear tabla `asistente_movimientos_log` y añadirla a `TABLAS_GRUPO2_PK` y triggers Delta.
2. `asistente_recepcion.py` **[NUEVO]**: Motor con filtro `GR-09`, consultas de Habitación/Yape/WSP/Vitrina `h1..h36`, guías con fotos, ejecutor directo auditado y generador del Extracto del Dueño.
3. `arnes_recepcion/.agent/harness/guardrails/safety_rules.json`: Añadir regla `GR-09`.
4. `static/PanelDisponibilidad.html`, `domain_logic.py` y `main.py`: Burbuja flotante `🤖 Copiloto Óvalo`, visor de fotos guía, confirmación en chat, botón de **Extracto del Dueño** y endpoints RPC (`consultarAsistenteRecepcion`, `confirmarMovimientoAsistente`, `obtenerExtractoDuenoAsistente`).

---

## 💬 8. RESUMEN INTELIGENTE DE WHATSAPP (`wsp_inteligencia.py`) Y SINCRONIZACIÓN ESPEJO ENTRE LAPTOPS EN PRUEBA (`8082 ↔ 8082`) [2026-11-18 — Commit `6dd26af`]

### A. ¿Cuál fue el motivo y qué problema se resolvió?
1. **En Laptop 1 salía `"WSP: resumen no disponible"` y no se actualizaban los mensajes:**
   - **Causa 1 (Esquema incompleto en Supabase):** El recolector de WhatsApp en Laptop 2 (`whatsapp_mcp_recepcion/db.js` + `wsp_daemon.js`) guardaba 20 columnas en `wsp_recepcion_mensajes` (`id_mensaje`, `jid`, `remitente_jid`, `nombre_emisor`, `es_mio`, `timestamp_unix`, `tipo_mensaje`, `tiene_archivo`, `ruta_archivo`, `creado_en`), pero la tabla `wsp_recepcion_mensajes` en Supabase solo tenía las 10 columnas iniciales. Cuando Laptop 2 intentaba subir los mensajes a Supabase, Postgres devolvía `HTTP 400 Bad Request` y `supabase_sync.py` los descartaba en silencio (`0 mensajes` en Supabase).
   - **Causa 2 (Lectura local en modo Prueba):**En Laptop 2 el entorno de prueba (`8082`) abría directamente `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db` del disco duro de Laptop 2 (`_abrir_operativa_ro()`), donde sí estaban los mensajes. En cambio, en Laptop 1 la base local tenía `0 mensajes` y en `MODO_PRUEBA` no descargaba las tablas `wsp_recepcion_*` desde Supabase.
   - **Causa 3 (Rutas RPC en `main.py` de Prueba):** Faltaban registrar en `main.py` las 3 llamadas RPC (`obtenerAlertasWsp`, `obtenerBandejaWsp`, `actualizarResumenWspAhora`) además de la inyección en `domain_logic.py`, y el hilo `_background_wsp_worker` para los cortes programados.

### B. ¿Qué cambios técnicos se aplicaron (100% en Entorno de Prueba `8082` + Supabase, sin tocar la Operativa)?
1. **Migración en Supabase (`hostal-ovalo` — proyecto `fmlfqupwscymosvwnnzx`):**
   - **`wsp_recepcion_mensajes`:** Se agregaron las columnas `id_mensaje` (con índice único), `jid`, `remitente_jid`, `nombre_emisor`, `es_mio`, `timestamp_unix`, `tipo_mensaje`, `tiene_archivo`, `ruta_archivo`, `creado_en`.
   - **`wsp_recepcion_chats`:** Se agregaron `tipo`, `ultimo_mensaje`, `fecha_ultimo_mensaje`, `no_leidos`.
   - **`wsp_recepcion_documentos`:** Se agregaron `id_mensaje`, `jid`, `tipo_archivo`, `nombre_archivo`, `ruta_local`, `metodo_extraccion`, `ruc_emisor`, `fecha_procesado`.
   - **`wsp_recepcion_cola_envio`:** Se agregaron `telefono_o_jid`, `mensaje`, `ruta_adjunto`, `nombre_adjunto`, `detalle_error`.
   - **Tablas nuevas en Supabase:** Se crearon `wsp_recepcion_estado` (`clave` PK, `valor`, `actualizado_en`) y `wsp_resumen_ejecutivo` (`id` PK, `fecha_hora`, `slot_horario`, `fuente_ia`, `payload_json`) con políticas RLS (`hostal_sync_policy`) y triggers `fn_log_hostal_change()`.
2. **Puente Espejo de WhatsApp para Pruebas (`_sincronizar_espejo_wsp_modo_prueba` en `supabase_sync.py`):**
   - Añadidas `wsp_recepcion_estado` y `wsp_resumen_ejecutivo` al catálogo `TABLAS_GRUPO2_PK` y auto-migración de columnas híbridas en SQLite (`_extra_wsp_cols`).
   - En `MODO_PRUEBA` (`8082`), Laptop 2 sube automáticamente sus chats, mensajes, documentos, estado y cortes de `wsp_resumen_ejecutivo` a Supabase, y Laptop 1 (`8082`) los descarga en solo lectura (`extraer_datos_reales_supabase_readonly`) cada 25 segundos y cada vez que el usuario pulsa **"Actualizar resumen ahora"**.
   - **Aislamiento garantizado:** No toca estancias, pagos, tienda ni Google Sheets.
3. **Motor Funcional de WhatsApp (`wsp_inteligencia.py` + `main.py` + `PanelDisponibilidad.html`):**
   - **Visor 100% Solo Lectura y Síntesis Ejecutiva:** Sin botones de envío ni plantillas que alteren conversaciones reales.
   - **Fallback Inteligente de Base de Datos (`_recolectar_chats_clientes` y `_estado_daemon_raw`):** Si en `MODO_PRUEBA` la base operativa local de esa laptop tiene 0 mensajes (como ocurre en Laptop 1), cae automáticamente a la base SQLite de Prueba (`C:\ovalo_mariategui_prueba\data\ovalo_sistema.db`) alimentada desde Supabase.
   - **Auto-regeneración de corte vacío:** Si el último corte en `wsp_resumen_ejecutivo` se había generado cuando aún había 0 mensajes, en cuanto entran los mensajes reales regenera el análisis automáticamente.
   - **Cruce automático con el Hotel:** Cruza cada conversación de WhatsApp por celular de 9 dígitos, nombre de perfil (`sugerencia_wsp`), DNI/RUC, habitación y comprobantes emitidos en `estancias`, `huespedes_padron`, `empresas_padron`, `sunat_ventas` y `sunat_lineas_cache`.
   - **Clasificación en 3 Bloques Claros en el Modal WSP:**
     1. 🔴 **Pendientes por responder / emitir comprobante** (ej. solicitudes de factura/boleta, vouchers enviados o consultas sin respuesta de recepción).
     2. 琥 **Recomendaciones, Notas y Acuerdos del turno** (ej. huéspedes que avisan hora de llegada, acuerdos de tarifa o recojo).
     3. 🟢 **Atendidos y cerrados** (conversaciones donde recepción ya envió el PDF del comprobante o respondió y cerró con agradecimiento).
   - **4 Cortes Programados al Día (`OvaloWspWorker`):** Ejecuta cortes automáticos a las `08:00`, `13:00`, `19:00` y `23:00`, más actualización manual en vivo con el botón **"Actualizar resumen ahora"**.

### C. Estado Verificado en Vivo (`2026-11-18`):
- **En Supabase y en ambas Laptops (Prueba `8082`):** `83 chats`, `238 mensajes`, `4 estados` (`CONECTADO`) y `20 cortes ejecutivos`.
- **En Barra Superior de `http://127.0.0.1:8082`:** Muestra `WSP: 3 por responder · 7 atendidos (Corte 08:00)` con `Kata`, `Mari` e `ISA` en pendientes, 1 nota de seguimiento y 7 chats atendidos.


