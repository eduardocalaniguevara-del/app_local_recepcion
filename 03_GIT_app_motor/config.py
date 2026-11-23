"""
03_GIT_app_motor/config.py — Configuración unificada y Multi-Hotel (Estructura 01..05):
- NIVEL_1_INGENIERIA (Tu Laptop 1): Editor de código (git push) + Terminal Terciaria (recibe datos de Supabase).
  No vincula tu WhatsApp ni tu correo personal.
- NIVEL_2_MADRE_HOTEL (Laptop 2 Recepción 24/7): 100% autónoma. Tiene el único QR de WhatsApp de Recepción,
  lee su propio correo del hotel (hostal.ovalo@gmail.com con copia reenviada de BCP), emite SUNAT y envía por su WSP.
- NIVEL_3_TERCIARIA (Laptops 3+): Solo recibe información desde Supabase sin vincular WhatsApp ni correo.
"""
import json
import os
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BASE_APP_DIR = BASE_DIR
BORRADORES_DIR = BASE_DIR / "01_PRIV_borradores"
DATA_DIR = BASE_DIR / "02_DB_sqlite_local"
APP_MOTOR_DIR = BASE_DIR / "03_GIT_app_motor"
WEB_DIR = BASE_DIR / "04_GIT_web_mostrador"
STATIC_DIR = WEB_DIR
VIGILANTE_DIR = BASE_DIR / "05_GIT_vigilante_24x7"
DB_PATH = DATA_DIR / "ovalo.db"
PERFIL_HOTEL_PATH = DATA_DIR / "hotel_perfil.json"

# Crear automáticamente las carpetas locales (01_PRIV y 02_DB) en cualquier laptop donde arranque el sistema
BORRADORES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)


def cargar_env_local() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    raw_text = env_file.read_text(encoding="utf-8-sig")
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip().lstrip("\ufeff"), v.strip())


def _cargar_credenciales_supabase_locales() -> dict:
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
APP_HOST = "0.0.0.0"
APP_PORT = PUERTO_WEB
MODO_PRUEBA = False
SYNC_INTERVAL_SECONDS = 25

NOMBRE_HOTEL = os.environ.get("NOMBRE_HOTEL", "Hotel El Óvalo Mariátegui")
CORREO_RECEPCION_HOTEL = os.environ.get("CORREO_RECEPCION_HOTEL", "hostal.ovalo@gmail.com")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8")
COL_TIPO_ADICIONAL = "BA"
COL_TIPO_ADICIONAL_IDX = 52
FORMULA_SNACKS_EN_S2 = False
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDENTIAL_CANDIDATES = [
    DATA_DIR / "credentials.json",
    Path(r"C:\Hostal_Sistema\config\credentials.json"),
    Path.home() / "Downloads" / "trabajo" / "Sistema de agentes IA" / "HostalAI" / "config" / "credentials.json",
]


def obtener_ruta_credenciales() -> str:
    for p in CREDENTIAL_CANDIDATES:
        if p.exists():
            return str(p)
    return str(CREDENTIAL_CANDIDATES[-1])


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

_DEFAULT_SB_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZtbGZxdXB3c2N5bW9zdndubnp4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ0MjYzNzUsImV4cCI6MjExMDAwMjM3NX0."
    "pi3f4dEklYT28oBxYhktYlWwGiLkFdMqhqSq6-gfZ2g"
)
_DEFAULT_SB_TOKEN = "ovalo_master_sync_2026_k9x"

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    _sb_local.get("supabase_url", "https://fmlfqupwscymosvwnnzx.supabase.co"),
).rstrip("/")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    _sb_local.get("supabase_anon_key") or _sb_local.get("supabase_publishable_key") or _DEFAULT_SB_KEY,
)
HOSTAL_SYNC_TOKEN = os.environ.get(
    "HOSTAL_SYNC_TOKEN",
    _sb_local.get("hostal_sync_token", _DEFAULT_SB_TOKEN),
)
SUPABASE_CONFIGURADO = bool(SUPABASE_URL and SUPABASE_KEY and "REEMPLAZAR" not in SUPABASE_KEY)
SUPABASE_ENABLED = SUPABASE_CONFIGURADO
SUPABASE_ANON_KEY = SUPABASE_KEY
SUPABASE_SYNC_TOKEN = HOSTAL_SYNC_TOKEN
SUPABASE_SANDBOX_CONNECTED = False

RUTA_HOSTAL_SISTEMA = Path(os.environ.get("RUTA_HOSTAL_SISTEMA", r"C:\Hostal_Sistema"))
HOSTAL_SISTEMA_DIR = RUTA_HOSTAL_SISTEMA
SCRIPTS_HOSTAL_DIR = HOSTAL_SISTEMA_DIR / "scripts"
EJECUTAR_REMOTO_SCRIPT = HOSTAL_SISTEMA_DIR / "ejecutar_remoto.py"
EMITIR_COMPROBANTE_SCRIPT = SCRIPTS_HOSTAL_DIR / "emitir_comprobante_sunat.py"
BRAZO_WSP_SCRIPT = SCRIPTS_HOSTAL_DIR / "brazo_wsp.py"
REGLAS_AUDITORIA_JSON = HOSTAL_SISTEMA_DIR / "reglas_auditoria.json"
DESCARGAS_RECEPCION_DIR = HOSTAL_SISTEMA_DIR / "descargas_recepcion"

APISPERU_TOKEN = os.environ.get(
    "APISPERU_TOKEN",
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJlbWFpbCI6ImVkdWFyZG9jYWxhbmlndWV2YXJhQGdtYWlsLmNvbSIsImp0aSI6ImIxMDM1MmRkY2VmZDU4MDkifQ.0sX69ADETj8CXcs_HLn9_n9ioe6KW2IwLKo_ot5XtiA",
)
APISPERU_DNI_URL = "https://dniruc.apisperu.com/api/v1/dni"
APISPERU_RUC_URL = "https://dniruc.apisperu.com/api/v1/ruc"
