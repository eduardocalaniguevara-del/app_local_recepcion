# Reglamento Maestro del Proyecto — `C:\Ovalo_Sistema_v2`

## 0. Modo Arquitecto Senior (Cero Complacencia) + Prioridad Visual (`🗺️`)
- **🗺️ Usuario Visual Primero:** Toda explicación de arquitectura, flujos o módulos debe apoyarse en el **Plano Interactivo v2** del Visor (`http://127.0.0.1:8788/?grupo=sistema_v2`) con zonas claras, flechas de recorrido y colores de canal.
- **Prohibido duplicar carpetas o entornos:** No existe entorno `8082` ni carpetas `Promover_a_Operativa`. Todo cambio se prueba aquí en `C:\Ovalo_Sistema_v2` antes de hacer `git push` a `main`.

## 1. Regla de Oro Multi-Hotel: Autonomía Total de Laptop 2 y Doble Rol de Laptop 1
1. **🏢 Laptop 2 Recepción (`ROL_NODO=NIVEL_2_MADRE_HOTEL`) tiene TODO para trabajar sola 24/7:**
   - **WhatsApp de Recepción:** Aquí (y solo aquí) se escanea el QR de WhatsApp del hotel.
   - **Facturación SUNAT + Envío WSP:** Aquí emite las facturas/boletas en SUNAT y las envía automáticamente por su propio WhatsApp.
   - **Correo Propio del Hotel (`CORREO_RECEPCION_HOTEL=hostal.ovalo@gmail.com`):** Recibe copia reenviada automáticamente de las notificaciones bancarias BCP (Cuenta `8023` Maelen) y las lee las 24 horas aunque Laptop 1 esté apagada.
   - **Vigilante 24/7 (`05_GIT_vigilante_24x7/motor_24x7.py`):** Encendido únicamente aquí. Todo lo que procesa lo sube en vivo a **Supabase**.
2. **💻 Tu Laptop 1 (`ROL_NODO=NIVEL_1_INGENIERIA`) funciona únicamente de 2 formas (CERO WhatsApp personal y CERO Gmail personal vinculado):**
   - **Modo A — Editor de Código:** Programa mejoras con Antigravity (usando `01_PRIV_borradores\` para borradores locales) y las publica por **GitHub (`git push`)**.
   - **Modo B — Terminal Terciaria / Visor:** Recibe toda la información lista desde la Laptop 2 vía **Supabase** (`02_DB_sqlite_local\ovalo.db`).
3. **🏨 Escalabilidad a Otro Hotel:** Cada nuevo hotel tiene su propia Laptop Madre 24/7 en Recepción (con su propio QR WhatsApp, su propio correo de recepción y su propio `.env`), y tu Laptop 1 actúa como Editor y Terciaria sin mezclar cuentas personales.

## 2. Sistema de 5 Carpetas con Código de Reconocimiento (`01` al `05`)
1. **`01_PRIV_borradores\`**: 🔒 Privado local de tu Laptop 1 (pruebas y apuntes con agentes IA; bloqueado en `.gitignore`).
2. **`02_DB_sqlite_local\`**: 🟢 Base SQLite local `ovalo.db` (se crea sola en cualquier laptop y sincroniza con **Supabase**; bloqueada en `.gitignore`).
3. **`03_GIT_app_motor\`**: 🟣 Los 8 módulos Python del sistema (`config.py`, `db.py`, `estancias.py`, `yape_bcp.py`, `sunat.py`, `whatsapp.py`, `sync_nube.py`, `salud.py`). Viaja por **GitHub `main`**.
4. **`04_GIT_web_mostrador\`**: 🟣 Pantallas HTML/CSS/JS del mostrador. Viaja por **GitHub `main`**.
5. **`05_GIT_vigilante_24x7\`**: 🟣 `motor_24x7.py`. Viaja por **GitHub `main`** y solo corre encendido 24/7 en `NIVEL_2_MADRE_HOTEL`.
