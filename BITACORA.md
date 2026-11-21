# 📓 Bitácora Viva de Construcción — `C:\Ovalo_Sistema_v2`
> **Actualizado:** 21/11/2026 — Monitoreado en vivo desde `http://127.0.0.1:8788/?grupo=sistema_v2`

---

## 🚦 Estado General por Fases

- **✅ Fase 0 — Regla Global Modo Arquitecto Senior + Prioridad Visual (`/learn`):** COMPLETADA
  - Grabada en `C:\Users\josec\.gemini\config\AGENTS.md`.
  - Retirado el bloque Coach repetitivo para respuestas limpias.
  - Añadida la directiva `🗺️ Prioridad Visual (Usuario Visual)`.
- **✅ Fase 1 — Esqueleto Limpio `C:\Ovalo_Sistema_v2` + Aduana `.gitignore` + Plano Interactivo (`:8788`):** COMPLETADA
  - Creadas las 3 zonas físicas en `C:\Ovalo_Sistema_v2`:
    - **🔒 Zona 1 (Privada L1, bloqueada en Git):** `borradores\`, `privado_l1\`, `data\`.
    - **📐 Zona 2 (Planos y Aduana):** `GEMINI.md`, `BITACORA.md`, `.gitignore`, `.env.example`.
    - **⚙️ Zona 3 (Esqueleto Motor):** `main.py`, `app\config.py`, `app\salud.py` (~80 líneas), `web\`, `scripts_24x7\motor_24x7.py`.
  - Inicializado repositorio Git en rama **`main`** con verificación de que `.gitignore` bloquea `borradores/`, `privado_l1/`, `data/` y `.env`.
  - Conectado el **`🗺️ Plano Interactivo v2 (Mapa Visual)`** en `http://127.0.0.1:8788/?grupo=sistema_v2` con auto-actualización cada 2 segundos.
- **⏳ Fase 2 — Base SQLite Limpia (`app/db.py`) + Puente Supabase (`app/sync_nube.py`):** PENDIENTE (Siguiente paso)
- **⏳ Fase 3 — Operación de Recepción (`app/estancias.py` + `web/`):** PENDIENTE
- **⏳ Fase 4 — Conciliación Bancaria (`privado_l1/` + `app/yape_bcp.py` sin `WSP +5m`):** PENDIENTE
- **⏳ Fase 5 — SUNAT (`app/sunat.py`) + WhatsApp (`app/whatsapp.py`) + Activación `motor_24x7.py`:** PENDIENTE
- **⏳ Fase 6 — Clon en Solo Lectura en Laptop 2 y Retiro de Carpetas Antiguas:** PENDIENTE

---

## 🏛️ Decisiones Arquitectónicas Cerradas

1. **Carpeta única de trabajo (`C:\Ovalo_Sistema_v2`):** No se usan subcarpetas anidadas de proyecto. Los borradores con agentes viven en `C:\Ovalo_Sistema_v2\borradores\` bloqueados por `.gitignore`.
2. **Separación de ejecución L1 vs L2 (`scripts_24x7\motor_24x7.py`):** El archivo se programa y versiona en Git desde Laptop 1, pero tiene candado por rol de nodo (`ROL_NODO=LAPTOP_2_RECEPCION`) para mantenerse encendido 24/7 únicamente en Laptop 2.
3. **Reemplazo del Arnés de 25 archivos:** Sustituido desde esta Fase 1 por `app/salud.py` (~80 líneas), que chequea WebApp, SQLite, Supabase y candados `.gitignore`.
