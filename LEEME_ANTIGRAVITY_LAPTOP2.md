# 📋 CONTEXTO Y MEMORIA COMPLETA PARA ANTIGRAVITY EN LAPTOP 2 (RECEPCIÓN)
**Fecha de sincronización desde Laptop 1:** 2026-11-11
**Usuario en Laptop 2:** `C:\Users\ovalo mariategui`
**Carpeta de la WebApp en Laptop 2:** `C:\Recepcion_Ovalo_Local` (o repositorio `app_local_recepcion`)
**Carpeta compartida Syncthing:** `C:\Hostal_Sistema`

---

## 1. ¿QUÉ SE CONSTRUYÓ EN LAPTOP 1 (FASES 1 A 4 COMPLETADAS)?

Se diseñó y desplegó la arquitectura **SQLite Dual + Supabase (`hostal-ovalo`)** para el **Hotel El Óvalo Mariátegui** (`RUC 20611986484`), separando estrictamente los datos privados del administrador (Grupo 1) de los datos operativos y contables del Hostal (Grupo 2):

### 🔒 GRUPO 1 — Base Privada (`wsp_wspdocs_russ.db`)
- **Ubicación:** Exclusivamente en Laptop 1 (`c:\Users\josec\Downloads\trabajo\Sistema de agentes IA\wsp_wspdocs_russ.db`).
- **Regla de Oro:** **JAMÁS** se sube a Supabase ni se copia a Laptop 2. Contiene el WhatsApp personal de José Eduardo, su OCR personal y sus emisiones RUS/RHE con DNI (`10741182934`).

### 🏨 GRUPO 2 — Base Compartida del Hostal (`ovalo_sistema.db` ↔ Supabase `hostal-ovalo` ↔ Laptop 2)
- **Ubicación en Laptop 1:** `c:\Users\josec\Downloads\trabajo\Sistema de agentes IA\ovalo_sistema.db`
- **Ubicación en Laptop 2:** `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db` (migrada automáticamente desde `ovalo_local.db` por `config.py`).
- **Servidor Cloud Supabase (`hostal-ovalo`):**
  - **Project ID:** `fmlfqupwscymosvwnnzx`
  - **Región:** `sa-east-1` (São Paulo, Brasil)
  - **Project URL:** `https://fmlfqupwscymosvwnnzx.supabase.co`
  - **Anon Key (JWT):** `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0.pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g`
  - **Publishable Key:** `sb_publishable__6OMyuDgRv0SFNSQYfy9KQ_gy9Mtayq`
  - **Header de Seguridad RLS (`x-hostal-sync-token`):** `ovalo_master_sync_2026_k9x`
  - **IMPORTANTE:** No se requiere iniciar sesión con ningún correo en Laptop 2. La conexión usa `SUPABASE_URL + SUPABASE_ANON_KEY + x-hostal-sync-token` ya integrados en `config.py` y `supabase_sync.py`.

---

## 2. LAS 35 TABLAS DEL GRUPO 2 YA CREADAS Y POBLADAS EN SUPABASE

1. **Operación de Recepción y Caja (23 tablas):**
   - `estancias` (5,626 filas), `pagos` (2,864 filas), `ventas_tienda` (2,969 filas), `gastos` (838 filas), `huespedes_padron` (2,278 filas), `empresas_padron` (16 filas), `habitaciones_config` (29 filas), `habitaciones_colores`, `catalogos_local` (78 filas), `vitrina_local` (25 filas), `almacen_movimientos` (270 filas), `compras_tienda_lotes`, `personal_config` (3 filas), `horario_registros` (751 filas), `horario_auditoria` (156 filas), `horario_liquidaciones` (4 filas), `control_pases`, `servicios_detalle`, `historial_movimientos` (202 filas), `estado_sistema`, `sync_queue`, `auditoria_audio_noche` (12 filas), `tablet_limpieza_dia`, `tablet_firmas_conformidad`, `tablet_buzon_mejoras`.
2. **Facturación SUNAT, SIRE, OCR Hostal y Yape (6 tablas):**
   - `sunat_ventas` (355 comprobantes E001/EB01/NC de Sep y Oct)
   - `sunat_lineas_cache` (519 líneas por noche de hospedaje)
   - `calzado_cache` (385 filas de calzado SUNAT vs Estancias)
   - `sire_compras_hostal` (37 compras SIRE Oct/Nov con estado de bancarización)
   - `hostal_documentos_ocr` (comprobantes/vouchers del Hostal extraídos por OCR)
   - `yape_conciliacion_hostal` (cruce bancario Yape BCP)
3. **WhatsApp de Recepción (4 tablas listas para alimentar desde Laptop 2):**
   - `wsp_recepcion_chats` (PK: `jid`)
   - `wsp_recepcion_mensajes` (PK: `id_mensaje`)
   - `wsp_recepcion_documentos` (PK: `id`)
   - `wsp_recepcion_cola_envio` (PK: `id`)
4. **Tabla Delta en Tiempo Real en Supabase:**
   - `public.supabase_change_log` (alimentada automáticamente por el trigger PostgreSQL `fn_log_hostal_change()` en las 33 tablas de datos).

---

## 3. ¿CÓMO FUNCIONA LA SINCRONIZACIÓN EN LAPTOP 2 (`supabase_sync.py`)?

- **Triggers SQLite Locales (`_supabase_outbox`):** Cada vez que Recepción guarda un Check-In, Pago, Venta de Tienda, Gasto, Horario, Pase o mensaje de WhatsApp de Recepción en `ovalo_sistema.db`, el trigger SQLite lo mete en `_supabase_outbox` en `0 ms` y `push_to_supabase()` lo sube a Supabase en `< 150 ms`.
- **Descarga Delta (`pull_from_supabase()`):** Cada ciclo consulta `GET /rest/v1/supabase_change_log?log_id=gt.<sb_last_log_id>`, ignora los cambios originados por su propio `EQUIPO_ID` y aplica en `ovalo_sistema.db` todo lo que haya hecho Laptop 1 (con `_supabase_sync_control.pulling = 1` para evitar bucles).
- **Aislamiento de Google Sheets:** En `sync_engine.py`, `pull_from_sheets()` activa `pulling = 1` durante su transacción para que las lecturas periódicas de Google Sheets **no disparen** `_supabase_outbox` ni sobre-escriban Supabase.

---

## 4. ¿QUÉ FALTA HACER EN LAPTOP 2 CON ANTIGRAVITY?

1. **Verificar que `C:\Recepcion_Ovalo_Local` esté en el último Commit (`8bc6112` o superior):**
   - Probar el endpoint local: `http://127.0.0.1:8080/api/supabase/status`
   - Debe responder `"status": "OK"`, `"equipo": "LAPTOP_2_RECEPCION_..."`, `"outbox_pendientes": 0`.
   - Si `C:\Recepcion_Ovalo_Local` aún no se actualizó sola, copiar los archivos `config.py`, `supabase_sync.py`, `main.py` y `sync_engine.py` incluidos en esta misma carpeta (`PARA_ANTIGRAVITY_LAPTOP2\webapp_actualizada\`) hacia `C:\Recepcion_Ovalo_Local\` y reiniciar la WebApp.

2. **Si el usuario desea activar la extracción de WhatsApp de Recepción en Laptop 2 (`wsp_recepcion_*`):**
   - Usar la plantilla incluida en `PARA_ANTIGRAVITY_LAPTOP2\whatsapp_mcp_recepcion\` que escribe directamente en las tablas `wsp_recepcion_chats`, `wsp_recepcion_mensajes`, `wsp_recepcion_documentos` y `wsp_recepcion_cola_envio` de `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db`.
   - Como esas 4 tablas ya tienen Triggers SQLite activos en `supabase_sync.py`, todo mensaje o voucher que llegue al WhatsApp de Recepción en Laptop 2 subirá automáticamente a Supabase y se verá en vivo en el visor `http://127.0.0.1:8788` de Laptop 1.

3. **Reglas de Estilo del Usuario (`GEMINI.md` / `AGENTS.md`):**
   - **Navegador por defecto:** Usar siempre **Microsoft Edge** (`edge-browser` MCP / `msedge`).
   - **Orden obligatorio de cada respuesta:**
     1. `> **🎓 En esta respuesta puedes aplicar:** [Herramienta] — [Explicación sencilla] → *Ejemplo: "..."*` (SIEMPRE EN LA PRIMERA LÍNEA).
     2. Respuesta directa al grano (Modo Turbo).
     3. `🏗️ Arquitectura In-House (Cómo funciona por dentro):` (3-4 líneas con analogías prácticas).
     4. `💡 Extrapolación` (máximo 3 ideas al final, sin implementar hasta aprobación).
