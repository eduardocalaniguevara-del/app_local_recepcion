"""
03_GIT_app_motor/salud.py — Monitor ligero + Seguidor en Vivo de Carpetas Multi-Laptop (vía Supabase).
Cada laptop (Nivel 1, Nivel 2 Hotel Madre, Nivel 3 Terciarias) escanea sus 5 carpetas (01..05) y archivos .py,
y publica su huella en vivo en Supabase (`estado_sistema` -> `seguidor_v2_<ROL_NODO>`) para que el
Plano Interactivo (:8788) muestre en tiempo real cuando una carpeta o .py se crea o actualiza en otra laptop.
"""
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    ACTIVAR_MOTOR_24X7,
    BASE_DIR,
    CORREO_RECEPCION_HOTEL,
    DATA_DIR,
    DB_PATH,
    EQUIPO_ID,
    HOSTAL_SYNC_TOKEN,
    ROL_NODO,
    SUPABASE_CONFIGURADO,
    SUPABASE_KEY,
    SUPABASE_URL,
    TIENE_QR_WSP_FISICO,
)


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
            size_kb = round(DB_PATH.stat().st_size / 1024, 1)
            return {
                "ok": integro,
                "estado": "INTEGRO" if integro else "OBSERVADO",
                "ruta": str(DB_PATH),
                "size_kb": size_kb,
            }
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


def _obtener_git_head_local() -> dict:
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--pretty=format:%h|%s|%ci"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.strip().split("|", 2)
            return {
                "commit": parts[0],
                "mensaje": parts[1] if len(parts) > 1 else "",
                "fecha": parts[2] if len(parts) > 2 else "",
            }
    except Exception:
        pass
    return {"commit": "sin-git", "mensaje": "", "fecha": ""}


def escanear_carpetas_nodo_local() -> dict:
    """
    Escanea las 5 carpetas (01..05) y el archivo .env de esta laptop para informar
    qué archivos .py / .html / .db existen y cuál fue el último archivo editado o actualizado.
    """
    objetivos = {
        "01_PRIV": BASE_DIR / "01_PRIV_borradores",
        "env": BASE_DIR / ".env",
        "02_DB": BASE_DIR / "02_DB_sqlite_local",
        "03_GIT": BASE_DIR / "03_GIT_app_motor",
        "04_GIT": BASE_DIR / "04_GIT_web_mostrador",
        "05_GIT": BASE_DIR / "05_GIT_vigilante_24x7",
    }
    carpetas_info = {}
    ultimo_py_editado = {"archivo": "", "mtime_iso": "", "mtime_ts": 0.0}

    for clave, ruta in objetivos.items():
        if not ruta.exists():
            carpetas_info[clave] = {"exists": False, "items": 0, "latest_file": "", "latest_mtime": ""}
            continue
        if ruta.is_file():
            st = ruta.stat()
            carpetas_info[clave] = {
                "exists": True,
                "items": 1,
                "size_kb": round(st.st_size / 1024, 1),
                "latest_file": ruta.name,
                "latest_mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            }
            continue

        archivos = [
            f for f in ruta.rglob("*")
            if f.is_file() and "__pycache__" not in str(f)
        ]
        latest_f = ""
        latest_ts = 0.0
        total_bytes = 0
        for f in archivos:
            try:
                fst = f.stat()
                total_bytes += fst.st_size
                if fst.st_mtime > latest_ts:
                    latest_ts = fst.st_mtime
                    latest_f = f.name
                if f.suffix in (".py", ".html", ".js") and fst.st_mtime > ultimo_py_editado["mtime_ts"]:
                    ultimo_py_editado = {
                        "archivo": f"{ruta.name}/{f.name}",
                        "mtime_iso": datetime.fromtimestamp(fst.st_mtime).isoformat(timespec="seconds"),
                        "mtime_ts": fst.st_mtime,
                    }
            except Exception:
                continue

        carpetas_info[clave] = {
            "exists": True,
            "items": len(archivos),
            "size_kb": round(total_bytes / 1024, 1),
            "latest_file": latest_f,
            "latest_mtime": datetime.fromtimestamp(latest_ts).isoformat(timespec="seconds") if latest_ts > 0 else "",
        }

    git_info = _obtener_git_head_local()
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "rol_nodo": ROL_NODO,
        "equipo_id": EQUIPO_ID,
        "motor_24x7_activo": ACTIVAR_MOTOR_24X7,
        "tiene_qr_wsp_fisico": TIENE_QR_WSP_FISICO,
        "correo_hotel_bcp": CORREO_RECEPCION_HOTEL,
        "git_commit": git_info["commit"],
        "git_mensaje": git_info["mensaje"],
        "ultimo_codigo_modificado": ultimo_py_editado["archivo"],
        "ultimo_codigo_mtime": ultimo_py_editado["mtime_iso"],
        "carpetas": carpetas_info,
    }


def publicar_seguidor_carpetas_supabase() -> dict:
    """
    Envía la radiografía en vivo de las carpetas (01..05) y archivos .py de esta laptop
    hacia Supabase (`estado_sistema` -> `seguidor_v2_<ROL_NODO>`) para que tu Visualizador (:8788)
    muestre en tiempo real los cambios en Laptop 2 (Zona 5) y Laptops Terciarias (Zona 6).
    """
    payload = escanear_carpetas_nodo_local()
    clave_rol = f"seguidor_v2_{ROL_NODO}"
    eq_clean = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(EQUIPO_ID or ROL_NODO).upper())
    clave_equipo = f"seguidor_v2_nodo_{eq_clean}"
    ahora_iso = payload["timestamp"]
    valor_json = json.dumps(payload, ensure_ascii=False)

    # 1. Guardar en SQLite local (estado_sistema) tanto por rol como por equipo individual
    try:
        if DB_PATH.exists():
            with sqlite3.connect(str(DB_PATH), timeout=5) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS estado_sistema (
                        clave TEXT PRIMARY KEY,
                        valor TEXT NOT NULL DEFAULT '',
                        updated_at TEXT DEFAULT ''
                    );
                    """
                )
                for ck in (clave_rol, clave_equipo):
                    conn.execute(
                        "INSERT OR REPLACE INTO estado_sistema (clave, valor, updated_at) VALUES (?, ?, ?)",
                        (ck, valor_json, ahora_iso),
                    )
                conn.commit()
    except Exception:
        pass

    # 2. Subir directo por HTTP REST a Supabase para visibilidad instantánea (<1s) en el Mapa :8788
    subido_nube = False
    if SUPABASE_CONFIGURADO:
        url = f"{SUPABASE_URL}/rest/v1/estado_sistema?on_conflict=clave"
        rows_post = [
            {"clave": clave_rol, "valor": valor_json, "updated_at": ahora_iso},
            {"clave": clave_equipo, "valor": valor_json, "updated_at": ahora_iso},
        ]
        body = json.dumps(rows_post, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "x-hostal-sync-token": HOSTAL_SYNC_TOKEN,
            "x-hostal-equipo": EQUIPO_ID,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                subido_nube = 200 <= resp.status < 300
        except Exception:
            subido_nube = False

    return {"ok": True, "clave": clave_rol, "clave_equipo": clave_equipo, "subido_supabase": subido_nube, "seguidor": payload}


def reporte_salud() -> dict:
    aduana = verificar_aduana_gitignore()
    sqlite_st = verificar_sqlite_local()
    nube = verificar_alcance_supabase()
    seg = publicar_seguidor_carpetas_supabase()
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
        "seguidor_carpetas_v2": seg,
    }


if __name__ == "__main__":
    print(json.dumps(reporte_salud(), indent=2, ensure_ascii=False))
