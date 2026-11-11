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
