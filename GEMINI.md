# Reglamento Maestro del Proyecto — `C:\Ovalo_Sistema_v2`

## 0. Modo Arquitecto Senior (Cero Complacencia) + Prioridad Visual (`🗺️`)
- **🗺️ Usuario Visual Primero:** Toda explicación de arquitectura, flujos o módulos debe apoyarse en el **Plano Interactivo v2** del Visor (`http://127.0.0.1:8788/?grupo=sistema_v2`) con zonas claras, flechas de recorrido y colores de canal.
- **Prohibido duplicar carpetas o entornos:** No existe entorno `8082` ni carpetas `Promover_a_Operativa`. Todo cambio se prueba aquí en `C:\Ovalo_Sistema_v2` antes de hacer `git push` a `main`.
- **Sistema de Nombres con Código de Reconocimiento Escalable (`01` al `06`):**
  1. **`01_PRIV_borradores\`**: 🔒 Privado local (pruebas y apuntes con agentes IA; bloqueado en `.gitignore`).
  2. **`02_PRIV_correo_bcp\`**: 🔒 Privado local (extractor de Gmail personal BCP/Maelen → Supabase; bloqueado en `.gitignore`).
  3. **`03_DB_sqlite_local\`**: 🟢 Base SQLite local `ovalo.db` (se crea sola en cualquier laptop y sincroniza con **Supabase**; bloqueada en `.gitignore`).
  4. **`04_GIT_app_motor\`**: 🟣 Los 8 módulos Python del sistema (`config.py`, `db.py`, `estancias.py`, `yape_bcp.py`, `sunat.py`, `whatsapp.py`, `sync_nube.py`, `salud.py`). Viaja por **GitHub `main`** a todas las laptops.
  5. **`05_GIT_web_mostrador\`**: 🟣 Pantallas HTML/CSS/JS. Viaja por **GitHub `main`** a todas las laptops.
  6. **`06_GIT_vigilante_24x7\`**: 🟣 `motor_24x7.py`. Se programa en Laptop 1, viaja por **GitHub `main`** a todas las laptops (L2, L3...), y **solo se mantiene encendido 24/7 donde `.env` diga `ACTIVAR_MOTOR_24X7=true`**.
