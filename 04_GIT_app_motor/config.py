"""
04_GIT_app_motor/config.py — Configuración unificada para la Arquitectura Hotelera de 3 Niveles:
- NIVEL_1_INGENIERIA: Tu Laptop 1 (crea código, corre 02_PRIV_correo_bcp -> Supabase).
- NIVEL_2_MADRE_HOTEL: Laptop 2 Recepción 24/7 (único QR físico de WhatsApp + SUNAT + motor_24x7).
- NIVEL_3_TERCIARIA: Laptops 3, 4... (reciben todo en vivo desde Supabase sin escanear QR).
"""
import json
import os
import socket
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


def _cargar_credenciales_supabase_locales() -> dict:
    """
    Si el archivo .env aún no tiene SUPABASE_KEY, busca en las ubicaciones locales
    seguras de la laptop (fuera de Git) para que funcione sin exponer llaves en código.
    """
    candidatos = [
        BASE_DIR / "supabase_config.json",
        Path(r"C:\Hostal_Sistema\leemeantrigravityrecepcion\supabase_config.json"),
        Path.home() / "Downloads" / "leemeantrigravityrecepcion" / "supabase_config.json",
        Path.home() / "Downloads" / "trabajo" / "Sistema de agentes IA" / "supabase_config.json",
    ]
    for p in candidatos:
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


cargar_env_local()
_sb_local = _cargar_credenciales_supabase_locales()

ROL_NODO = os.environ.get("ROL_NODO", "NIVEL_1_INGENIERIA").upper()
EQUIPO_ID = os.environ.get("EQUIPO_ID", socket.gethostname() or ROL_NODO)
PUERTO_WEB = int(os.environ.get("PUERTO_WEB", "8080"))

ES_NIVEL_1_INGENIERIA = ROL_NODO in ("NIVEL_1_INGENIERIA", "LAPTOP_1_ADMIN")
ES_NIVEL_2_MADRE_HOTEL = ROL_NODO in ("NIVEL_2_MADRE_HOTEL", "LAPTOP_2_RECEPCION")
ES_NIVEL_3_TERCIARIA = ROL_NODO.startswith("NIVEL_3")

ACTIVAR_MOTOR_24X7 = (
    os.environ.get("ACTIVAR_MOTOR_24X7", "false").lower() == "true"
    or ES_NIVEL_2_MADRE_HOTEL
)
TIENE_QR_WSP_FISICO = (
    os.environ.get("TIENE_QR_WSP_FISICO", "false").lower() == "true"
    or ES_NIVEL_2_MADRE_HOTEL
)

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    _sb_local.get("supabase_url", "https://fmlfqupwscymosvwnnzx.supabase.co"),
).rstrip("/")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    _sb_local.get("supabase_anon_key") or _sb_local.get("supabase_publishable_key") or "",
)
HOSTAL_SYNC_TOKEN = os.environ.get(
    "HOSTAL_SYNC_TOKEN",
    _sb_local.get("hostal_sync_token", ""),
)
SUPABASE_CONFIGURADO = bool(SUPABASE_URL and SUPABASE_KEY and "REEMPLAZAR" not in SUPABASE_KEY)
RUTA_HOSTAL_SISTEMA = Path(os.environ.get("RUTA_HOSTAL_SISTEMA", r"C:\Hostal_Sistema"))
