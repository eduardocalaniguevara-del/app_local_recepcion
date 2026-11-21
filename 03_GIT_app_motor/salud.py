"""
03_GIT_app_motor/salud.py — Monitor ligero (~80 líneas) que reemplaza a los 25 archivos del antiguo Arnés.
Verifica solo lo vital: Aduana .gitignore (Estructura 01..05), SQLite local y alcance de Supabase.
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BASE_DIR, CORREO_RECEPCION_HOTEL, DATA_DIR, DB_PATH, ROL_NODO, SUPABASE_URL, ACTIVAR_MOTOR_24X7


def verificar_aduana_gitignore() -> dict:
    gi_path = BASE_DIR / ".gitignore"
    if not gi_path.exists():
        return {"ok": False, "detalle": "Falta archivo .gitignore"}
    texto = gi_path.read_text(encoding="utf-8", errors="ignore")
    reglas = {
        "borradores": "01_PRIV_borradores/" in texto,
        "data_db": "02_DB_sqlite_local/" in texto and "*.db" in texto,
        "env": ".env" in texto,
    }
    return {"ok": all(reglas.values()), "candados": reglas}


def verificar_sqlite_local() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        return {"ok": True, "estado": "PENDIENTE_FASE_2", "ruta": str(DB_PATH)}
    try:
        with sqlite3.connect(str(DB_PATH), timeout=3) as conn:
            res = conn.execute("PRAGMA integrity_check;").fetchone()
            integro = bool(res and res[0] == "ok")
            return {"ok": integro, "estado": "INTEGRO" if integro else "OBSERVADO", "ruta": str(DB_PATH)}
    except Exception as exc:
        return {"ok": False, "estado": "ERROR", "error": str(exc)}


def verificar_alcance_supabase() -> dict:
    try:
        req = urllib.request.Request(SUPABASE_URL, method="HEAD")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return {"ok": resp.status < 500, "http_status": resp.status, "alcance": "ONLINE"}
    except urllib.error.HTTPError as http_err:
        return {"ok": http_err.code < 500, "http_status": http_err.code, "alcance": "ONLINE"}
    except Exception as exc:
        return {"ok": False, "alcance": "OFFLINE", "error": str(exc)}


def reporte_salud() -> dict:
    aduana = verificar_aduana_gitignore()
    sqlite_st = verificar_sqlite_local()
    nube = verificar_alcance_supabase()
    salud_global = aduana["ok"] and sqlite_st["ok"]
    return {
        "ok": salud_global,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "nodo": ROL_NODO,
        "correo_hotel_bcp": CORREO_RECEPCION_HOTEL,
        "motor_24x7_activo_en_este_nodo": ACTIVAR_MOTOR_24X7,
        "aduana_gitignore": aduana,
        "sqlite_local": sqlite_st,
        "supabase_red": nube,
    }


if __name__ == "__main__":
    print(json.dumps(reporte_salud(), indent=2, ensure_ascii=False))
