"""
04_GIT_app_motor/config.py — Configuración unificada y escalable (Laptop 1, Laptop 2, Laptop 3...).
Cero rutas fijas C:\\Users\\...; el rol de cada laptop lo define su propio archivo .env local.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BORRADORES_DIR = BASE_DIR / "01_PRIV_borradores"
PRIVADO_BCP_DIR = BASE_DIR / "02_PRIV_correo_bcp"
DATA_DIR = BASE_DIR / "03_DB_sqlite_local"
APP_MOTOR_DIR = BASE_DIR / "04_GIT_app_motor"
WEB_DIR = BASE_DIR / "05_GIT_web_mostrador"
VIGILANTE_DIR = BASE_DIR / "06_GIT_vigilante_24x7"
DB_PATH = DATA_DIR / "ovalo.db"


def cargar_env_local() -> None:
    """Lee el archivo .env local de esta laptop (si existe)."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


cargar_env_local()

# En cualquier laptop (L1, L2, L3...), .env define el nombre del nodo y si enciende el vigilante 24/7
ROL_NODO = os.environ.get("ROL_NODO", "LAPTOP_1_ADMIN")
PUERTO_WEB = int(os.environ.get("PUERTO_WEB", "8080"))
ES_NODO_RECEPCION_24X7 = ROL_NODO.upper() == "LAPTOP_2_RECEPCION"
ACTIVAR_MOTOR_24X7 = os.environ.get("ACTIVAR_MOTOR_24X7", "false").lower() == "true" or ES_NODO_RECEPCION_24X7
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fmlfqupwscymosvwnnzx.supabase.co")
RUTA_HOSTAL_SISTEMA = Path(os.environ.get("RUTA_HOSTAL_SISTEMA", r"C:\Hostal_Sistema"))
