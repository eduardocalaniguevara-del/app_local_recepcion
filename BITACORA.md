# 📓 Bitácora Viva de Construcción — `C:\Ovalo_Sistema_v2`
> **Actualizado:** 21/11/2026 — Monitoreado en vivo desde `http://127.0.0.1:8788/?grupo=sistema_v2`

---

## 🚦 Estado General por Fases

- **✅ Fase 0 — Regla Global Modo Arquitecto Senior + Prioridad Visual (`/learn`):** COMPLETADA
- **✅ Fase 1 — Esqueleto Escalable `01..06` + Aduana `.gitignore` + Plano Interactivo (`:8788`):** COMPLETADA
- **✅ Fase 2 — Base SQLite Limpia (`04_GIT_app_motor/db.py`) + Puente Supabase 3-Niveles (`04_GIT_app_motor/sync_nube.py`):** COMPLETADA
  - **Roles de 3 Niveles oficializados en `config.py` y `.env.example`:**
    1. `NIVEL_1_INGENIERIA` (Tu Laptop 1: crea código, extrae Gmail personal BCP → Supabase).
    2. `NIVEL_2_MADRE_HOTEL` (Laptop 2 Recepción: único QR físico de WhatsApp + emisor SUNAT + vigilante 24/7).
    3. `NIVEL_3_TERCIARIA` (Laptop 3, 4, Gerencia: recibe toda la información y chats de WhatsApp vía Supabase sin escanear QR, y envía mensajes a través de `wsp_recepcion_cola_envio` hacia la Laptop Madre).
  - **Base SQLite `03_DB_sqlite_local/ovalo.db` creada y verificada (`INTEGRO`):**
    - 38 tablas operativas en modo `WAL` + autorreparación en caliente (`repair_db_if_malformed`).
    - Candado `UNIQUE(fecha_hora, monto, operacion_nro, titular_origen)` en `maelen_transferencias_correo` probado con doble inserción (0 duplicados).
  - **Puente `sync_nube.py` activo:**
    - 99 triggers SQLite de `0 ms` hacia `_supabase_outbox` (con bloqueo estricto de las 3 tablas caché `yape_conciliacion_hostal`, `calzado_cache`, `sunat_lineas_cache`).
    - Lectura y escritura en vivo con Supabase `hostal-ovalo` verificada (`HTTP 200`).
- **⏳ Fase 3 — Operación de Recepción (`04_GIT_app_motor/estancias.py` + `05_GIT_web_mostrador/`):** PENDIENTE (Siguiente paso)
- **⏳ Fase 4 — Conciliación Bancaria (`02_PRIV_correo_bcp/` + `04_GIT_app_motor/yape_bcp.py`):** PENDIENTE
- **⏳ Fase 5 — SUNAT (`04_GIT_app_motor/sunat.py`) + WhatsApp (`04_GIT_app_motor/whatsapp.py`) + `motor_24x7.py`:** PENDIENTE
- **⏳ Fase 6 — Clon en Solo Lectura en Laptop Madre (L2) / Terciarias (L3+) y Retiro de Carpetas Antiguas:** PENDIENTE
