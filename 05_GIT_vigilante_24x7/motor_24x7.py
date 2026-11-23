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
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MOTOR_DIR = BASE_DIR / "03_GIT_app_motor"
if str(MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(MOTOR_DIR))

from config import ACTIVAR_MOTOR_24X7, ES_NIVEL_2_MADRE_HOTEL, ROL_NODO, TIENE_QR_WSP_FISICO
from salud import publicar_seguidor_carpetas_supabase, reporte_salud
from sync_nube import instalar_triggers_outbox, pull_from_supabase, push_to_supabase
import sunat as sunat_svc
import whatsapp as wsp
import yape_bcp as ym


def actualizar_codigo_git_solo_lectura() -> dict:
    """
    En Laptop 2 (Recepción) o Laptops Terciarias: descarga en SOLO LECTURA cualquier mejora
    que hayas subido desde tu Laptop 1 con `git push`, sin tocar 01_PRIV_*, 02_DB_* ni .env.
    """
    if not (BASE_DIR / ".git").exists():
        return {"ok": False, "detalle": "Repositorio Git no inicializado"}
    try:
        proc = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "sistema-v2"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        salida = (proc.stdout or proc.stderr or "").strip()
        return {"ok": proc.returncode == 0, "salida": salida[:200]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def ejecutar_ronda_integral() -> dict:
    """Ejecuta un ciclo completo de sincronización, conciliación Yape/BCP, WhatsApp y salud."""
    instalar_triggers_outbox()
    up = push_to_supabase()
    down = pull_from_supabase()

    res_yape = {"ok": True}
    try:
        if ES_NIVEL_2_MADRE_HOTEL:
            ym.sincronizar_transferencias_maelen_correo(forzar=False)
        ym.conciliar_yapes_sqlite()
    except Exception as exc:
        res_yape = {"ok": False, "error": str(exc)}

    res_wsp = {"ok": True, "qr_fisico_en_este_nodo": TIENE_QR_WSP_FISICO}
    try:
        wsp.tick_resumen_programado()
        if TIENE_QR_WSP_FISICO:
            wsp.vigilar_daemon()
        if ES_NIVEL_2_MADRE_HOTEL:
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
            if ES_NIVEL_2_MADRE_HOTEL:
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
