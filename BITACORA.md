# 📓 Bitácora Viva de Construcción — `C:\Ovalo_Sistema_v2`
> **Actualizado:** 21/11/2026 — Monitoreado en vivo desde `http://127.0.0.1:8788/?grupo=sistema_v2`

---

## 🚦 Estado General por Fases (`17 / 17 Componentes en Disco — 100%`)

- **✅ Fase 0 — Regla Global Modo Arquitecto Senior + Prioridad Visual (`/learn`):** COMPLETADA
- **✅ Fase 1 — Estructura Limpia de 5 Carpetas (`01..05`) + Aduana `.gitignore` + Plano Interactivo 6 Zonas (`:8788`):** COMPLETADA
  - **Disección Visual en el Mapa (`:8788`) en 6 Zonas con sus carpetas (`01..05` + `.env`) por equipo:**
    - **Zonas 1, 2 y 3 (Tu Laptop 1 en Modo Editor):** Donde tienes `01_PRIV_borradores\` para trabajar con agentes IA y editas el código `03_GIT`, `04_GIT` y `05_GIT` que sube a GitHub.
    - **Zona 4 (Puentes Oficiales):** GitHub `sistema-v2` (Código), Supabase `hostal-ovalo` (Datos SQLite) y Syncthing (Solo PDFs SUNAT).
    - **🏢 Zona 5 (Hotel Madre de Recepción — Laptop 2 24/7):** Muestra sus 5 carpetas operando al 100%:
      1. `01_PRIV_boveda_hotel\`: Guarda el único QR de WhatsApp de Recepción, las Claves SOL SUNAT y la sesión de `hostal.ovalo@gmail.com`.
      2. `.env`: `ROL_NODO=NIVEL_2_MADRE_HOTEL` · `ACTIVAR_MOTOR_24X7=true` · `CORREO_RECEPCION_HOTEL=hostal.ovalo@gmail.com`.
      3. `02_DB_sqlite_local\ovalo.db`: Captura todo lo del hotel 24/7 y lo **SUBE (`PUSH`)** a Supabase.
      4. `03_GIT_app_motor\` + `04_GIT_web_mostrador\`: Descargados por `git pull`, emiten SUNAT y envían por el WhatsApp de Recepción.
      5. `05_GIT_vigilante_24x7\motor_24x7.py`: **🟢 ENCENDIDO 24/7 AQUÍ**.
    - **💻 Zona 6 (Laptops Terciarias y tu Laptop 1 en Modo Visor):** Muestra sus 5 carpetas en modo receptor:
      1. `01_PRIV_...`: **CERO QR de WhatsApp**, CERO claves SUNAT y CERO correo del hotel.
      2. `.env`: `ROL_NODO=NIVEL_3_TERCIARIA` · `ACTIVAR_MOTOR_24X7=false` · `TIENE_QR_WSP_FISICO=false`.
      3. `02_DB_sqlite_local\ovalo.db`: **SOLO RECIBE (`PULL`)** desde Supabase todo lo que el Hotel Madre produjo.
      4. `03_GIT_app_motor\` + `04_GIT_web_mostrador\`: Muestran la misma interfaz en Google Chrome leyendo `02_DB` sin abrir WhatsApp.
      5. `05_GIT_vigilante_24x7\motor_24x7.py`: **⚪ EN REPOSO (APAGADO AQUÍ)**.
- **✅ Fase 2 — Base SQLite Limpia (`03_GIT_app_motor/db.py`) + Puente Supabase (`03_GIT_app_motor/sync_nube.py`):** COMPLETADA
- **✅ Fase 3 — Mostrador de Recepción (`03_GIT_app_motor/estancias.py` + `04_GIT_web_mostrador/` + Ícono Google Chrome):** COMPLETADA
- **✅ Fase 4 — Conciliación Bancaria 24/7 en Laptop 2 (`03_GIT_app_motor/yape_bcp.py` leyendo `hostal.ovalo@gmail.com` sin `WSP +5m`):** COMPLETADA
- **✅ Fase 5 — SUNAT (`03_GIT_app_motor/sunat.py`) + WhatsApp (`03_GIT_app_motor/whatsapp.py`) + Ronda `motor_24x7.py`:** COMPLETADA
- **✅ Fase 6 — Publicado en GitHub (`sistema-v2`) + Seguidor Multi-Laptop en Vivo (`seguidor_v2_*`) + Paquete Instalador 1-Clic en `Descargas`:** COMPLETADO
  - **🛰️ Seguidor en Vivo de Carpetas y Archivos `.py` en Otras Laptops (`03_GIT_app_motor/salud.py`):**
    - Cada laptop (`NIVEL_1`, `NIVEL_2_MADRE_HOTEL`, `NIVEL_3_TERCIARIA`) escanea sus 5 carpetas (`01_PRIV` a `05_GIT`) + `.env`, detecta qué archivo `.py`/`.html`/`.js` se movió o actualizó por última vez y qué commit Git tiene, y lo publica automáticamente en Supabase (`estado_sistema` -> `seguidor_v2_<ROL_NODO>`).
    - El **Plano Interactivo v2 (`:8788`)** lee esos sensores desde Supabase cada 5 segundos e ilumina las cajas de **Zona 5 (Hotel Madre L2)** y **Zona 6 (Terciarias)** en tiempo real cuando una carpeta o `.py` se crea o actualiza en las otras laptops.
- **✅ Fase 7 — Desconexión Definitiva del Sistema Antiguo y Unificación en `:8080` (Laptop 1 y Laptop 2):** COMPLETADA
  - **Escritorio Limpio:** Eliminados los accesos directos antiguos (`Recepcion Hotel Ovalo (Local).lnk` y `Ovalo_Mariategui_PRUEBA.lnk`). Solo queda el acceso oficial **`Hostal Ovalo - Sistema v2`**.
  - **Candado Antirretorno en Carpetas Antiguas:** Los `lanzador_escritorio.py` de `C:\ovalo_mariategui_prueba` y `...\app_local_recepcion` fueron desconectados (respaldados como `.DESCONECTADO_V1.py`) y redirigidos a `C:\Ovalo_Sistema_v2\lanzador_escritorio.py`, de modo que sus antiguos archivos `data\ovalo.db` jamás vuelvan a encenderse ni a tocar Supabase.
  - **Puerto Único Oficial (`:8080`):** Tanto **Laptop 2 (`NIVEL_2_MADRE_HOTEL`)** como **Laptop 1 (`NIVEL_1_INGENIERIA`)** operan exclusivamente en `http://127.0.0.1:8080` sobre `02_DB_sqlite_local\ovalo.db`.
- **✅ Fase 8 — Estandarización y Emparejamiento 100% SQLite ↔ Supabase (`L1_V2` == `L2_V2` == `Supabase`):** COMPLETADA
  - **Exclusión de `auditoria_audio_noche` (5.77 MB) de Supabase:** Movida a `TABLAS_SOLO_LOCAL_CACHE` en `03_GIT_app_motor/db.py` para ahorrar 60% de tráfico Egress; queda local / Syncthing.
  - **Esquema Supabase Homologado:** Agregadas en Supabase `unificado_en_uuid` y `opcion_decision` en `estancias`, `yape_tienda_usado_en` en `ventas_tienda`, columnas XML de `sunat_ventas`, `sire_compras_hostal`, `hostal_documentos_ocr` y creada la tabla `maelen_transferencias_correo`.
  - **Aduana de Recorte Automático en `PUSH` y Lectura `row_json` en `PULL` (`03_GIT_app_motor/sync_nube.py`):** Si cualquier tabla SQLite local tiene una columna extra que no exista en Supabase, `push_to_supabase()` la recorta al vuelo y reintenta sin cortar la transmisión; `pull_from_supabase()` lee `row_json` desde `supabase_change_log`.
  - **Base Maestra Unificada:** Sembrada la base de Recepción (`5,831 estancias`, `3,103 pagos`, `778 sunat_ventas`, `55 transferencias BCP`) con `0` pendientes en `_supabase_outbox`.
