# Reglamento Maestro del Proyecto — `C:\Ovalo_Sistema_v2`

## 0. Modo Arquitecto Senior (Cero Complacencia) + Prioridad Visual (`🗺️`)
- **🗺️ Usuario Visual Primero:** Toda explicación de arquitectura, flujos o módulos debe apoyarse en el **Plano Interactivo v2** del Visor (`http://127.0.0.1:8788/?grupo=sistema_v2`) con zonas claras, flechas de recorrido y colores de canal.
- **Prohibido duplicar carpetas o entornos:** No existe entorno `8082` ni carpetas `Promover_a_Operativa`. Todo cambio se prueba aquí en `C:\Ovalo_Sistema_v2` (Laptop 1) antes de hacer `git push` a `main`.
- **1 Canal = 1 Propósito:**
  1. **Código público (`app/`, `web/`, `scripts_24x7/`, `main.py`):** Viaja únicamente por **GitHub (`ovalo-sistema-v2` / rama `main`)**. Laptop 2 descarga en **Solo Lectura (`git pull`)**.
  2. **Taller de agentes y Gmail personal (`borradores/`, `privado_l1/`):** Bloqueados en `.gitignore`. **Jamás** salen de Laptop 1.
  3. **Datos vivos (`data/ovalo.db`):** Sincronizan bidireccionalmente con **Supabase (`hostal-ovalo`)**, nunca por Git ni Syncthing.
  4. **PDFs SUNAT:** Viajan por **Syncthing** en `C:\Hostal_Sistema`, separados de esta carpeta de código.

## 1. Disciplina de Código Modular
- Cada archivo dentro de `app/` tiene una sola responsabilidad clara y corta:
  - `app/config.py`: Rutas relativas dinámicas (cero `C:\Users\josec` escrito en duro).
  - `app/db.py`: Esquema SQLite limpio con índices `UNIQUE` anti-duplicados.
  - `app/estancias.py`: Habitaciones, check-in, pagos y vitrina tienda.
  - `app/yape_bcp.py`: Conciliación bancaria limpia (sin ruido `WSP +5m`).
  - `app/sunat.py`: Emisión y consulta de comprobantes SUNAT.
  - `app/whatsapp.py`: Cola y lectura de WhatsApp Recepción.
  - `app/sync_nube.py`: Puente bidireccional SQLite ↔ Supabase.
  - `app/salud.py`: Monitor ligero (~80 líneas) que reemplaza al antiguo Arnés.
- Al cerrar cada hito o cambio relevante, actualizar `BITACORA.md` para que el Visor `:8788` lo refleje en vivo.
