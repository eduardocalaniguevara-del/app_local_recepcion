"""
05_GIT_vigilante_24x7/motor_24x7.py — Vigilante Automático 24/7 (Arquitectura Multi-Hotel 01..05)
REGLA ARQUITECTÓNICA:
- Se programa en tu Laptop 1 (NIVEL_1_INGENIERIA) y viaja por GitHub a todos los equipos.
- SOLO se mantiene corriendo 24/7 en la Laptop Madre del Hotel (NIVEL_2_MADRE_HOTEL / ACTIVAR_MOTOR_24X7=true),
  que tiene:
    1) El único QR físico de WhatsApp de Recepción.
    2) El correo propio del hotel (hostal.ovalo@gmail.com) con copia reenviada de BCP.
    3) La emisión de facturas/boletas SUNAT y envío por su WhatsApp.
    4) Descarga automática en SOLO LECTURA desde GitHub (git pull) sin tocar 01_PRIV ni 02_DB.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Evita que motor_24x7.py arranque un segundo hilo rápido de consumo SUNAT en paralelo al servidor :8080
os.environ["OVALO_PROCESO_ROL"] = "MOTOR_24X7"

BASE_DIR = Path(__file__).resolve().parent.parent
MOTOR_DIR = BASE_DIR / "03_GIT_app_motor"
if str(MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(MOTOR_DIR))

from config import ACTIVAR_MOTOR_24X7, ES_NIVEL_2_MADRE_HOTEL, ROL_NODO, TIENE_QR_WSP_FISICO
from db import get_connection
from salud import publicar_seguidor_carpetas_supabase, reporte_salud
from sunat_motor_playwright import revisar_y_descargar_faltantes_sunat_incremental
from sync_nube import instalar_triggers_outbox, pull_from_supabase, push_to_supabase
import sunat as sunat_svc
import whatsapp as wsp
import yape_bcp as ym


def _servidor_8080_activo() -> bool:
    """Devuelve True si el servidor web de recepción (:8080) está activo y consumiendo la cola rápida SUNAT."""
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=0.4):
            return True
    except Exception:
        return False


def actualizar_codigo_git_solo_lectura() -> dict:
    """
    En Laptop 2 (Recepción) o Laptops Terciarias: descarga en SOLO LECTURA cualquier mejora
    que hayas subido desde tu Laptop 1 con `git push`, sin tocar 01_PRIV_*, 02_DB_* ni .env.
    Si el commit cambió, recarga los módulos en memoria y reinicia el proceso para aplicar mejoras al instante.
    """
    if not (BASE_DIR / ".git").exists():
        return {"ok": False, "detalle": "Repositorio Git no inicializado"}
    try:
        if hasattr(sunat_svc, "hay_trabajo_sunat_en_curso") and sunat_svc.hay_trabajo_sunat_en_curso():
            return {"ok": True, "skipped": True, "motivo": "EMISION_SUNAT_EN_CURSO"}
    except Exception:
        pass
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5, creationflags=flags
        ).stdout.strip()
        proc = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=flags,
        )
        after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5, creationflags=flags
        ).stdout.strip()
        salida = (proc.stdout or proc.stderr or "").strip()
        if before and after and before != after:
            import importlib
            # Orden de dependencias: config/db -> motor Playwright -> servicio SUNAT (antes faltaba el motor y fallaba en silencio)
            mods_reload = []
            for nombre in ("config", "db", "sunat_motor_playwright"):
                m_extra = sys.modules.get(nombre)
                if m_extra is not None:
                    mods_reload.append(m_extra)
            mods_reload.extend((ym, wsp, sunat_svc))
            for mod in mods_reload:
                try:
                    importlib.reload(mod)
                except Exception as exc_rl:
                    print(f"[motor_24x7] reload {getattr(mod, '__name__', mod)} falló: {exc_rl}", flush=True)
            try:
                diff_files = subprocess.run(
                    ["git", "diff", "--name-only", before, after],
                    cwd=str(BASE_DIR),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=flags,
                ).stdout
                if ("main.py" in (diff_files or "")) or ("lanzador_escritorio.py" in (diff_files or "")) or not _servidor_8080_activo():
                    pyw = Path(sys.executable).with_name("pythonw.exe")
                    exe = str(pyw if pyw.exists() else sys.executable)
                    subprocess.Popen(
                        [exe, str(BASE_DIR / "lanzador_escritorio.py"), "--no-browser", "--restart"],
                        cwd=str(BASE_DIR),
                        creationflags=flags,
                    )
                if ("--once" not in sys.argv) and any(ln.strip().endswith(".py") for ln in (diff_files or "").splitlines()):
                    pyw = Path(sys.executable).with_name("pythonw.exe")
                    exe = str(pyw if pyw.exists() else sys.executable)
                    print(f"[motor_24x7] Código cambió {before[:7]} -> {after[:7]}; reiniciando motor_24x7 silencioso.", flush=True)
                    subprocess.Popen(
                        [exe, str(BASE_DIR / "05_GIT_vigilante_24x7" / "motor_24x7.py")],
                        cwd=str(BASE_DIR),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags,
                    )
                    os._exit(0)
            except Exception:
                pass
        return {"ok": proc.returncode == 0, "changed": before != after, "commit": after[:7], "salida": salida[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _verificar_revisor_incremental_sunat_8am() -> dict:
    """
    Ejecuta `revisar_y_descargar_faltantes_sunat_incremental(dias_atras=7)` automáticamente
    1 sola vez al día a partir de las 08:00 AM (únicamente en `ES_NIVEL_2_MADRE_HOTEL`),
    guardando la última fecha ejecutada en `estado_sistema` (`sunat_revisor_incremental_ult_fecha`).
    """
    if not ES_NIVEL_2_MADRE_HOTEL:
        return {"ok": False, "status": "SKIPPED_NO_MADRE"}
    ahora = datetime.now()
    if ahora.hour < 8:
        return {"ok": True, "status": "ANTES_DE_8AM"}

    hoy_ymd = ahora.strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT valor FROM estado_sistema WHERE clave = 'sunat_revisor_incremental_ult_fecha'"
        ).fetchone()
        ult_fecha = str(row["valor"] or "").strip() if row else ""
    finally:
        conn.close()

    if ult_fecha == hoy_ymd:
        return {"ok": True, "status": "YA_EJECUTADO_HOY", "fecha": hoy_ymd}

    return revisar_y_descargar_faltantes_sunat_incremental(dias_atras=7)


def ejecutar_ronda_integral() -> dict:
    """Ejecuta un ciclo completo de sincronización, conciliación Yape local, revisor SUNAT 8AM, WhatsApp y salud."""
    instalar_triggers_outbox()
    up = push_to_supabase()
    down = pull_from_supabase()

    res_yape = {"ok": True}
    try:
        # En Laptop 2 Madre (hostal.ovalo@gmail.com): lee notificaciones BCP 8023 reenviadas y concilia Yape local
        if ES_NIVEL_2_MADRE_HOTEL:
            ym.sincronizar_transferencias_maelen_correo()
        ym.conciliar_yapes_sqlite()
    except Exception as exc:
        res_yape = {"ok": False, "error": str(exc)}

    res_sunat_8am = {"ok": True, "status": "SKIPPED"}
    if ES_NIVEL_2_MADRE_HOTEL:
        try:
            res_sunat_8am = _verificar_revisor_incremental_sunat_8am()
        except Exception as exc:
            res_sunat_8am = {"ok": False, "error": str(exc)}

    res_wsp = {"ok": True, "qr_fisico_en_este_nodo": TIENE_QR_WSP_FISICO}
    try:
        wsp.tick_resumen_programado()
        if TIENE_QR_WSP_FISICO:
            wsp.vigilar_daemon()
        if ES_NIVEL_2_MADRE_HOTEL and not _servidor_8080_activo():
            sunat_svc.procesar_cola_ordenes_sunat_madre()
    except Exception as exc:
        res_wsp = {"ok": False, "error": str(exc)}

    git_st = {"ok": True, "modo": "EDITOR_L1"}
    if ES_NIVEL_2_MADRE_HOTEL:
        git_st = actualizar_codigo_git_solo_lectura()

    salud = reporte_salud()
    return {
        "ok": salud["ok"],
        "timestamp": salud["timestamp"],
        "nodo": ROL_NODO,
        "git_solo_lectura": git_st,
        "supabase_push": up.get("enviados", 0),
        "supabase_pull": down.get("aplicados", 0),
        "yape_conciliacion": res_yape.get("ok", False),
        "sunat_revisor_8am": res_sunat_8am,
        "whatsapp_ciclo": res_wsp,
        "salud": salud,
    }


def iniciar_vigilante_24x7(modo_prueba_unica: bool = False) -> None:
    if not ACTIVAR_MOTOR_24X7 and not modo_prueba_unica:
        print(
            f"[motor_24x7] En reposo automático en '{ROL_NODO}' (ACTIVAR_MOTOR_24X7=false). "
            "El vigilante 24/7 solo se mantiene encendido en NIVEL_2_MADRE_HOTEL (Laptop 2 Recepción)."
        )
        return

    print(f"[motor_24x7] Vigilante 24/7 ACTIVO en {ROL_NODO}. Ejecutando ronda integral...")
    res = ejecutar_ronda_integral()
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if modo_prueba_unica:
        return

    ciclo = 0
    while True:
        time.sleep(30)
        ciclo += 1
        try:
            push_to_supabase()
            pull_from_supabase()
            if ES_NIVEL_2_MADRE_HOTEL and not _servidor_8080_activo():
                sunat_svc.procesar_cola_ordenes_sunat_madre()
            wsp.tick_resumen_programado()
            if TIENE_QR_WSP_FISICO:
                wsp.vigilar_daemon()
            if ciclo % 10 == 0 and ES_NIVEL_2_MADRE_HOTEL:  # Cada 5 min revisa si subiste código nuevo a GitHub
                actualizar_codigo_git_solo_lectura()
                publicar_seguidor_carpetas_supabase()
            if ciclo % 60 == 0:  # Cada 30 minutos ronda completa de salud y correo BCP
                est = ejecutar_ronda_integral()
                print(f"[motor_24x7] Ronda 30m -> Salud OK={est['ok']} ({est['timestamp']})")
        except Exception as exc:
            print(f"[motor_24x7] Advertencia en ciclo: {exc}")


if __name__ == "__main__":
    iniciar_vigilante_24x7(modo_prueba_unica="--once" in sys.argv)
