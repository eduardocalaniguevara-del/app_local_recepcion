"""
app/config.py — Configuración unificada sin rutas fijas C:\\Users\\...
Funciona idéntico en Laptop 1 (Admin) y Laptop 2 (Recepción 24/7).
"""
import os
from pathlib import Path

# Raíz dinámica del proyecto (C:\Ovalo_Sistema_v2)
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"
PRIVADO_L1_DIR = BASE_DIR / "privado_l1"
BORRADORES_DIR = BASE_DIR / "borradores"
DB_PATH = DATA_DIR / "ovalo.db"


def cargar_env_local() -> None:
    """Lee el archivo .env local si existe, sin depender de librerías externas."""
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

ROL_NODO = os.environ.get("ROL_NODO", "LAPTOP_1_ADMIN")
PUERTO_WEB = int(os.environ.get("PUERTO_WEB", "8080"))
ES_LAPTOP_2 = ROL_NODO.upper() == "LAPTOP_2_RECEPCION"
ACTIVAR_MOTOR_24X7 = os.environ.get("ACTIVAR_MOTOR_24X7", "false").lower() == "true" or ES_LAPTOP_2
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fmlfqupwscymosvwnnzx.supabase.co")
RUTA_HOSTAL_SISTEMA = Path(os.environ.get("RUTA_HOSTAL_SISTEMA", r"C:\Hostal_Sistema"))
