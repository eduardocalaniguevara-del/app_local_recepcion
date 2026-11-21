"""
06_GIT_vigilante_24x7/motor_24x7.py — Vigilante automático 24/7 escalable.
REGLA DE ESCALABILIDAD:
- Tú lo programas en tu laptop principal y lo subes a GitHub (`06_GIT_vigilante_24x7`).
- Se descarga por `git pull` en Laptop 2, Laptop 3 o cualquier equipo nuevo.
- ¿Dónde se enciende 24/7? SOLO en la laptop cuyo archivo `.env` local tenga `ACTIVAR_MOTOR_24X7=true` (por defecto Laptop 2 Recepción).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "04_GIT_app_motor"))
from config import ROL_NODO, ACTIVAR_MOTOR_24X7
from salud import reporte_salud


def iniciar_vigilante_24x7(modo_prueba_unica: bool = False) -> None:
    if not ACTIVAR_MOTOR_24X7 and not modo_prueba_unica:
        print(
            f"[motor_24x7] En reposo automático en '{ROL_NODO}' (ACTIVAR_MOTOR_24X7=false). "
            "Solo corre 24/7 en la laptop designada en .env (ej. LAPTOP_2_RECEPCION)."
        )
        return

    print(f"[motor_24x7] Vigilante 24/7 ACTIVO en {ROL_NODO}. Ejecutando ronda de salud...")
    estado = reporte_salud()
    print(f"[motor_24x7] Salud OK={estado['ok']} | Timestamp={estado['timestamp']}")
    if modo_prueba_unica:
        return

    while True:
        time.sleep(1800)  # Ronda cada 30 minutos
        estado = reporte_salud()
        print(f"[motor_24x7] Ronda 30m -> Salud OK={estado['ok']} ({estado['timestamp']})")


if __name__ == "__main__":
    iniciar_vigilante_24x7(modo_prueba_unica="--once" in sys.argv)
