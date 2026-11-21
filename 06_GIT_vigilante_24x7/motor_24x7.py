"""
06_GIT_vigilante_24x7/motor_24x7.py — Vigilante Automático 24/7 (Arquitectura de 3 Niveles)
REGLA ARQUITECTÓNICA:
- Se programa en tu Laptop 1 (NIVEL_1_INGENIERIA) y viaja por GitHub a todos los equipos.
- SOLO se mantiene corriendo 24/7 en la Laptop Madre del Hotel (NIVEL_2_MADRE_HOTEL / ACTIVAR_MOTOR_24X7=true),
  que es el único equipo donde está escaneado el QR físico de WhatsApp de Recepción.
- En Nivel 1 y en Laptops Terciarias (NIVEL_3_TERCIARIA), permanece en reposo automático.
"""
import json
import sys
import time
from pathlib import Path

MOTOR_DIR = Path(__file__).resolve().parent.parent / "04_GIT_app_motor"
if str(MOTOR_DIR) not in sys.path:
    sys.path.insert(0, str(MOTOR_DIR))

from config import ACTIVAR_MOTOR_24X7, ROL_NODO, TIENE_QR_WSP_FISICO
from salud import reporte_salud
from sync_nube import instalar_triggers_outbox, pull_from_supabase, push_to_supabase
import whatsapp as wsp
import yape_bcp as ym


def ejecutar_ronda_integral() -> dict:
    """Ejecuta un ciclo completo de sincronización, conciliación Yape/BCP, WhatsApp y salud."""
    instalar_triggers_outbox()
    up = push_to_supabase()
    down = pull_from_supabase()

    res_yape = {"ok": True}
    try:
        ym.conciliar_yapes_sqlite()
    except Exception as exc:
        res_yape = {"ok": False, "error": str(exc)}

    res_wsp = {"ok": True, "qr_fisico_en_este_nodo": TIENE_QR_WSP_FISICO}
    try:
        wsp.tick_resumen_programado()
        if TIENE_QR_WSP_FISICO:
            wsp.vigilar_daemon()
    except Exception as exc:
        res_wsp = {"ok": False, "error": str(exc)}

    salud = reporte_salud()
    return {
        "ok": salud["ok"],
        "timestamp": salud["timestamp"],
        "nodo": ROL_NODO,
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
            wsp.tick_resumen_programado()
            if TIENE_QR_WSP_FISICO:
                wsp.vigilar_daemon()
            if ciclo % 60 == 0:  # Cada 30 minutos (60 * 30s)
                est = ejecutar_ronda_integral()
                print(f"[motor_24x7] Ronda 30m -> Salud OK={est['ok']} ({est['timestamp']})")
        except Exception as exc:
            print(f"[motor_24x7] Advertencia en ciclo: {exc}")


if __name__ == "__main__":
    iniciar_vigilante_24x7(modo_prueba_unica="--once" in sys.argv)
