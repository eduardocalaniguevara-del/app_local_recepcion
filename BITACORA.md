# 📓 Bitácora Viva de Construcción — `C:\Ovalo_Sistema_v2`
> **Actualizado:** 27/11/2026 — Monitoreado en vivo desde `http://127.0.0.1:8788/?grupo=sistema_v2`

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
- **✅ Fase 9 — Reconexión Automática de WhatsApp Recepción (`db.js` → `ovalo.db` v2) y Filtro Oficial de Reenvío BCP (`8023`) a `hostal.ovalo@gmail.com`:** COMPLETADA
  - **Auto-reconexión de `wsp_daemon.js` en Laptop 2 (`03_GIT_app_motor/whatsapp.py`):** `vigilar_daemon()` redirige automáticamente `db.js` del daemon de WhatsApp en Recepción hacia `C:\Ovalo_Sistema_v2\02_DB_sqlite_local\ovalo.db` y lo reinicia sin tocar `auth_info` (cero QR nuevo). Rescatados y sincronizados los `91 chats` y `446 mensajes` de WhatsApp.
  - **Filtro de Correo BCP (`8023` Maelen) Activo en Gmail:** Configurado en el Gmail personal (`Disable forwarding` global activo + filtro exclusivo `from:(notificacionesbcp.com.pe) 8023` -> `Forward it to: hostal.ovalo@gmail.com` + `Never send it to Spam`). Así la Laptop 2 lee 24/7 únicamente las transferencias de la cuenta del hotel sin exponer ningún correo personal.
- **✅ Fase 10 — Purga Total de la Versión 1 (Disco + GitHub) y Despliegue Desde Cero 100% Git + Supabase (Cero ZIPs):** COMPLETADA (22/11/2026)
  - **1. Eliminación de Versiones Anteriores (V1) en Disco y GitHub:**
    - **Disco `C:\` limpio:** Eliminadas `C:\ovalo_mariategui_prueba`, `C:\Recepcion_Ovalo_Local`, `C:\sistema ovalo laptop 3` y las subcarpetas de backups V1 dentro de `C:\Hostal_Sistema` (`respaldos_promocion`, `actualizacion_webapp_local`, `PARA_ANTIGRAVITY_LAPTOP2`, `diagnosticos_14nov_archivo` y `vigilante_boletas.py`). Solo se conservan en `C:\Hostal_Sistema` los activos de Syncthing (PDFs SUNAT, `config\credentials.json`, audios y los scripts Playwright invocados por `sunat.py`).
    - **GitHub unificado (`eduardocalaniguevara-del/app_local_recepcion`):** Eliminada la Versión 1 de la rama `main` y borrada la rama basura `--push`. Ahora `main` y `sistema-v2` son **100% idénticas** (ambas contienen exclusivamente las 5 carpetas `01..05` del Sistema v2 y `publicar_github.py` actualiza ambas ramas en cada publicación). El antiguo repo `ovalo_mariategui_prueba` quedó archivado como `[OBSOLETO]`.
  - **2. Espejo Unidireccional hacia Google Sheets (`sincronizar_espejo_sheets` en `03_GIT_app_motor/sync_nube.py`):**
    - La fuente maestra es `02_DB_sqlite_local\ovalo.db ↔ Supabase`. Google Sheets funciona únicamente como **copia espejo unidireccional (`SQLite ➔ Sheets`)**: lo que se edita en la aplicación se refleja en Sheets respetando las fórmulas `=MAP(...)`, y jamás a la inversa.
  - **3. Hidratación Completa Automática para Laptops Nuevas (`bootstrap_initial_from_supabase`):**
    - Cuando una computadora nueva se instala desde cero (`< 500 estancias`), descarga automáticamente el **100% de las tablas maestras desde Supabase** (`5,762+ estancias`, `3,108+ pagos`, `29 habitaciones`, tienda y gastos), crea `sync_queue` en `init_db()`, ignora cualquier BOM UTF-8 en `.env` y marca `network_online = 1` (`🟢 En vivo`).
- **✅ Fase 11 — Facturación Electrónica SUNAT v2 Autonomizada (`03_GIT_app_motor`), Carpeta Local `comprobantes_pdf`, Conversor PDF ➔ Supabase y Cola Remota (`L1/L3 ➔ L2 Madre`):** COMPLETADA (23/11/2026)
  - **1. Motores SUNAT y WhatsApp Integrados en `03_GIT_app_motor\` (100% vía GitHub, Cero dependencia de `C:\Hostal_Sistema`):**
    - Integrados en `03_GIT_app_motor/sunat_motor_playwright.py`, `03_GIT_app_motor/sunat.py` y `03_GIT_app_motor/auditor_calzado_bcp_sunat.py` los motores Playwright + MS Edge Headless Stealth (Boletas `EB01`, Facturas `E001` al Contado, Notas de Crédito y Modo Borrador Preliminar con IGV 10.5% Ley 31556).
  - **2. Carpeta Local Exclusiva de PDFs en Laptop Madre (`02_DB_sqlite_local\comprobantes_pdf\`):**
    - Creada estructura local protegida en `.gitignore` (`boletas\`, `facturas\`, `notas_credito\`, `capturas\`). Ningún PDF nuevo se guarda en la antigua carpeta compartida `C:\Hostal_Sistema` ni sube a GitHub.
  - **3. Conversión Inmediata de PDF a Datos Estructurados en Supabase (`convertir_comprobante_a_datos_y_subir_supabase`):**
    - Apenas se emite un comprobante, el motor extrae `nro_cp`, `tipo_doc`, `fecha_emision`, `receptor_doc`, `receptor_nombre`, `op_gravada`, `igv_105`, `total`, `items_json`, `hab` y `noche_dmy`, y los sincroniza en vivo hacia `sunat_ventas`, `sunat_lineas_cache` y `estancias` en Supabase (verificado con la Boleta `EB01-6904` de `YESICA HUALLPARA AROCUTIPA` por `S/ 80.00`).
  - **4. Candado Estricto de Rol (`ROL_NODO`) y Cola Remota por Supabase (`L1 / L3 Terciaria ➔ L2 Madre`):**
    - Aunque todas las laptops descargan los mismos `.py` desde GitHub, solo `NIVEL_2_MADRE_HOTEL` (Laptop 2 Recepción) mantiene encendido el emisor SUNAT y el despachador WhatsApp (con hilo consumidor automático cada `3.5s` al recargar `sunat.py`).
    - `NIVEL_3_TERCIARIA` y `NIVEL_1_INGENIERIA` tienen bloqueada la emisión local y delegan toda orden vía `estado_sistema` (`sunat_orden_remota_<job_id>`) hacia la Laptop Madre.
- **✅ Fase 12 — Blindaje Post-Emisión SUNAT + Descarga Oficial PDF + Envío WhatsApp y Auto-Reinicio de Código en Madre (`4956bad`, `2366484`, `62f57df`, `a38f883`):** COMPLETADA (24–27/11/2026)
  - **`4956bad` (24/11):** Blindaje de 3 capas post-emisión sin doble POST a SUNAT y rescate automático de PDF para WhatsApp.
  - **`2366484` (26/11):** Rescate del PDF antes de despachar WhatsApp, soporte para nombres crudos SUNAT con RUC pegado (`PDF-DOC-E001-2143<RUC>.pdf`), registro permanente `Emision SUNAT` en `historial_movimientos` y timeout L1->Madre ampliado a `400s`.
  - **`62f57df` (26/11, verificado en vivo en L2 el 27/11 con `E001-2148`, `E001-2149` y `EB01-6917`):** Cierre preventivo de modales superpuestos de SUNAT (`Problemas al conectarse al servidor` / `Debe ingresar un correo`) antes de cada intento de `Descargar PDF` (PDF descargado a la primera y enviado por WhatsApp Baileys en 2–4 segundos).
  - **`a38f883` (26/11):** Reinicio real del proceso `main.py` cuando cambia algún `.py` en disco tras `git pull` (propio o de `motor_24x7.py`), recarga ordenada de `config`, `db` y `sunat_motor_playwright` en `motor_24x7.py`, y envío exclusivo del PDF oficial de `Descargar PDF` de SUNAT.

---

## 🚀 Protocolo Oficial para Pasar el Sistema a Otra Computadora Desde Cero (100% Git + Supabase, Cero ZIPs)

> **Regla de Oro:** Nunca se pasan carpetas ni bases `.db` por `.zip`. Toda computadora nueva (sea **Madre Recepción 24/7** o **Terciaria / Laptop 3+**) descarga el código con **`git clone`** directo desde GitHub y descarga toda la base de datos directo desde **Supabase**.
> En `Descargas` tienes también el botón visual **[INSTALAR_DESDE_CERO_LAPTOP_NUEVA.html](file:///C:/Users/josec/Downloads/INSTALAR_DESDE_CERO_LAPTOP_NUEVA.html)** que ejecuta exactamente este flujo con 1 clic.

### 💻 CASO A: Instalar en una **Laptop Terciaria / Visor / Administración (`NIVEL_3_TERCIARIA`)**
*(Para ver y operar el hotel en vivo desde cualquier otra PC sin abrir el WhatsApp del hotel ni duplicar el Vigilante 24/7)*:

1. **Si la PC está recién formateada (instalar Git + Python 3.12 + Librerías):**
   ```powershell
   winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
   winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --silent
   python -m pip install --upgrade pip fastapi uvicorn httpx pydantic gspread google-auth google-api-python-client requests cryptography
   ```
2. **Clonar desde GitHub e hidratar el 100% de los datos desde Supabase:**
   ```powershell
   git clone -b main https://github.com/eduardocalaniguevara-del/app_local_recepcion.git C:\Ovalo_Sistema_v2
   python C:\Ovalo_Sistema_v2\instalar_sistema_v2.py
   ```
   *(El script `instalar_sistema_v2.py` crea `.env` con `ROL_NODO=NIVEL_3_TERCIARIA`, `ACTIVAR_MOTOR_24X7=false`, `TIENE_QR_WSP_FISICO=false`, descarga todas las estancias/pagos de Supabase en `02_DB_sqlite_local\ovalo.db`, crea el ícono **Hostal Ovalo - Sistema v2** en el Escritorio y abre `:8080` en **`🟢 En vivo`**).*

---

### 🏢 CASO B: Instalar en una **Laptop Madre de Recepción 24/7 (`NIVEL_2_MADRE_HOTEL`)**
*(Para la PC principal de Recepción del hotel, que sí tiene el único QR físico de WhatsApp, lee `hostal.ovalo@gmail.com`, emite SUNAT y corre `motor_24x7.py` las 24 horas)*:

1. **Instalar Git + Python 3.12 + Librerías (si la PC es nueva):**
   ```powershell
   winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
   winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --silent
   python -m pip install --upgrade pip fastapi uvicorn httpx pydantic gspread google-auth google-api-python-client requests cryptography playwright
   ```
2. **Clonar desde GitHub e iniciar en modo `MADRE_L2` (`--madre`):**
   ```powershell
   git clone -b main https://github.com/eduardocalaniguevara-del/app_local_recepcion.git C:\Ovalo_Sistema_v2
   python C:\Ovalo_Sistema_v2\instalar_sistema_v2.py --madre
   ```
   *(Con `--madre`, configura `.env` con `ROL_NODO=NIVEL_2_MADRE_HOTEL`, `ACTIVAR_MOTOR_24X7=true`, `TIENE_QR_WSP_FISICO=true`, descarga toda la base de Supabase, crea el ícono en el Escritorio y deja encendido el mostrador `:8080` + el vigilante `05_GIT_vigilante_24x7/motor_24x7.py`).*
